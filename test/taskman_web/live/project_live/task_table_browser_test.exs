defmodule TaskmanWeb.ProjectLive.TaskTableBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts
  alias Wallaby.{Browser, Query}

  @moduletag :browser

  @state_js ~S"""
  const stored = key => {
    try { return localStorage.getItem(key) } catch (_error) { return null }
  };
  return {
    url: location.href,
    pressed: document.querySelector('#include-child-lists')?.getAttribute('aria-pressed'),
    include: stored('taskman.task-table.include-children'),
    statuses: stored('taskman.task-table.visible-statuses'),
    memory: stored('taskman:selected-project-id:v1'),
    panel: document.querySelector('#main-panel')?.dataset.state,
    hydrated: document.querySelector('#project-memory')?.dataset.preferencesHydrated,
    projectHref: document.querySelector('#project-tasks-link')?.getAttribute('href'),
    connected: document.querySelector('[data-phx-main]')?.classList.contains('phx-connected') === true,
    taskVisible: !!document.querySelector('#task-' + arguments[0])
  }
  """

  @share_js ~S"""
  const modalButton = document.querySelector('#task-modal-share-task-view');
  const button = modalButton || document.querySelector('#share-task-view');
  const input = button && document.getElementById(button.id + '-url');
  const feedback = button && document.getElementById(button.id + '-feedback');
  const toast = button && document.getElementById(button.id + '-toast');
  const move = document.querySelector('#move-task-detail-button-' + arguments[0]);
  const modal = document.querySelector('#task-modal-content');
  const feedbackBounds = feedback?.getBoundingClientRect();
  const inputBounds = input?.getBoundingClientRect();
  const modalBounds = modal?.getBoundingClientRect();
  const bounds = button?.getBoundingClientRect();
  const hit = bounds && document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2);
  return {
    url: location.href,
    ready: button && !button.disabled,
    viewportWidth: window.innerWidth,
    modalShare: !!modalButton,
    leftOfMove: !!(modalButton && move &&
      modalButton.getBoundingClientRect().right <= move.getBoundingClientRect().left),
    matchesMoveHeight: !!(modalButton && move &&
      Math.abs(modalButton.getBoundingClientRect().height - move.getBoundingClientRect().height) < 1 &&
      Math.abs(modalButton.getBoundingClientRect().width - modalButton.getBoundingClientRect().height) < 1),
    toastVisible: !!(toast && !toast.hidden),
    feedbackVisible: !!(feedback && !feedback.hidden),
    include: button?.dataset.includeChildren,
    statuses: button?.dataset.statuses,
    copied: window.__copiedShare,
    status: button && document.getElementById(button.id + '-status')?.textContent.trim(),
    fallback: input && !input.hidden ? input.value : null,
    fallbackVisible: !modal || !feedbackBounds || !modalBounds ||
      (feedbackBounds.left >= modalBounds.left && feedbackBounds.right <= modalBounds.right),
    fallbackInputWithinBounds: !!(inputBounds && inputBounds.width > 0 &&
      inputBounds.left >= 0 && inputBounds.right <= window.innerWidth &&
      inputBounds.top >= 0 && inputBounds.bottom <= window.innerHeight &&
      (!modalBounds || (inputBounds.left >= modalBounds.left && inputBounds.right <= modalBounds.right &&
        inputBounds.top >= modalBounds.top && inputBounds.bottom <= modalBounds.bottom))),
    focus: document.activeElement?.id,
    pointerReachable: !!(button && button.contains(hit))
  };
  """

  test "browser-held filters, sharing, and Project memory survive navigation" do
    password = "browser-test-password"
    email = "browser-#{System.unique_integer([:positive])}@example.com"
    assert {:ok, _user} = Accounts.bootstrap_admin(email, password)

    project = project_fixture(%{})
    task_list = list_fixture(project)
    task = task_fixture(project, task_list, %{status: :done})
    list_fixture(project, task_list)
    folder = list_fixture(project)
    list_fixture(project, folder)

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {_address, port}} = ThousandIsland.listener_info(server)
    base_url = "http://127.0.0.1:#{port}"
    {:ok, _started} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(
        binary: System.find_executable("chromium"),
        window_size: [width: 1200, height: 800]
      )

    try do
      sign_in(session, base_url, email, password, task.id)
      check_identity(session, base_url, project.id, folder.id, task.id)
      check_share(session, base_url, project.id, task_list.id, task.id)
      check_filter_history(session, base_url, project.id, task.id)
      check_project_memory(session, base_url, project.id, task_list.id, task.id)
    after
      Wallaby.end_session(session)
    end
  end

  test "workspace keeps navigation and Task controls visible while Lists and Tasks scroll" do
    password = "browser-test-password"
    email = "browser-layout-#{System.unique_integer([:positive])}@example.com"
    assert {:ok, _user} = Accounts.bootstrap_admin(email, password)

    project = project_fixture(%{})

    for index <- 1..26 do
      list_fixture(project, %{name: "List #{index}"})
    end

    tasks =
      for index <- 1..26 do
        task_fixture(project, %{title: "Task #{index}", status: :pending})
      end

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {_address, port}} = ThousandIsland.listener_info(server)
    base_url = "http://127.0.0.1:#{port}"
    {:ok, _started} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(
        binary: System.find_executable("chromium"),
        window_size: [width: 1024, height: 700]
      )

    try do
      sign_in(session, base_url, email, password, hd(tasks).id)
      Browser.visit(session, "#{base_url}/projects/#{project.id}")

      wait_for_js(
        session,
        "overflowing workspace ready",
        "return document.querySelectorAll('#tasks article').length === 26 && document.querySelectorAll('#workspace-tree [role=treeitem]').length === 26",
        true
      )

      before = layout_state(session)
      assert before["contentOverflow"] == false
      assert before["taskScrollRange"] > 0
      assert before["treeScrollRange"] > 0
      assert before["newProjectVisible"]
      assert before["controlsVisible"]
      assert before["tableHeaderVisible"]

      js(session, ~S"""
      document.querySelector('#task-list-scroll').scrollTop = 10_000;
      document.querySelector('#workspace-tree').scrollTop = 10_000;
      """)

      after_scroll = layout_state(session)
      assert after_scroll["taskScrollTop"] > 0
      assert after_scroll["treeScrollTop"] > 0
      assert after_scroll["contentScrollTop"] == 0
      assert after_scroll["newProjectTop"] == before["newProjectTop"]
      assert after_scroll["controlsTop"] == before["controlsTop"]
      assert after_scroll["tableHeaderTop"] == before["tableHeaderTop"]

      Browser.resize_window(session, 390, 700)

      wait_for_js(
        session,
        "narrow workspace ready",
        "return window.innerWidth < 1024 && !!document.querySelector('#project-sidebar-toggle')",
        true
      )

      narrow = mobile_layout_state(session)
      assert narrow["sidebarHidden"]
      assert narrow["mainFillsWidth"]
      assert narrow["contentOverflow"] == false
      assert narrow["taskScrollRange"] > 0
      assert narrow["controlsVisible"]
      assert narrow["toolbarFits"]
      assert narrow["mainFitsWidth"]

      click(session, "#project-sidebar-toggle")

      wait_for_js(
        session,
        "Project drawer opens",
        "return document.querySelector('#project-sidebar-toggle')?.getAttribute('aria-expanded')",
        "true"
      )

      open_drawer = mobile_layout_state(session)
      refute open_drawer["sidebarHidden"]
      assert open_drawer["footerVisible"]
      assert open_drawer["treeScrollRange"] > 0

      js(session, "document.querySelector('#workspace-tree').scrollTop = 10_000")
      scrolled_drawer = mobile_layout_state(session)
      assert scrolled_drawer["treeScrollTop"] > 0
      assert scrolled_drawer["footerTop"] == open_drawer["footerTop"]

      Browser.send_keys(session, [:escape])

      wait_for_js(
        session,
        "Escape closes Project drawer",
        "return document.querySelector('#project-sidebar-toggle')?.getAttribute('aria-expanded')",
        "false"
      )

      assert js(session, "return document.activeElement?.id") == "project-sidebar-toggle"

      click(session, "#project-sidebar-toggle")

      wait_for_js(
        session,
        "Project drawer reopens",
        "return document.querySelector('#project-sidebar-toggle')?.getAttribute('aria-expanded')",
        "true"
      )

      click(session, "#project-sidebar-close")

      wait_for_js(
        session,
        "Project drawer closes",
        "return getComputedStyle(document.querySelector('#project-sidebar')).display === 'none'",
        true
      )
    after
      Wallaby.end_session(session)
    end
  end

  test "icon actions show a delayed tooltip that remains inside the viewport" do
    password = "browser-test-password"
    email = "browser-tooltips-#{System.unique_integer([:positive])}@example.com"
    assert {:ok, _user} = Accounts.bootstrap_admin(email, password)

    project = project_fixture(%{})
    task_list = list_fixture(project)
    task = task_fixture(project, %{title: "Tooltip target"})

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {_address, port}} = ThousandIsland.listener_info(server)
    base_url = "http://127.0.0.1:#{port}"
    {:ok, _started} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(
        binary: System.find_executable("chromium"),
        window_size: [width: 1024, height: 700]
      )

    try do
      sign_in(session, base_url, email, password, task.id)
      Browser.visit(session, "#{base_url}/projects/#{project.id}")

      wait_for_js(
        session,
        "Task row ready for tooltip",
        "return !!document.getElementById('move-task-row-button-#{task.id}')",
        true
      )

      Browser.hover(session, Query.css("#move-task-row-button-#{task.id}"))
      assert js(session, "return !!document.querySelector('[role=tooltip]')") == false

      wait_for_js(
        session,
        "Move Task tooltip appears",
        "return document.querySelector('[role=tooltip]')?.textContent",
        "Move Task"
      )

      assert js(session, ~S"""
             const tip = document.querySelector('[role=tooltip]');
             const bounds = tip.getBoundingClientRect();
             return bounds.left >= 0 && bounds.right <= innerWidth &&
               bounds.top >= 0 && bounds.bottom <= innerHeight &&
               getComputedStyle(tip).position === 'fixed';
             """)

      Browser.hover(session, Query.css("#project-selector-toggle"))

      wait_for_js(
        session,
        "Project selector tooltip appears",
        "return document.querySelector('[role=tooltip]')?.textContent",
        "Choose Project"
      )

      js(session, "document.getElementById('project-selector-toggle').blur()")
      Browser.hover(session, Query.css("#project-selector-name"))

      wait_for_js(
        session,
        "Tooltip clears after leaving icon action",
        "return !!document.querySelector('[role=tooltip]')",
        false
      )

      js(session, "document.getElementById('project-selector-toggle').focus()")

      assert js(session, "return document.querySelector('[role=tooltip]')?.textContent") ==
               "Choose Project"

      Browser.hover(session, Query.css("#add-child-list-#{task_list.id}"))

      wait_for_js(
        session,
        "Child List tooltip uses a short action label",
        "return document.querySelector('[role=tooltip]')?.textContent",
        "Add child List"
      )
    after
      Wallaby.end_session(session)
    end
  end

  defp mobile_layout_state(session) do
    js(session, ~S"""
    const content = document.querySelector('#application-content');
    const sidebar = document.querySelector('#project-sidebar');
    const main = document.querySelector('#main-panel');
    const tasks = document.querySelector('#task-list-scroll');
    const tree = document.querySelector('#workspace-tree');
    const footer = document.querySelector('#new-project-button');
    const controls = document.querySelector('#main-panel header');
    const mainBounds = main.getBoundingClientRect();
    const actions = [...controls.querySelectorAll('#share-task-view, #include-child-lists, #task-status-filter-button, #add-task')];
    return {
      sidebarHidden: getComputedStyle(sidebar).display === 'none',
      mainFillsWidth: Math.abs(main.getBoundingClientRect().width - content.getBoundingClientRect().width) < 1,
      contentOverflow: content.scrollHeight > content.clientHeight,
      taskScrollRange: tasks.scrollHeight - tasks.clientHeight,
      treeScrollRange: tree.scrollHeight - tree.clientHeight,
      treeScrollTop: tree.scrollTop,
      footerTop: footer.getBoundingClientRect().top,
      footerVisible: footer.getBoundingClientRect().bottom <= window.innerHeight,
      controlsVisible: controls.getBoundingClientRect().top >= content.getBoundingClientRect().top &&
        controls.getBoundingClientRect().bottom <= window.innerHeight,
      toolbarFits: actions.every(action => action.getBoundingClientRect().left >= mainBounds.left - 1 &&
        action.getBoundingClientRect().right <= mainBounds.right + 1),
      mainFitsWidth: main.scrollWidth <= main.clientWidth
    };
    """)
  end

  defp layout_state(session) do
    js(session, ~S"""
    const content = document.querySelector('#application-content');
    const tasks = document.querySelector('#task-list-scroll');
    const tree = document.querySelector('#workspace-tree');
    const footer = document.querySelector('#new-project-button');
    const controls = document.querySelector('#main-panel header');
    const tableHeader = document.querySelector('#task-table-header');
    const viewportBottom = window.innerHeight;
    return {
      contentOverflow: content.scrollHeight > content.clientHeight,
      contentScrollTop: content.scrollTop,
      taskScrollRange: tasks.scrollHeight - tasks.clientHeight,
      taskScrollTop: tasks.scrollTop,
      treeScrollRange: tree.scrollHeight - tree.clientHeight,
      treeScrollTop: tree.scrollTop,
      newProjectTop: footer.getBoundingClientRect().top,
      newProjectVisible: footer.getBoundingClientRect().bottom <= viewportBottom,
      controlsTop: controls.getBoundingClientRect().top,
      controlsVisible: controls.getBoundingClientRect().top >= content.getBoundingClientRect().top,
      tableHeaderTop: tableHeader.getBoundingClientRect().top,
      tableHeaderVisible: tableHeader.getBoundingClientRect().bottom <= viewportBottom
    };
    """)
  end

  defp sign_in(session, base_url, email, password, task_id) do
    Browser.visit(session, base_url <> "/sign-in")

    wait_for(
      session,
      "sign-in ready",
      %{"url" => base_url <> "/sign-in", "connected" => true},
      task_id
    )

    js(
      session,
      ~S"""
      const email = document.querySelector('input[name="user[email]"]');
      const password = document.querySelector('input[name="user[password]"]');
      email.value = arguments[0];
      password.value = arguments[1];
      document.querySelector('form[action="/auth/user/password/sign_in"]').submit();
      """,
      [email, password]
    )

    wait_for(session, "sign-in", %{"url" => base_url <> "/"}, task_id)
  end

  defp check_identity(session, base_url, project_id, folder_id, task_id) do
    project_url = "#{base_url}/projects/#{project_id}"
    Browser.visit(session, project_url)

    wait_for(
      session,
      "Project ready for identity checks",
      %{"hydrated" => "true", "connected" => true},
      task_id
    )

    click(session, "#project-selector-toggle")

    wait_for_js(
      session,
      "Project choices open from the chevron",
      "return document.querySelector('#project-selector-toggle')?.getAttribute('aria-expanded')",
      "true"
    )

    click(session, "#project-selector-toggle")

    wait_for_js(
      session,
      "Project choices close from the chevron",
      "return document.querySelector('#project-selector-toggle')?.getAttribute('aria-expanded')",
      "false"
    )

    click(session, "#project-selector-toggle")

    wait_for_js(
      session,
      "Project choices reopen",
      "return document.querySelector('#project-selector-toggle')?.getAttribute('aria-expanded')",
      "true"
    )

    click(session, "#main-panel")

    wait_for_js(
      session,
      "Project choices close outside the selector",
      "return document.querySelector('#project-selector-toggle')?.getAttribute('aria-expanded')",
      "false"
    )

    click(session, "#project-edit-button")

    wait_for_js(
      session,
      "Project editor opens",
      "return !!document.querySelector('#project-icons')",
      true
    )

    icons =
      js(
        session,
        "return [...document.querySelectorAll('#project-icons [class*=hero-]')].map(e => ({name: e.className, rendered: getComputedStyle(e).maskImage !== 'none'}))"
      )

    assert length(icons) == 8
    assert Enum.all?(icons, & &1["rendered"])
    click(session, "#project-cancel-button")

    for icon <- ["queue-list", "folder"] do
      assert js(
               session,
               "return getComputedStyle(document.querySelector('#workspace-tree .hero-' + arguments[0])).maskImage !== 'none'",
               [icon]
             )
    end

    toggle_id = "toggle-list-#{folder_id}"
    js(session, "document.getElementById(arguments[0]).focus()", [toggle_id])
    assert js(session, "return document.activeElement?.id") == toggle_id
    Browser.send_keys(session, [:enter])

    wait_for_js(
      session,
      "List expands",
      "return document.getElementById('#{toggle_id}')?.getAttribute('aria-expanded')",
      "true"
    )

    assert js(session, "return document.activeElement?.id") == toggle_id

    assert js(
             session,
             "return getComputedStyle(document.querySelector('#workspace-tree .hero-folder-open')).maskImage !== 'none'"
           )

    Browser.send_keys(session, [:enter])

    wait_for_js(
      session,
      "List collapses",
      "return document.getElementById('#{toggle_id}')?.getAttribute('aria-expanded')",
      "false"
    )

    assert js(session, "return document.activeElement?.id") == toggle_id
  end

  defp check_share(session, base_url, project_id, list_id, task_id) do
    project_url = "#{base_url}/projects/#{project_id}"
    task_route = "#{project_url}/lists/#{list_id}/tasks/#{task_id}"
    default_statuses = "icebox%2Cpending%2Cin_progress%2Cin_review%2Cdone"

    for route <- [project_url, "#{project_url}/lists/#{list_id}", task_route] do
      Browser.visit(session, route)

      wait_for(
        session,
        "Share route hydrated",
        %{"url" => route, "hydrated" => "true", "connected" => true},
        task_id
      )

      share_wait(session, "Share ready", %{"ready" => true}, task_id)

      if route == task_route do
        share_wait(
          session,
          "Task Share beside Move",
          %{
            "modalShare" => true,
            "pointerReachable" => true,
            "leftOfMove" => true,
            "matchesMoveHeight" => true
          },
          task_id
        )
      end

      js(session, ~S"""
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: {writeText: text => { window.__copiedShare = text; return Promise.resolve() }}
      });
      """)

      selector =
        if route == task_route, do: "#task-modal-share-task-view", else: "#share-task-view"

      click(session, selector)

      share_wait(
        session,
        "Share copied current route",
        %{
          "url" => route,
          "copied" => "#{route}?include_children=false&statuses=#{default_statuses}",
          "status" => "Link copied",
          "focus" => String.trim_leading(selector, "#")
        },
        task_id
      )

      share_wait(
        session,
        "Share success is a toast",
        %{"toastVisible" => true, "feedbackVisible" => false},
        task_id
      )

      share_wait(session, "Share success clears", %{"toastVisible" => false}, task_id,
        timeout: 6_000
      )

      if route == task_route do
        js(session, "history.replaceState(history.state, '', arguments[0])", [
          "#{project_url}/lists/#{list_id}"
        ])

        click(session, selector)

        share_wait(
          session,
          "Task Share retains Task target",
          %{
            "copied" => "#{route}?include_children=false&statuses=#{default_statuses}"
          },
          task_id
        )

        js(session, "history.replaceState(history.state, '', arguments[0])", [route])
      end
    end

    js(
      session,
      "window.liveSocket.disconnect(); window.setTimeout(() => window.liveSocket.connect(), 100)"
    )

    wait_for(session, "Share reconnects", %{"connected" => true, "hydrated" => "true"}, task_id)
    share_wait(session, "Share ready after reconnect", %{"ready" => true}, task_id)

    Browser.resize_window(session, 768, 800)
    click(session, "#task-hierarchy-toggle")

    assert js(
             session,
             "return getComputedStyle(document.querySelector('#task-hierarchy-overlay')).display === 'none'"
           )

    Browser.resize_window(session, 767, 800)

    assert js(
             session,
             "return getComputedStyle(document.querySelector('#task-hierarchy-overlay')).display === 'block'"
           )

    click(session, "#task-hierarchy-overlay")

    assert js(
             session,
             "return document.querySelector('#task-detail-layout').dataset.hierarchyExpanded === 'false'"
           )

    Browser.resize_window(session, 390, 800)
    Browser.visit(session, task_route)
    wait_for(session, "narrow Task route", %{"url" => task_route, "hydrated" => "true"}, task_id)

    js(session, ~S"""
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {writeText: () => Promise.reject(new Error('denied'))}
    });
    """)

    click(session, "#task-modal-share-task-view")

    share_wait(
      session,
      "narrow Task manual copy",
      %{
        "url" => task_route,
        "focus" => "task-modal-share-task-view-url",
        "fallbackVisible" => true,
        "fallbackInputWithinBounds" => true,
        "viewportWidth" => 390
      },
      task_id
    )

    Browser.resize_window(session, 1200, 800)
    check_share_snapshots(session, project_url, task_id)
  end

  defp check_share_snapshots(session, project_url, task_id) do
    current_url = project_url <> "?include_children=true&statuses=done&other=keep#fragment"
    Browser.visit(session, current_url)

    wait_for(
      session,
      "Share explicit snapshot",
      %{"url" => current_url, "hydrated" => "true"},
      task_id
    )

    click(session, "#task-status-filter-button")
    click(session, "#task-status-filter-option-pending")

    wait_for(
      session,
      "Share current filters",
      %{
        "url" => project_url <> "?other=keep#fragment",
        "statuses" => ~s(["pending","done"])
      },
      task_id
    )

    share_wait(session, "Share rendered filters", %{"statuses" => "pending,done"}, task_id)

    failure_url =
      project_url <>
        "?include_children=false&include_children=true&statuses=done&statuses=bogus&other=keep#fragment"

    Browser.visit(session, failure_url)

    wait_for(
      session,
      "Share ignores duplicate filter query values",
      %{"url" => failure_url, "hydrated" => "true"},
      task_id
    )

    share_wait(
      session,
      "duplicate keys retain stored filters",
      %{"include" => "true", "statuses" => "pending,done"},
      task_id
    )

    js(session, ~S"""
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {writeText: () => Promise.reject(new Error('denied'))}
    });
    """)

    click(session, "#share-task-view")

    share_wait(
      session,
      "Share clipboard fallback",
      %{
        "url" => failure_url,
        "fallback" =>
          project_url <> "?include_children=true&statuses=pending%2Cdone&other=keep#fragment",
        "focus" => "share-task-view-url",
        "status" => "Copy failed. Select and copy the link below."
      },
      task_id
    )

    empty_url = project_url <> "?statuses="
    Browser.visit(session, empty_url)

    wait_for(
      session,
      "empty snapshot hydrated",
      %{"url" => empty_url, "hydrated" => "true", "statuses" => "[]"},
      task_id
    )

    share_wait(session, "empty snapshot rendered", %{"statuses" => ""}, task_id)

    js(
      session,
      "Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: text => { window.__copiedShare = text; return Promise.resolve() }}})"
    )

    click(session, "#share-task-view")

    share_wait(
      session,
      "Share preserves empty statuses",
      %{
        "url" => empty_url,
        "copied" => project_url <> "?statuses=&include_children=true",
        "status" => "Link copied"
      },
      task_id
    )
  end

  defp share_wait(session, label, expected, task_id, opts \\ []) do
    wait_for(session, label, expected, task_id, Keyword.put(opts, :snapshot, :share))
  end

  defp check_filter_history(session, base_url, project_id, task_id) do
    project_url = "#{base_url}/projects/#{project_id}"
    snapshot_url = project_url <> "?include_children=true&statuses=done&marker=shared#snapshot"
    Browser.visit(session, snapshot_url)

    wait_for(
      session,
      "explicit snapshot",
      %{
        "url" => snapshot_url,
        "pressed" => "true",
        "include" => "true",
        "statuses" => ~s(["done"]),
        "projectHref" => "/projects/#{project_id}",
        "connected" => true,
        "taskVisible" => true
      },
      task_id
    )

    click(session, "#project-tasks-link")

    wait_for(
      session,
      "clean navigation",
      %{"url" => project_url, "pressed" => "true", "connected" => true},
      task_id
    )

    click(session, "#include-child-lists")

    wait_for(
      session,
      "later entry changed",
      %{"url" => project_url, "pressed" => "false", "taskVisible" => false},
      task_id
    )

    js(session, "history.back()")

    wait_for(
      session,
      "untouched Back snapshot",
      %{
        "url" => snapshot_url,
        "pressed" => "true",
        "include" => "true",
        "statuses" => ~s(["done"]),
        "taskVisible" => true
      },
      task_id
    )

    js(session, "location.reload()")

    wait_for(
      session,
      "untouched reload",
      %{"url" => snapshot_url, "pressed" => "true", "connected" => true},
      task_id
    )

    click(session, "#include-child-lists")
    edited_url = project_url <> "?marker=shared#snapshot"

    wait_for(
      session,
      "edited entry cleanup",
      %{
        "url" => edited_url,
        "pressed" => "false",
        "include" => "false",
        "taskVisible" => false
      },
      task_id
    )

    js(session, "history.forward()")

    wait_for(
      session,
      "Forward clean entry",
      %{"url" => project_url, "pressed" => "false"},
      task_id
    )

    js(session, "history.back()")
    wait_for(session, "Back edited entry", %{"url" => edited_url, "pressed" => "false"}, task_id)
    js(session, "location.reload()")

    wait_for(
      session,
      "edited reload",
      %{"url" => edited_url, "pressed" => "false", "connected" => true},
      task_id
    )

    status_url = project_url <> "?include_children=true&statuses=done&other=keep#status"
    Browser.visit(session, status_url)

    wait_for(
      session,
      "status snapshot",
      %{"url" => status_url, "statuses" => ~s(["done"]), "connected" => true},
      task_id
    )

    click(session, "#task-status-filter-button")
    click(session, "#task-status-filter-option-pending")

    wait_for(
      session,
      "status cleanup",
      %{
        "url" => project_url <> "?other=keep#status",
        "statuses" => ~s(["pending","done"]),
        "include" => "true"
      },
      task_id
    )
  end

  defp check_project_memory(session, base_url, project_id, list_id, task_id) do
    project_url = "#{base_url}/projects/#{project_id}"
    list_url = "#{project_url}/lists/#{list_id}"
    project_id_string = Integer.to_string(project_id)

    js(session, ~S"""
    localStorage.setItem('taskman.task-table.include-children', 'invalid');
    localStorage.setItem('taskman.task-table.visible-statuses', 'invalid-json');
    """)

    Browser.visit(session, project_url)

    wait_for(
      session,
      "malformed storage defaults",
      %{
        "url" => project_url,
        "pressed" => "false",
        "include" => nil,
        "statuses" => nil,
        "connected" => true
      },
      task_id
    )

    js(session, ~S"""
    localStorage.setItem('taskman.task-table.include-children', 'true');
    localStorage.setItem('taskman.task-table.visible-statuses', '["done"]');
    """)

    Browser.visit(session, base_url <> "/?include_children=false&statuses=")

    wait_for(
      session,
      "independent root overrides",
      %{
        "url" => project_url,
        "include" => "false",
        "statuses" => "[]",
        "connected" => true
      },
      task_id
    )

    js(
      session,
      ~S"""
      localStorage.setItem('taskman:selected-project-id:v1', arguments[0]);
      localStorage.setItem('taskman.task-table.include-children', 'true');
      localStorage.setItem('taskman.task-table.visible-statuses', '["done"]');
      """,
      [project_id_string]
    )

    Browser.visit(session, base_url <> "/?include_children=false")

    wait_for(
      session,
      "remembered root with include override",
      %{
        "url" => project_url,
        "include" => "false",
        "statuses" => ~s(["done"]),
        "hydrated" => "true"
      },
      task_id
    )

    js(session, ~S"""
    localStorage.setItem('taskman.task-table.include-children', 'true');
    localStorage.setItem('taskman.task-table.visible-statuses', '["done"]');
    """)

    Browser.visit(session, base_url <> "/?statuses=")

    wait_for(
      session,
      "remembered root with statuses override",
      %{
        "url" => project_url,
        "include" => "true",
        "statuses" => "[]",
        "hydrated" => "true"
      },
      task_id
    )

    js(session, "location.reload()")

    wait_for(
      session,
      "remembered reload",
      %{"url" => project_url, "memory" => project_id_string, "connected" => true},
      task_id
    )

    Browser.visit(session, list_url)

    wait_for(
      session,
      "direct List selection",
      %{"memory" => project_id_string, "connected" => true},
      task_id
    )

    click(session, "#application-home-link")

    wait_for(
      session,
      "navbar List to remembered Project root",
      %{
        "url" => project_url,
        "include" => "true",
        "statuses" => "[]",
        "memory" => project_id_string
      },
      task_id
    )

    js(session, "history.back()")
    wait_for(session, "navbar Back skips replaced root", %{"url" => list_url}, task_id)
    js(session, "history.forward()")
    wait_for(session, "navbar Forward returns to Project root", %{"url" => project_url}, task_id)

    Browser.visit(session, project_url <> "/lists/999999999")

    wait_for(
      session,
      "missing List still remembers valid Project",
      %{
        "panel" => "location-not-found",
        "memory" => project_id_string,
        "connected" => true
      },
      task_id
    )

    Browser.visit(session, base_url <> "/")

    wait_for(
      session,
      "missing List restores Project root",
      %{"url" => project_url, "memory" => project_id_string},
      task_id
    )

    js(session, "localStorage.setItem('taskman:selected-project-id:v1', 'bad')")
    Browser.visit(session, base_url <> "/")

    wait_for(
      session,
      "malformed memory",
      %{"url" => base_url <> "/", "panel" => "no-selection", "memory" => nil},
      task_id
    )

    js(session, "localStorage.setItem('taskman:selected-project-id:v1', '999999999')")
    Browser.visit(session, base_url <> "/")

    wait_for(
      session,
      "stale memory",
      %{"url" => base_url <> "/", "panel" => "no-selection", "memory" => nil},
      task_id
    )

    Browser.visit(session, project_url)

    wait_for(
      session,
      "direct URL replaces stale choice",
      %{"url" => project_url, "memory" => project_id_string},
      task_id
    )

    Browser.visit(session, base_url <> "/projects/999999999")

    wait_for(
      session,
      "invalid explicit Project keeps remembered choice",
      %{
        "panel" => "not-found",
        "memory" => project_id_string,
        "connected" => true
      },
      task_id
    )

    Browser.visit(session, project_url)

    cdp(session, "Page.addScriptToEvaluateOnNewDocument", %{
      source:
        "Storage.prototype.getItem = Storage.prototype.setItem = function () { throw new Error('storage denied') }"
    })

    previous_js_errors = Application.get_env(:wallaby, :js_errors, true)
    Application.put_env(:wallaby, :js_errors, false)

    try do
      Browser.visit(session, base_url <> "/")

      wait_for(
        session,
        "denied storage leaves root usable",
        %{"url" => base_url <> "/", "panel" => "no-selection"},
        task_id
      )

      Browser.visit(session, project_url)

      wait_for(
        session,
        "denied writes leave direct Project usable",
        %{"url" => project_url, "panel" => "selected"},
        task_id
      )
    after
      Application.put_env(:wallaby, :js_errors, previous_js_errors)
    end
  end

  defp cdp(session, command, params) do
    response =
      Req.post!(session.url <> "/goog/cdp/execute",
        json: %{cmd: command, params: params},
        retry: false
      )

    assert response.status == 200, inspect(response.body)
  end

  defp click(session, selector), do: Browser.click(session, Query.css(selector))

  defp js(session, script, args \\ []) do
    ref = make_ref()
    owner = self()
    Browser.execute_script(session, script, args, fn result -> send(owner, {ref, result}) end)

    receive do
      {^ref, result} -> result
    after
      5_000 -> flunk("browser script did not return: #{script}")
    end
  end

  defp state(session, task_id), do: js(session, @state_js, [task_id])
  defp share_state(session, task_id), do: js(session, @share_js, [task_id])

  defp wait_for(session, label, expected, task_id, opts \\ []) do
    snapshot = Keyword.get(opts, :snapshot, :state)
    timeout = Keyword.get(opts, :timeout, 12_000)
    deadline = System.monotonic_time(:millisecond) + timeout
    wait_for_snapshot(session, label, expected, task_id, snapshot, deadline)
  end

  defp wait_for_snapshot(session, label, expected, task_id, snapshot, deadline) do
    actual =
      if snapshot == :share, do: share_state(session, task_id), else: state(session, task_id)

    if Enum.all?(expected, fn {key, value} -> Map.get(actual, key) == value end) do
      actual
    else
      if System.monotonic_time(:millisecond) >= deadline do
        flunk("#{label}: expected #{inspect(expected)}, got #{inspect(actual)}")
      end

      receive do
      after
        50 -> wait_for_snapshot(session, label, expected, task_id, snapshot, deadline)
      end
    end
  end

  defp wait_for_js(session, label, script, expected) do
    deadline = System.monotonic_time(:millisecond) + 12_000
    wait_for_js(session, label, script, expected, deadline)
  end

  defp wait_for_js(session, label, script, expected, deadline) do
    actual = js(session, script)

    if actual == expected do
      actual
    else
      if System.monotonic_time(:millisecond) >= deadline do
        flunk("#{label}: expected #{inspect(expected)}, got #{inspect(actual)}")
      end

      receive do
      after
        50 -> wait_for_js(session, label, script, expected, deadline)
      end
    end
  end
end
