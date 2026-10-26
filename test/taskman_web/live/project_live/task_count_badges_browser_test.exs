defmodule TaskmanWeb.ProjectLive.TaskCountBadgesBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts
  alias Wallaby.{Browser, Query}

  @moduletag :browser

  setup do
    password = "browser-count-password"
    email = "browser-count-#{Ecto.UUID.generate()}@example.com"
    assert {:ok, _user} = Accounts.bootstrap_admin(email, password)
    project = project_fixture(%{name: "A Project with a deliberately long name for wrapping"})
    parent = list_fixture(project, %{name: "A long parent List name that needs truncation"})

    child =
      list_fixture(project, parent, %{name: "A long nested List name that needs truncation"})

    for _ <- 1..12, do: task_fixture(project, %{status: :pending})
    task_fixture(project, %{status: :done})
    for _ <- 1..12, do: task_fixture(project, child, %{status: :pending})

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {_address, port}} = ThousandIsland.listener_info(server)
    base_url = "http://127.0.0.1:#{port}"
    {:ok, _} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(
        binary: System.find_executable("chromium"),
        # Headed Chromium exposes real desktop hover capability; the test verifies it.
        headless: false,
        window_size: [width: 1440, height: 900]
      )

    on_exit(fn -> Wallaby.end_session(session) end)
    Browser.visit(session, base_url <> "/sign-in")

    wait_for(
      session,
      "sign-in connected",
      "return !!document.querySelector('.phx-connected')",
      true
    )

    js(
      session,
      ~S"""
      document.querySelector('input[name="user[email]"]').value = arguments[0];
      document.querySelector('input[name="user[password]"]').value = arguments[1];
      document.querySelector('form[action="/auth/user/password/sign_in"]').submit();
      """,
      [email, password]
    )

    wait_for(session, "signed in", "return location.pathname", "/")
    url = "#{base_url}/projects/#{project.id}"
    Browser.visit(session, url)
    hydrated(session)
    %{session: session, project: project, parent: parent, child: child, url: url}
  end

  test "sidebar badges swap with reachable actions on hover and keyboard focus", %{
    session: session,
    project: project,
    parent: parent
  } do
    assert js(session, "return matchMedia('(hover: hover)').matches")

    for {link, badge, actions} <- [
          {"project-tasks-link", "project-tasks-count", ["add-root-list-#{project.id}"]},
          {"select-list-#{parent.id}", "list-task-count-#{parent.id}",
           ["rename-list-#{parent.id}", "add-child-list-#{parent.id}"]}
        ] do
      Browser.hover(session, Query.css("#location-heading"))
      js(session, "document.activeElement.blur()")
      assert visible?(session, badge)
      for action <- actions, do: refute(visible?(session, action))

      assert js(
               session,
               ~S"""
               const badge = document.getElementById(arguments[0]);
               const bounds = badge.getBoundingClientRect();
               return badge.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2));
               """,
               [badge]
             )

      Browser.hover(session, Query.css("##{link}"))
      wait_for(session, "badge hides on hover", visibility_script(badge), false)
      for action <- actions, do: assert(visible?(session, action))
      assert visible?(session, "location-task-count")

      Browser.hover(session, Query.css("#location-heading"))
      wait_for(session, "badge returns after hover", visibility_script(badge), true)
      js(session, "document.getElementById(arguments[0]).focus()", [link])
      wait_for(session, "badge hides on focus", visibility_script(badge), false)
      assert js(session, "return document.activeElement.id") == link

      for action <- actions do
        Browser.send_keys(session, [:tab])
        assert js(session, "return document.activeElement.id") == action
        assert visible?(session, action)
        assert js(session, "return getComputedStyle(document.activeElement).boxShadow !== 'none'")
      end
    end
  end

  test "browser hydration preserves defaults and saved status choices in both count surfaces", %{
    session: session,
    url: url
  } do
    assert_counts(session, "12", "12 / 12")
    assert_selected_statuses(session, ["icebox", "pending", "in_progress", "in_review"])

    for storage <- [:malformed, :done, :empty] do
      js(session, "localStorage.clear()")

      case storage do
        :malformed ->
          js(
            session,
            "localStorage.setItem('taskman.task-table.visible-statuses', 'invalid-json')"
          )

        :done ->
          js(session, ~s|localStorage.setItem('taskman.task-table.visible-statuses', '["done"]')|)

        :empty ->
          js(session, "localStorage.setItem('taskman.task-table.visible-statuses', '[]')")
      end

      Browser.visit(session, url)
      hydrated(session)

      case storage do
        :malformed ->
          assert_counts(session, "12", "12 / 12")
          assert_selected_statuses(session, ["icebox", "pending", "in_progress", "in_review"])

        :done ->
          assert_counts(session, "1", "1 / 13")
          assert_selected_statuses(session, ["done"])

        :empty ->
          assert_counts(session, "0", "0 / 12")
          assert_selected_statuses(session, [])
      end
    end

    # Deny preference storage without also disabling Phoenix's sessionStorage history.
    cdp(session, "Page.addScriptToEvaluateOnNewDocument", %{
      source: ~S"""
      for (const method of ['getItem', 'setItem']) {
        const original = Storage.prototype[method];
        Storage.prototype[method] = function (...args) {
          if (this === window.localStorage) throw new Error('localStorage denied');
          return original.apply(this, args);
        };
      }
      """
    })

    Browser.visit(session, url)
    hydrated(session)
    assert_counts(session, "12", "12 / 12")
    assert_selected_statuses(session, ["icebox", "pending", "in_progress", "in_review"])
  end

  defp assert_counts(session, sidebar, header) do
    wait_for(
      session,
      "matching sidebar count",
      "return document.querySelector('#project-tasks-count-value')?.textContent.trim()",
      sidebar
    )

    wait_for(
      session,
      "matching header count",
      "return document.querySelector('#location-task-count-value')?.textContent.trim().replace(/\\s+/g, ' ')",
      header
    )
  end

  defp assert_selected_statuses(session, expected) do
    Browser.click(session, Query.css("#task-status-filter-button"))

    assert js(
             session,
             "return Array.from(document.querySelectorAll('[id^=task-status-filter-option-]:checked')).map(el => el.id.replace('task-status-filter-option-', ''))"
           ) == expected

    Browser.click(session, Query.css("#task-status-filter-button"))
  end

  defp hydrated(session),
    do:
      wait_for(
        session,
        "preferences hydrated",
        "return document.querySelector('#project-memory')?.dataset.preferencesHydrated === 'true' && !!document.querySelector('.phx-connected')",
        true
      )

  defp visibility_script(id),
    do: """
    const el = document.getElementById(#{Jason.encode!(id)});
    return !!el && el.getBoundingClientRect().width > 0 && Array.from((function*(node) { while(node) { yield node; node = node.parentElement } })(el)).every(node => { const style = getComputedStyle(node); return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0 });
    """

  defp visible?(session, id), do: js(session, visibility_script(id))

  defp cdp(session, command, params) do
    response =
      Req.post!(session.url <> "/goog/cdp/execute",
        json: %{cmd: command, params: params},
        retry: false
      )

    assert response.status == 200, inspect(response.body)
    response.body["value"]
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

  defp wait_for(session, label, script, expected),
    do: wait_for(session, label, script, expected, System.monotonic_time(:millisecond) + 12_000)

  defp wait_for(session, label, script, expected, deadline) do
    actual = js(session, script)

    if actual == expected do
      actual
    else
      if System.monotonic_time(:millisecond) >= deadline,
        do: flunk("#{label}: expected #{inspect(expected)}, got #{inspect(actual)}")

      receive do
      after
        50 -> wait_for(session, label, script, expected, deadline)
      end
    end
  end
end
