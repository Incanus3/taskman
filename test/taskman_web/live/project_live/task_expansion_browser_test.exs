defmodule TaskmanWeb.ProjectLive.TaskExpansionBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts
  alias Wallaby.{Browser, Query}

  @moduletag :browser

  test "root disclosure is visible and activates independently of the row link" do
    password = "browser-expansion-password"
    email = "browser-expansion-#{Ecto.UUID.generate()}@example.com"
    assert {:ok, _user} = Accounts.bootstrap_admin(email, password)
    project = project_fixture(%{name: "Disclosure acceptance"})
    root = task_fixture(project, %{title: "Root Task", status: :pending})
    child = task_fixture(project, %{title: "Child Task", status: :pending}, parent: root)

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
        headless: false,
        window_size: [width: 1440, height: 900]
      )

    try do
      Browser.visit(session, base_url <> "/sign-in")
      wait_for(session, "return !!document.querySelector('.phx-connected')", true)

      js(
        session,
        ~S"""
        document.querySelector('input[name="user[email]"]').value = arguments[0];
        document.querySelector('input[name="user[password]"]').value = arguments[1];
        document.querySelector('form[action="/auth/user/password/sign_in"]').submit();
        """,
        [email, password]
      )

      wait_for(session, "return location.pathname", "/")
      path = "/projects/#{project.id}"
      Browser.visit(session, base_url <> path)

      wait_for(
        session,
        "return document.querySelector('#project-memory')?.dataset.preferencesHydrated === 'true' && !!document.querySelector('.phx-connected')",
        true
      )

      assert js(session, "return matchMedia('(hover: hover)').matches")
      Browser.hover(session, Query.css("#location-heading"))
      js(session, "document.activeElement.blur()")
      button = "toggle-task-subtasks-#{root.id}"
      assert visible?(session, "##{button} .hero-chevron-right")
      assert visible?(session, "#task-number-#{root.id}")

      Browser.click(session, Query.css("##{button}"))
      assert_expansion(session, button, child.id, true, path)

      # Enter the disclosure through the native tab order from the preceding row link.
      js(session, "document.getElementById(arguments[0]).focus()", ["open-task-#{root.id}"])
      Browser.send_keys(session, [:tab])
      assert js(session, "return document.activeElement.id") == button
      Browser.send_keys(session, [:enter])
      assert_expansion(session, button, child.id, false, path)
      js(session, "document.getElementById(arguments[0]).focus()", ["open-task-#{root.id}"])
      Browser.send_keys(session, [:tab])
      assert js(session, "return document.activeElement.id") == button
      Browser.send_keys(session, [:space])
      assert_expansion(session, button, child.id, true, path)
    after
      Wallaby.end_session(session)
    end
  end

  defp assert_expansion(session, button, child_id, expanded?, path) do
    wait_for(
      session,
      "return document.getElementById(#{Jason.encode!(button)})?.getAttribute('aria-expanded')",
      to_string(expanded?)
    )

    assert js(session, "return !!document.getElementById('tasks-' + arguments[0])", [child_id]) ==
             expanded?

    assert js(session, "return location.pathname") == path
    assert js(session, "return document.querySelector('#task-modal-content') === null")
  end

  defp visible?(session, selector) do
    js(
      session,
      ~S"""
      const el = document.querySelector(arguments[0]);
      if (!el) return false;
      const bounds = el.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0) return false;
      for (let node = el; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
      }
      return true;
      """,
      [selector]
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

  defp wait_for(session, script, expected),
    do: wait_for(session, script, expected, System.monotonic_time(:millisecond) + 12_000)

  defp wait_for(session, script, expected, deadline) do
    actual = js(session, script)

    if actual != expected do
      if System.monotonic_time(:millisecond) >= deadline,
        do: flunk("expected #{inspect(expected)}, got #{inspect(actual)} for #{script}")

      receive do
      after
        50 -> wait_for(session, script, expected, deadline)
      end
    end
  end
end
