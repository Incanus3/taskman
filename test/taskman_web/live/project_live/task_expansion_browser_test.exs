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

  test "table edges fit their content before and after expanding the final root" do
    password = "browser-table-edges-password"
    email = "browser-table-edges-#{Ecto.UUID.generate()}@example.com"
    assert {:ok, _user} = Accounts.bootstrap_admin(email, password)
    project = project_fixture(%{name: "Table edges"})
    task_fixture(project, %{title: "Earlier root", status: :pending})
    root = task_fixture(project, %{title: "Final root", status: :pending})
    child = task_fixture(project, %{title: "Final child", status: :pending}, parent: root)

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
        headless: true,
        window_size: [width: 1440, height: 1100]
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
      path = "/projects/#{project.id}?include_children=true"
      Browser.visit(session, base_url <> path)

      wait_for(
        session,
        "return document.querySelector('#project-memory')?.dataset.preferencesHydrated === 'true' && !!document.querySelector('.phx-connected')",
        true
      )

      for width <- [1440, 1024, 390, 312] do
        Browser.resize_window(session, width, 1100)
        assert js(session, "return innerWidth") == width
        assert_table_edges(session, root.id, root.id)
        Browser.click(session, Query.css("#toggle-task-subtasks-#{root.id}"))
        wait_for(session, "return !!document.getElementById('tasks-#{child.id}')", true)
        assert_table_edges(session, root.id, child.id)
        Browser.click(session, Query.css("#toggle-task-subtasks-#{root.id}"))
        wait_for(session, "return !!document.getElementById('tasks-#{child.id}')", false)
      end
    after
      Wallaby.end_session(session)
    end
  end

  test "pointer and keyboard highlight the complete displayed Task family across stream updates" do
    password = "browser-tree-family-password"
    email = "browser-tree-family-#{Ecto.UUID.generate()}@example.com"
    assert {:ok, _user} = Accounts.bootstrap_admin(email, password)
    project = project_fixture(%{name: "Task family highlighting"})
    root = task_fixture(project, %{title: "Zulu root", status: :in_review})
    middle = task_fixture(project, %{title: "Zulu child", status: :pending}, parent: root)
    leaf = task_fixture(project, %{title: "Leaf", status: :pending}, parent: middle)
    sibling = task_fixture(project, %{title: "Alpha child", status: :pending}, parent: root)
    other = task_fixture(project, %{title: "Alpha root", status: :pending})
    other_child = task_fixture(project, %{title: "Other child", status: :pending}, parent: other)
    family = [root.id, middle.id, leaf.id, sibling.id]
    other_family = [other.id, other_child.id]

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {_address, port}} = ThousandIsland.listener_info(server)
    {:ok, _} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(
        binary: System.find_executable("chromium"),
        headless: false,
        window_size: [width: 1440, height: 1100]
      )

    try do
      base_url = "http://127.0.0.1:#{port}"
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
      Browser.visit(session, base_url <> "/projects/#{project.id}")

      wait_for(
        session,
        "return document.querySelector('#project-memory')?.dataset.preferencesHydrated === 'true' && !!document.querySelector('.phx-connected')",
        true
      )

      assert js(session, "return matchMedia('(hover: hover)').matches")
      Browser.click(session, Query.css("#toggle-task-subtasks-#{root.id}"))
      wait_for(session, "return !!document.getElementById('tasks-#{leaf.id}')", true)
      Browser.click(session, Query.css("#toggle-task-subtasks-#{other.id}"))
      wait_for(session, "return !!document.getElementById('tasks-#{other_child.id}')", true)
      Browser.hover(session, Query.css("#location-heading"))
      js(session, "document.activeElement.blur()")

      wait_for(
        session,
        "return !document.querySelector('#tasks article:hover, #tasks article:focus-within')",
        true
      )

      baseline =
        js(session, "return getComputedStyle(document.querySelector('.task-tree-guides')).color")

      Browser.hover(session, Query.css("#open-task-#{leaf.id}"))
      assert_highlighted_family(session, baseline, family)
      Browser.hover(session, Query.css("#move-task-row-button-#{other_child.id}"))
      assert_highlighted_family(session, baseline, other_family)
      Browser.hover(session, Query.css("#location-heading"))
      assert_highlighted_family(session, baseline, [])

      js(session, "document.getElementById(arguments[0]).focus()", ["open-task-#{middle.id}"])
      Browser.send_keys(session, [:tab])
      assert js(session, "return document.activeElement.id") == "add-subtask-#{middle.id}"
      assert_highlighted_family(session, baseline, family)
      Browser.hover(session, Query.css("#open-task-#{other_child.id}"))
      assert_highlighted_family(session, baseline, family ++ other_family)
      Browser.hover(session, Query.css("#location-heading"))
      assert_highlighted_family(session, baseline, family)
      js(session, "document.activeElement.blur()")
      assert_highlighted_family(session, baseline, [])

      # Sorting replaces streamed rows while the pointer remains within the table.
      Browser.hover(session, Query.css("#open-task-#{leaf.id}"))
      assert_highlighted_family(session, baseline, family)
      js(session, "document.getElementById('sort-task-title').click()")

      wait_for(
        session,
        "return document.getElementById('task-title-header').getAttribute('aria-sort')",
        "ascending"
      )

      assert js(
               session,
               "return Array.from(document.querySelectorAll('#tasks > article')).map(row => row.id)"
             ) == Enum.map([other, other_child, root, sibling, middle, leaf], &"tasks-#{&1.id}")

      assert_highlighted_family(session, baseline, family)
      Browser.hover(session, Query.css("#open-task-#{middle.id}"))
      assert_highlighted_family(session, baseline, family)
      Browser.click(session, Query.css("#toggle-task-subtasks-#{root.id}"))
      wait_for(session, "return !!document.getElementById('tasks-#{leaf.id}')", false)
      Browser.hover(session, Query.css("#location-heading"))
      js(session, "document.activeElement.blur()")
      assert_highlighted_family(session, baseline, [])
      Browser.click(session, Query.css("#toggle-task-subtasks-#{root.id}"))
      wait_for(session, "return !!document.getElementById('tasks-#{leaf.id}')", true)
      Browser.hover(session, Query.css("#open-task-#{leaf.id}"))
      js(session, "document.activeElement.blur()")
      assert_highlighted_family(session, baseline, family)

      Browser.click(session, Query.css("#task-status-filter-button"))
      Browser.click(session, Query.css("#task-status-filter-option-in_review"))
      wait_for(session, "return !!document.getElementById('tasks-#{root.id}')", false)

      wait_for(
        session,
        "return document.getElementById('tasks-#{middle.id}')?.dataset.taskDepth",
        "0"
      )

      assert js(
               session,
               "return document.getElementById('tasks-#{sibling.id}').dataset.taskDepth"
             ) == "0"

      Browser.hover(session, Query.css("#open-task-#{leaf.id}"))
      assert_highlighted_family(session, baseline, [middle.id, leaf.id])
    after
      Wallaby.end_session(session)
    end
  end

  defp assert_highlighted_family(session, baseline, expected_ids) do
    wait_for(
      session,
      """
      return Array.from(document.querySelectorAll('#tasks > article'))
        .filter(row => {
          const guide = row.querySelector('.task-tree-guides');
          return guide && getComputedStyle(guide).color !== #{Jason.encode!(baseline)};
        })
        .map(row => Number(row.id.replace('tasks-', ''))).sort((a, b) => a - b);
      """,
      Enum.sort(expected_ids)
    )
  end

  defp assert_table_edges(session, root_id, last_id) do
    state =
      js(
        session,
        ~S"""
        const table = document.getElementById('task-list-scroll');
        const style = getComputedStyle(table);
        const last = document.querySelector('#tasks > article:last-of-type');
        const border = getComputedStyle(last);
        const separator = getComputedStyle(document.getElementById('tasks-' + arguments[0]), '::after');
        const reserved = table.offsetWidth - table.clientWidth - parseFloat(style.borderLeftWidth) - parseFloat(style.borderRightWidth);
        return {
          overflowing: table.scrollHeight > table.clientHeight,
          unusedGutter: reserved > 1,
          strongFinalBorder: last.id === 'tasks-' + arguments[1] &&
            parseFloat(border.borderBottomWidth) >= 1 && border.borderBottomColor === separator.backgroundColor
        };
        """,
        [root_id, last_id]
      )

    assert state == %{
             "overflowing" => false,
             "unusedGutter" => false,
             "strongFinalBorder" => true
           }
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
