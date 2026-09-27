defmodule TaskmanWeb.ProjectLive.CommentDepartureBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts
  alias Wallaby.{Browser, Query}

  @moduletag :browser

  test "Close, backdrop, and Escape hold a comment draft and restore detail focus" do
    {base_url, session, project, task} = browser_task()
    path = "/projects/#{project.id}/tasks/#{task.id}"

    try do
      Browser.visit(session, base_url <> path)
      Browser.fill_in(session, Query.css("#task-comment-text"), with: "Unposted draft")
      Browser.click(session, Query.css("#task-modal-close"))
      Browser.find(session, Query.css("#task-comment-departure"))

      assert script(session, "return document.activeElement.id") ==
               "task-comment-departure-go-back"

      assert script(session, "return location.pathname + location.search") == path

      Browser.click(session, Query.css("#task-comment-departure-go-back"))
      Browser.find(session, Query.css("#task-comment-text"))

      assert script(session, "return document.querySelector('#task-comment-text').value") ==
               "Unposted draft"

      Browser.find(session, Query.css("#task-modal-content:focus"))
      assert script(session, "return document.activeElement.id") == "task-modal-content"

      script(session, "document.elementFromPoint(8, 8).click()")
      Browser.find(session, Query.css("#task-comment-departure"))
      Browser.send_keys(session, [:escape])
      Browser.find(session, Query.css("#task-modal-content:focus"))
      assert script(session, "return document.querySelector('#task-comment-departure') === null")

      assert script(session, "return document.querySelector('#task-comment-text').value") ==
               "Unposted draft"
    after
      Wallaby.end_session(session)
    end
  end

  test "location links and Back retain the draft, while a same Task link keeps it without asking" do
    {base_url, session, project, task} = browser_task()
    path = "/projects/#{project.id}/tasks/#{task.id}"

    try do
      Browser.visit(session, base_url <> "/projects/#{project.id}")
      Browser.click(session, Query.css("#open-task-#{task.id}"))
      Browser.find(session, Query.css("#task-comment-text"))
      Browser.fill_in(session, Query.css("#task-comment-text"), with: "Hold route")
      Browser.click(session, Query.css("#task-location-project-#{project.id}"))
      Browser.find(session, Query.css("#task-comment-departure"))
      assert script(session, "return location.pathname + location.search") == path
      Browser.click(session, Query.css("#task-comment-departure-go-back"))

      Browser.click(session, Query.css("#task-hierarchy-toggle"))
      Browser.click(session, Query.css("#task-hierarchy-link-#{task.id}"))
      Browser.find(session, Query.css("#task-comment-text"))

      assert script(session, "return document.querySelector('#task-comment-text').value") ==
               "Hold route"

      assert script(session, "return document.querySelector('#task-comment-departure') === null")

      script(session, "history.back()")
      Browser.find(session, Query.css("#task-comment-departure"))

      assert script(session, "return document.querySelector('#task-comment-text').value") ==
               "Hold route"

      assert script(session, "return location.pathname + location.search") == path
    after
      Wallaby.end_session(session)
    end
  end

  test "reload warning follows a trimmed draft through a failed flush and discard" do
    {base_url, session, project, task} = browser_task()

    try do
      Browser.visit(session, base_url <> "/projects/#{project.id}/tasks/#{task.id}")
      Browser.find(session, Query.css("#task-comment-text"))
      refute warning_active?(session)
      Browser.fill_in(session, Query.css("#task-comment-text"), with: "   ")
      refute warning_active?(session)
      Browser.fill_in(session, Query.css("#task-comment-text"), with: "Keep on reload")
      assert warning_active?(session)

      Browser.fill_in(session, Query.css("#task-title"), with: "")
      Browser.click(session, Query.css("#task-modal-close"))
      Browser.find(session, Query.css("#task-comment-departure"))
      Browser.click(session, Query.css("#task-comment-departure-discard"))
      Browser.find(session, Query.css("#task-comment-departure-error"))
      assert warning_active?(session)
      Browser.click(session, Query.css("#task-comment-departure-go-back"))
      Browser.fill_in(session, Query.css("#task-title"), with: "Valid title")
      Browser.click(session, Query.css("#task-modal-close"))
      Browser.find(session, Query.css("#task-comment-departure"))
      Browser.click(session, Query.css("#task-comment-departure-discard"))
      Browser.find(session, Query.css("#task-comment-form", count: 0))
      refute warning_active?(session)
    after
      Wallaby.end_session(session)
    end
  end

  test "layered Escape closes hierarchy and Move before guarding Task departure" do
    {base_url, session, project, task} = browser_task()
    _child = task_fixture(project, %{}, parent: task)

    try do
      Browser.resize_window(session, 390, 800)
      Browser.visit(session, base_url <> "/projects/#{project.id}/tasks/#{task.id}")
      Browser.find(session, Query.css("#task-comment-text"))
      Browser.fill_in(session, Query.css("#task-comment-text"), with: "Layered draft")

      assert script(
               session,
               "return document.querySelector('#task-detail-layout').dataset.hierarchyExpanded"
             ) == "true"

      Browser.send_keys(session, [:escape])

      assert script(
               session,
               "return document.querySelector('#task-detail-layout').dataset.hierarchyExpanded"
             ) == "false"

      assert script(session, "return document.querySelector('#task-comment-departure') === null")

      Browser.click(session, Query.css("#move-task-detail-button-#{task.id}"))
      Browser.find(session, Query.css("#move-task-#{task.id}"))
      Browser.send_keys(session, [:escape])
      Browser.find(session, Query.css("#move-task-#{task.id}", count: 0))
      assert script(session, "return document.querySelector('#task-comment-departure') === null")

      Browser.send_keys(session, [:escape])
      Browser.find(session, Query.css("#task-comment-departure"))

      assert script(session, "return document.querySelector('#task-comment-text').value") ==
               "Layered draft"
    after
      Wallaby.end_session(session)
    end
  end

  test "a failed submit from Sessions reveals Activity and focuses its retained error" do
    {base_url, session, project, task} = browser_task()
    path = "/projects/#{project.id}/tasks/#{task.id}"

    try do
      Browser.visit(session, base_url <> path)
      Browser.fill_in(session, Query.css("#task-comment-text"), with: "Keep failed post")
      Browser.click(session, Query.css("#task-sessions-tab"))
      Browser.click(session, Query.css("#task-modal-close"))
      Browser.find(session, Query.css("#task-comment-departure"))
      Taskman.Repo.delete!(task)
      Browser.click(session, Query.css("#task-comment-departure-submit"))
      Browser.find(session, Query.css("#task-comment-departure", count: 0))
      Browser.find(session, Query.css("#task-activity-tab[aria-selected='true']"))
      Browser.find(session, Query.css("#task-comment-error"))
      Browser.find(session, Query.css("#task-modal:not([inert])"))

      assert script(session, "return document.activeElement.id") in [
               "task-comment-error",
               "task-comment-text"
             ]

      assert script(session, "return document.querySelector('#task-comment-text').value") ==
               "Keep failed post"

      assert script(session, "return location.pathname + location.search") == path

      Browser.click(session, Query.css("#task-modal-close"))
      Browser.find(session, Query.css("#task-comment-departure"))
    after
      Wallaby.end_session(session)
    end
  end

  test "canceled Back keeps the original history destination available for another attempt" do
    {base_url, session, project, task} = browser_task()
    project_path = "/projects/#{project.id}"
    snapshot_path = project_path <> "?include_children=true"
    task_path = "/projects/#{project.id}/tasks/#{task.id}"

    try do
      Browser.visit(session, base_url <> snapshot_path)
      Browser.click(session, Query.css("#open-task-#{task.id}"))
      Browser.find(session, Query.css("#task-comment-text"))
      Browser.fill_in(session, Query.css("#task-comment-text"), with: "Back draft")
      script(session, "document.querySelector('#include-child-lists').click()")
      Browser.find(session, Query.css("#include-child-lists[aria-pressed='false']"))

      assert script(session, "return localStorage.getItem('taskman.task-table.include-children')") ==
               "false"

      history_length = script(session, "return history.length")
      script(session, "history.back()")
      Browser.find(session, Query.css("#task-comment-departure"))
      assert script(session, "return location.pathname") == task_path
      assert script(session, "return history.length") == history_length
      Browser.find(session, Query.css("#include-child-lists[aria-pressed='false']"))

      assert script(session, "return localStorage.getItem('taskman.task-table.include-children')") ==
               "false"

      Browser.click(session, Query.css("#task-comment-departure-go-back"))
      Browser.find(session, Query.css("#task-comment-departure", count: 0))

      script(session, "history.back()")
      Browser.find(session, Query.css("#task-comment-departure"))
      assert script(session, "return location.pathname") == task_path
      Browser.click(session, Query.css("#task-comment-departure-discard"))
      Browser.find(session, Query.css("#task-comment-form", count: 0))
      assert script(session, "return location.pathname + location.search") == snapshot_path
    after
      Wallaby.end_session(session)
    end
  end

  test "canceled Forward keeps the next history destination available for another attempt" do
    {base_url, session, project, task} = browser_task()
    child = task_fixture(project, %{}, parent: task)
    task_path = "/projects/#{project.id}/tasks/#{task.id}"
    child_path = "/projects/#{project.id}/tasks/#{child.id}"

    try do
      Browser.visit(session, base_url <> "/projects/#{project.id}")
      Browser.click(session, Query.css("#open-task-#{task.id}"))
      Browser.find(session, Query.css("#task-comment-text"))
      Browser.click(session, Query.css("#task-hierarchy-link-#{child.id}"))
      Browser.find(session, Query.css("#task-detail-discussion[data-task-id='#{child.id}']"))
      script(session, "history.back()")
      Browser.find(session, Query.css("#task-detail-discussion[data-task-id='#{task.id}']"))
      Browser.fill_in(session, Query.css("#task-comment-text"), with: "Forward draft")
      history_length = script(session, "return history.length")

      script(session, "history.forward()")
      Browser.find(session, Query.css("#task-comment-departure"))
      assert script(session, "return location.pathname") == task_path
      assert script(session, "return history.length") == history_length

      Browser.click(session, Query.css("#task-comment-departure-go-back"))
      Browser.find(session, Query.css("#task-comment-departure", count: 0))

      script(session, "history.forward()")
      Browser.find(session, Query.css("#task-comment-departure"))
      assert script(session, "return location.pathname") == task_path

      assert script(session, "return document.querySelector('#task-comment-text').value") ==
               "Forward draft"

      Browser.click(session, Query.css("#task-comment-departure-discard"))
      Browser.find(session, Query.css("#task-detail-discussion[data-task-id='#{child.id}']"))
      assert script(session, "return location.pathname") == child_path
      assert script(session, "return document.querySelector('#task-comment-text').value") == ""
    after
      Wallaby.end_session(session)
    end
  end

  defp browser_task do
    email = "departure-browser-#{Ecto.UUID.generate()}@example.com"
    {:ok, _user} = Accounts.bootstrap_admin(email, "browser-test-password")
    project = project_fixture(%{})
    task = task_fixture(project)

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    {:ok, {_address, port}} = ThousandIsland.listener_info(server)
    {:ok, _} = Application.ensure_all_started(:wallaby)
    base_url = "http://127.0.0.1:#{port}"

    {:ok, session} =
      Wallaby.start_session(binary: System.find_executable("chromium"), window_size: [1440, 800])

    Browser.visit(session, base_url <> "/sign-in")
    Browser.find(session, Query.css("input[name='user[email]']"))

    script(
      session,
      "document.querySelector('input[name=\"user[email]\"]').value = arguments[0]; document.querySelector('input[name=\"user[password]\"]').value = arguments[1]; document.querySelector('form[action=\"/auth/user/password/sign_in\"]').submit()",
      [email, "browser-test-password"]
    )

    Browser.find(session, Query.css("#main-panel"))
    {base_url, session, project, task}
  end

  defp script(session, js, args \\ []) do
    ref = make_ref()
    owner = self()
    Browser.execute_script(session, js, args, fn result -> send(owner, {ref, result}) end)

    receive do
      {^ref, result} -> result
    after
      5_000 -> flunk("browser script did not return")
    end
  end

  defp warning_active?(session) do
    script(
      session,
      "const event = new Event('beforeunload', {cancelable: true}); dispatchEvent(event); return event.defaultPrevented"
    )
  end
end
