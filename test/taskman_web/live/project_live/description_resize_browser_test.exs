defmodule TaskmanWeb.ProjectLive.DescriptionResizeBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Accounts, Tasks}
  alias Wallaby.{Browser, Query}

  @moduletag :browser
  @visual "[data-editor-visual]"
  @source "[data-markdown-wrapper] textarea"

  setup do
    email = "resize-browser-#{Ecto.UUID.generate()}@example.com"
    {:ok, _} = Accounts.bootstrap_admin(email, "browser-test-password")
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Resizable description", description: "First line"})
    {session, base} = browser_session()
    on_exit(fn -> Wallaby.end_session(session) end)
    sign_in(session, base, email)
    Browser.visit(session, base <> "/projects/#{project.id}/tasks/#{task.id}")
    Browser.find(session, Query.css("[data-leaf-mount-state='ready']"))
    %{session: session, project: project, task: task}
  end

  test "native grips resize visual and source editors, retain size during edits and fit on double click",
       c do
    for {mode, selector} <- [{"hybrid", @visual}, {"markdown", @source}] do
      if mode == "markdown", do: mode(c.session, mode)
      metrics = metrics(c.session, selector)
      assert metrics["resize"] == "vertical"
      assert metrics["lines"] >= 8 && metrics["lines"] <= 11

      drag_grip(c.session, selector, 100)
      resized = metrics(c.session, selector)["height"]
      assert resized >= metrics["height"] + 80
      Browser.click(c.session, Query.css(selector))
      Browser.send_keys(c.session, [:end, "!"])

      title = "Updated #{mode} title"
      current = Tasks.get_task_for_project(c.project, c.task.id)

      {:ok, _} =
        Tasks.update_task(c.project, current, %{title: title},
          actor: Taskman.AccountsFixtures.user_fixture()
        )

      Browser.find(c.session, Query.css("#task-title[value='#{title}']"))
      assert metrics(c.session, selector)["height"] == resized

      double_click_grip(c.session, selector)
      fitted = metrics(c.session, selector)["height"]
      assert fitted < resized
      assert fitted >= metrics["height"]
    end

    mode(c.session, "hybrid")
    assert metrics(c.session, @visual)["resize"] == "vertical"

    assert js(c.session, "return document.querySelector('[data-editor-visual]').textContent") =~
             "!"
  end

  defp browser_session do
    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    {:ok, {_, port}} = ThousandIsland.listener_info(server)
    {:ok, _} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(
        binary: System.find_executable("chromium"),
        headless: true,
        window_size: [1440, 900]
      )

    {session, "http://127.0.0.1:#{port}"}
  end

  defp sign_in(session, base, email) do
    Browser.visit(session, base <> "/sign-in")
    Browser.find(session, Query.css("input[name='user[email]']"))

    js(
      session,
      """
      document.querySelector('input[name="user[email]"]').value = arguments[0];
      document.querySelector('input[name="user[password]"]').value = 'browser-test-password';
      document.querySelector('form[action="/auth/user/password/sign_in"]').submit();
      """,
      [email]
    )

    Browser.find(session, Query.css("#main-panel"))
  end

  defp mode(session, mode) do
    inline? =
      js(
        session,
        """
        const el = document.querySelector('[data-mode-switcher=inline] [data-mode-tab=' + arguments[0] + ']');
        return !!el && el.checkVisibility({checkVisibilityCSS: true});
        """,
        [mode]
      )

    if inline? do
      Browser.click(session, Query.css("[data-mode-switcher='inline'] [data-mode-tab='#{mode}']"))
    else
      Browser.click(session, Query.css("summary[title='More editor options']"))
      Browser.click(session, Query.css("[data-mode-tab='#{mode}']"))
    end
  end

  defp metrics(session, selector) do
    js(
      session,
      """
      const el = document.querySelector(arguments[0]), css = getComputedStyle(el);
      return {height: el.getBoundingClientRect().height, resize: css.resize,
        lines: (el.clientHeight - parseFloat(css.paddingTop) - parseFloat(css.paddingBottom)) / parseFloat(css.lineHeight)};
      """,
      [selector]
    )
  end

  defp grip(session, selector) do
    js(
      session,
      """
      const el = document.querySelector(arguments[0]);
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      const x = Math.floor(r.right - 7), y = Math.floor(r.bottom - 7);
      return {x, y, reachable: el.contains(document.elementFromPoint(x, y))};
      """,
      [selector]
    )
  end

  defp drag_grip(session, selector, dy) do
    %{"x" => x, "y" => y, "reachable" => true} = grip(session, selector)

    pointer(session, [
      move(x, y),
      %{type: "pointerDown", button: 0},
      %{type: "pointerMove", origin: "viewport", x: x, y: y + dy, duration: 250},
      %{type: "pointerUp", button: 0}
    ])
  end

  defp double_click_grip(session, selector) do
    %{"x" => x, "y" => y, "reachable" => true} = grip(session, selector)
    click = [%{type: "pointerDown", button: 0}, %{type: "pointerUp", button: 0}]
    pointer(session, [move(x, y)] ++ click ++ [%{type: "pause", duration: 100}] ++ click)
  end

  defp move(x, y), do: %{type: "pointerMove", origin: "viewport", x: x, y: y, duration: 0}

  defp pointer(session, actions) do
    assert %{status: 200, body: %{"value" => nil}} =
             Req.post!(session.session_url <> "/actions",
               json: %{
                 actions: [
                   %{
                     type: "pointer",
                     id: "resize-mouse",
                     parameters: %{pointerType: "mouse"},
                     actions: actions
                   }
                 ]
               }
             )
  end

  defp js(session, script, args \\ []) do
    ref = make_ref()
    owner = self()
    Browser.execute_script(session, script, args, fn result -> send(owner, {ref, result}) end)

    receive do
      {^ref, result} -> result
    after
      5_000 -> flunk("browser script did not return")
    end
  end
end
