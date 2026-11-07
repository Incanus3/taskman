defmodule TaskmanWeb.ProjectLive.DescriptionBlockMenuBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Accounts, Tasks}
  alias Wallaby.{Browser, Query}

  @moduletag :browser
  @source "Keep first\n\nKeep second\n\nKeep third\n\nKeep fourth\n\nKeep fifth\n\nMenu target"

  setup do
    email = "block-menu-#{Ecto.UUID.generate()}@example.com"
    {:ok, _} = Accounts.bootstrap_admin(email, "browser-test-password")
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Paragraph menu", description: @source})
    {session, base} = browser_session()
    on_exit(fn -> Wallaby.end_session(session) end)
    sign_in(session, base, email)
    %{session: session, base: base, project: project, task: task}
  end

  for {width, height} <- [{1440, 900}, {320, 720}], action <- ["Duplicate", "Delete"] do
    test "#{action} escapes a short editor and saves the selected paragraph at #{width} CSS pixels",
         c do
      resize(c.session, unquote(width), unquote(height))
      Browser.visit(c.session, c.base <> "/projects/#{c.project.id}")
      Browser.click(c.session, Query.css("#open-task-#{c.task.id}"))
      Browser.find(c.session, Query.css("[data-leaf-mount-state='ready']"))

      if js(c.session, "return document.querySelector('#task-detail-content').inert"),
        do: Browser.click(c.session, Query.css("#task-hierarchy-toggle"))

      assert js(
               c.session,
               "return {width:innerWidth,height:innerHeight,scroll:document.documentElement.scrollWidth}"
             ) ==
               %{
                 "width" => unquote(width),
                 "height" => unquote(height),
                 "scroll" => unquote(width)
               }

      assert js(
               c.session,
               "return document.querySelector('[data-editor-visual]').getBoundingClientRect().height"
             ) == 212

      open_menu(c.session)
      assert_last_action_reachable(c.session)
      assert_no_horizontal_overflow(c.session)
      click_action(c.session, unquote(action))
      count = if unquote(action) == "Duplicate", do: 7, else: 5
      Browser.find(c.session, Query.css("[data-editor-visual] > p", count: count))
      close(c.session)

      expected =
        if unquote(action) == "Duplicate",
          do: @source <> "\n\nMenu target",
          else: "Keep first\n\nKeep second\n\nKeep third\n\nKeep fourth\n\nKeep fifth"

      assert Tasks.get_task_for_project(c.project, c.task.id).description == expected
    end
  end

  for mode <- [:detail, :new] do
    test "the #{mode} Task paragraph menu stays open until a genuine backdrop click", c do
      Browser.visit(c.session, c.base <> "/projects/#{c.project.id}")
      trigger = if unquote(mode) == :detail, do: "#open-task-#{c.task.id}", else: "#add-task"
      Browser.click(c.session, Query.css(trigger))
      Browser.find(c.session, Query.css("[data-leaf-mount-state='ready']"))

      js(
        c.session,
        "document.querySelector('[data-editor-visual] > p:last-child').scrollIntoView({block:'center'})"
      )

      settle(c.session)
      click_handle(c.session)

      assert js(
               c.session,
               "return !!document.querySelector('#task-modal-content') && !!document.querySelector('.leaf-block-menu')"
             )

      assert js(
               c.session,
               "const target=document.elementFromPoint(4,4); return !!target.closest('#task-modal') && !target.closest('#task-modal-content')"
             )

      click_at(c.session, %{"x" => 4, "y" => 4})
      Browser.find(c.session, Query.css("#task-modal", count: 0))
      assert js(c.session, "return location.pathname") == "/projects/#{c.project.id}"
      assert Tasks.get_task_for_project(c.project, c.task.id).description == @source
    end
  end

  defp open_menu(session) do
    js(
      session,
      "document.querySelector('[data-editor-visual] > p:last-child').scrollIntoView({block:'end'})"
    )

    settle(session)

    bottom =
      js(session, """
      const p=document.querySelector('[data-editor-visual] > p:last-child').getBoundingClientRect();
      const e=document.querySelector('[data-editor-visual]').getBoundingClientRect();
      return {paragraph:p.bottom,editor:e.bottom};
      """)

    assert bottom["paragraph"] >= bottom["editor"] - 72, inspect(bottom)

    click_handle(session)

    assert js(
             session,
             "return document.querySelector('.leaf-block-menu').getBoundingClientRect().bottom > document.querySelector('[data-editor-visual]').getBoundingClientRect().bottom"
           )
  end

  defp click_handle(session) do
    assert reachable?(session, "[data-editor-visual] > p:last-child")
    point = center(session, "[data-editor-visual] > p:last-child")
    pointer(session, [move(point)])
    Browser.find(session, Query.css("[data-drag-handle]"))
    assert reachable?(session, "[data-drag-handle]")
    click_at(session, center(session, "[data-drag-handle]"))
    Browser.find(session, Query.css(".leaf-block-menu", visible: :any))
  end

  defp close(session) do
    Browser.click(session, Query.css("#task-modal-close"))
    Browser.find(session, Query.css("#task-modal", count: 0))
  end

  defp assert_last_action_reachable(session) do
    for _ <- 1..4, !reachable?(session, ".leaf-block-menu button:last-child") do
      wheel(session)
      settle(session)
    end

    assert reachable?(session, ".leaf-block-menu button:last-child"),
           "last paragraph action is clipped: #{inspect(menu_metrics(session))}"
  end

  defp click_action(session, text) do
    selector =
      if text == "Delete",
        do: ".leaf-block-menu button:last-child",
        else: ".leaf-block-menu button:nth-last-child(2)"

    assert js(session, "return document.querySelector(arguments[0]).textContent", [selector]) ==
             text

    assert reachable?(session, selector)
    click_at(session, center(session, selector))
    Browser.find(session, Query.css(".leaf-block-menu", count: 0))
  end

  defp assert_no_horizontal_overflow(session) do
    metrics =
      js(session, """
      return ['#task-detail-main', '#task-detail-content'].map(selector => {
        const el = document.querySelector(selector);
        return {selector, client: el.clientWidth, scroll: el.scrollWidth};
      });
      """)

    assert Enum.all?(metrics, &(&1["scroll"] <= &1["client"])), inspect(metrics)
  end

  defp reachable?(session, selector) do
    js(
      session,
      """
      const e = document.querySelector(arguments[0]), r = e.getBoundingClientRect();
      const x = r.x+r.width/2, y = r.y+r.height/2;
      return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight && e.contains(document.elementFromPoint(x,y));
      """,
      [selector]
    )
  end

  defp center(session, selector) do
    js(
      session,
      "const r=document.querySelector(arguments[0]).getBoundingClientRect(); return {x:Math.floor(r.x+r.width/2),y:Math.floor(r.y+r.height/2)}",
      [selector]
    )
  end

  defp menu_metrics(session) do
    js(session, """
    const rect = e => {const r=e.getBoundingClientRect(); return {top:r.top,bottom:r.bottom,left:r.left,right:r.right}};
    return {button:rect(document.querySelector('.leaf-block-menu button:last-child')),editor:rect(document.querySelector('[data-editor-visual]')),main:rect(document.querySelector('#task-detail-main')),bodyOverflow:getComputedStyle(document.querySelector('[data-leaf-body-wrapper]')).overflow,viewport:innerHeight};
    """)
  end

  defp wheel(session) do
    %{"x" => x, "y" => y} = center(session, ".leaf-block-menu")
    y = min(y, js(session, "return innerHeight - 100"))

    actions(session, %{
      type: "wheel",
      id: "paragraph-scroll",
      actions: [
        %{type: "scroll", origin: "viewport", x: x, y: y, deltaX: 0, deltaY: 220, duration: 200}
      ]
    })
  end

  defp click_at(session, point),
    do:
      pointer(session, [
        move(point),
        %{type: "pointerDown", button: 0},
        %{type: "pause", duration: 150},
        %{type: "pointerUp", button: 0}
      ])

  defp move(%{"x" => x, "y" => y}),
    do: %{type: "pointerMove", origin: "viewport", x: x, y: y, duration: 0}

  defp pointer(session, events),
    do:
      actions(session, %{
        type: "pointer",
        id: "paragraph-mouse",
        parameters: %{pointerType: "mouse"},
        actions: events
      })

  defp actions(session, source) do
    assert %{status: 200, body: %{"value" => nil}} =
             Req.post!(session.session_url <> "/actions", json: %{actions: [source]})
  end

  defp resize(session, width, height) do
    chrome = js(session, "return outerHeight-innerHeight")
    Browser.resize_window(session, width, height + chrome)
  end

  defp settle(session) do
    js(
      session,
      "window.blockMenuSettled=false; requestAnimationFrame(()=>requestAnimationFrame(()=>window.blockMenuSettled=true))"
    )

    wait(session, "return window.blockMenuSettled", System.monotonic_time(:millisecond) + 5_000)
  end

  defp wait(session, script, deadline) do
    if !js(session, script) do
      assert System.monotonic_time(:millisecond) < deadline
      wait(session, script, deadline)
    end
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
      document.querySelector('input[name="user[email]"]').value=arguments[0];
      document.querySelector('input[name="user[password]"]').value='browser-test-password';
      document.querySelector('form[action="/auth/user/password/sign_in"]').submit();
      """,
      [email]
    )

    Browser.find(session, Query.css("#main-panel"))
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
