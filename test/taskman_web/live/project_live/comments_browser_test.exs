defmodule TaskmanWeb.ProjectLive.CommentsBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Accounts, Tasks}
  alias Wallaby.{Browser, Query}

  @moduletag :browser

  test "Ctrl+Enter posts the comment while Enter still adds a line break" do
    {base_url, session, project, task, _user} = browser_task(1440)

    try do
      Browser.visit(session, task_url(base_url, project, task))
      Browser.find(session, Query.css("#task-comment-form"))

      assert js(
               session,
               "return document.querySelector('#task-comment-post').textContent.includes('Ctrl+Enter')"
             )

      Browser.fill_in(session, Query.css("#task-comment-text"), with: "First line")
      Browser.click(session, Query.css("#task-comment-text"))
      Browser.send_keys(session, [:enter])
      Browser.send_keys(session, "Second line")

      assert state(session)["draft"] == "First line\nSecond line"
      assert {:ok, []} = Tasks.list_comments(project, task)

      Browser.send_keys(session, [:control, :enter, :null])
      wait_until(session, "shortcut posts once", &(&1["commentCount"] == 1))

      assert {:ok, [comment]} = Tasks.list_comments(project, task)
      assert comment.text == "First line\nSecond line"
      assert state(session)["draft"] == ""

      Browser.click(session, Query.css("#task-comment-text"))
      Browser.send_keys(session, [:control, :enter, :null])
      Browser.find(session, Query.css("#task-comment-error"))
      assert {:ok, [_]} = Tasks.list_comments(project, task)
    after
      Wallaby.end_session(session)
    end
  end

  test "wide Activity follows new comments, preserves reading position and draft across tabs" do
    {base_url, session, project, task, user} = browser_task(1440)

    try do
      Browser.visit(session, task_url(base_url, project, task))
      Browser.find(session, Query.css("#task-comment-form"))

      {:ok, first} =
        Tasks.create_comment(project, task, user, %{
          text: String.duplicate("Long first note. ", 240)
        })

      Browser.find(session, Query.css("#task-comment-#{first.id}", visible: :any))
      wait_until(session, "wide thread overflow", &(&1["scrollRange"] > 0))
      wait_until(session, "first overflow follows bottom", &(&1["remaining"] <= 2))

      for index <- 1..4 do
        {:ok, _} =
          Tasks.create_comment(project, task, user, %{
            text: "Earlier #{index}: " <> String.duplicate("long note ", 20)
          })
      end

      wait_until(session, "queued comments arrive", &(&1["commentCount"] == 5))
      assert state(session)["scrollRange"] > 0
      assert state(session)["headersVisible"]
      assert state(session)["composerVisible"]

      js(
        session,
        "const thread = document.querySelector('#task-comment-thread'); thread.scrollTop = thread.scrollHeight; thread.dispatchEvent(new Event('scroll'))"
      )

      {:ok, bottom} = Tasks.create_comment(project, task, user, %{text: "Follow bottom"})
      Browser.find(session, Query.css("#task-comment-#{bottom.id}", visible: :any))
      wait_until(session, "follow new bottom", &(&1["remaining"] <= 2))
      assert state(session)["remaining"] <= 2
      assert state(session)["headersVisible"]
      assert state(session)["composerVisible"]

      js(
        session,
        "const thread = document.querySelector('#task-comment-thread'); thread.scrollTop = 35; thread.dispatchEvent(new Event('scroll')); document.querySelector('#task-comment-text').focus(); document.querySelector('#task-comment-text').value = 'Unposted draft'; document.querySelector('#task-comment-text').dispatchEvent(new Event('input', {bubbles: true}))"
      )

      assert state(session)["tab"] == "activity"

      {:ok, older} =
        Tasks.create_comment(project, task, user, %{
          text: "New while reading <b>plain</b>\nnext line"
        })

      Browser.find(session, Query.css("#task-comment-#{older.id}", visible: :any))
      wait_until(session, "preserve reading position", &(abs(&1["scrollTop"] - 35) <= 2))
      after_comment = state(session)
      assert abs(after_comment["scrollTop"] - 35) <= 2
      assert after_comment["draft"] == "Unposted draft"
      assert after_comment["focus"] == "task-comment-text"
      assert after_comment["htmlChildren"] == 0
      assert after_comment["multilinePreserved"]

      assert js(
               session,
               "return document.getElementById(arguments[0]).querySelector('.task-comment-text').textContent",
               ["task-comment-#{older.id}"]
             ) == older.text

      Browser.click(session, Query.css("#task-sessions-tab"))
      Browser.find(session, Query.css("#task-sessions-tab[aria-selected='true']"))
      assert state(session)["tab"] == "sessions"
      assert state(session)["sessionsUnderTabs"]
      {:ok, hidden} = Tasks.create_comment(project, task, user, %{text: "While hidden"})
      Browser.find(session, Query.css("#task-comment-#{hidden.id}", visible: :any))
      Browser.click(session, Query.css("#task-activity-tab"))
      Browser.find(session, Query.css("#task-activity-tab[aria-selected='true']"))
      wait_until(session, "restore position after Sessions", &(abs(&1["scrollTop"] - 35) <= 2))
      assert abs(state(session)["scrollTop"] - 35) <= 2
      assert state(session)["draft"] == "Unposted draft"

      js(
        session,
        "const thread = document.querySelector('#task-comment-thread'); thread.scrollTop = thread.scrollHeight; thread.dispatchEvent(new Event('scroll'))"
      )

      {:ok, followed} =
        Tasks.create_comment(project, task, user, %{text: "Follow after Sessions"})

      Browser.find(session, Query.css("#task-comment-#{followed.id}", visible: :any))
      wait_until(session, "follow after Sessions", &(&1["remaining"] <= 2))
    after
      Wallaby.end_session(session)
    end
  end

  test "switching Tasks resets the wide comment scroll to the new thread" do
    {base_url, session, project, task, user} = browser_task(1440)
    child = task_fixture(project, %{}, parent: task)

    {:ok, parent_comment} =
      Tasks.create_comment(project, task, user, %{text: String.duplicate("Parent note. ", 300)})

    {:ok, child_comment} =
      Tasks.create_comment(project, child, user, %{text: String.duplicate("Child note. ", 300)})

    try do
      Browser.visit(session, task_url(base_url, project, task))
      Browser.find(session, Query.css("#task-comment-#{parent_comment.id}"))
      wait_until(session, "parent thread overflow", &(&1["scrollRange"] > 100))

      js(
        session,
        "const thread = document.querySelector('#task-comment-thread'); thread.scrollTop = 35; thread.dispatchEvent(new Event('scroll'))"
      )

      assert abs(state(session)["scrollTop"] - 35) <= 2
      Browser.click(session, Query.css("#task-hierarchy-link-#{child.id}"))
      Browser.find(session, Query.css("#task-comment-#{child_comment.id}"))
      wait_until(session, "new Task follows its own bottom", &(&1["remaining"] <= 2))
      assert state(session)["scrollTop"] > 35
      assert state(session)["tab"] == "activity"
    after
      Wallaby.end_session(session)
    end
  end

  test "narrow Activity shares the detail scroll and keeps reading position on new comments" do
    {base_url, session, project, task, user} = browser_task(390)

    try do
      Browser.visit(session, task_url(base_url, project, task))
      Browser.find(session, Query.css("#task-comment-form"))
      assert state(session)["viewport"] < 1280
      assert state(session)["independentScroll"] == false

      {:ok, first} =
        Tasks.create_comment(project, task, user, %{
          text: "Incoming " <> String.duplicate("with detail ", 220)
        })

      Browser.find(session, Query.css("#task-comment-#{first.id}", visible: :any))
      wait_until(session, "narrow detail overflows", &(&1["detailScrollRange"] > 80))
      js(session, "document.querySelector('#task-detail-content').scrollTop = 80")

      {:ok, second} = Tasks.create_comment(project, task, user, %{text: "New while reading"})
      Browser.find(session, Query.css("#task-comment-#{second.id}", visible: :any))
      assert state(session)["independentScroll"] == false
      assert abs(state(session)["detailScrollTop"] - 80) <= 2

      js(session, "document.querySelector('#task-activity-tab').focus()")
      Browser.send_keys(session, [:right_arrow])
      Browser.find(session, Query.css("#task-sessions-tab[aria-selected='true']"))
      assert state(session)["tab"] == "sessions"
      assert state(session)["sessionsUnderTabs"]
    after
      Wallaby.end_session(session)
    end
  end

  defp browser_task(width) do
    email = "comments-browser-#{Ecto.UUID.generate()}@example.com"
    {:ok, user} = Accounts.bootstrap_admin(email, "browser-test-password")
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
      Wallaby.start_session(binary: System.find_executable("chromium"), window_size: [width, 800])

    Browser.resize_window(session, width, 800)
    sign_in(session, base_url, email, "browser-test-password")
    {base_url, session, project, task, user}
  end

  defp sign_in(session, base_url, email, password) do
    Browser.visit(session, base_url <> "/sign-in")
    Browser.find(session, Query.css("input[name='user[email]']"))

    js(
      session,
      "document.querySelector('input[name=\"user[email]\"]').value = arguments[0]; document.querySelector('input[name=\"user[password]\"]').value = arguments[1]; document.querySelector('form[action=\"/auth/user/password/sign_in\"]').submit()",
      [email, password]
    )

    Browser.find(session, Query.css("#main-panel"))
  end

  defp task_url(base_url, project, task),
    do: "#{base_url}/projects/#{project.id}/tasks/#{task.id}"

  defp state(session) do
    js(session, ~S"""
      const thread = document.querySelector('#task-comment-thread');
      const tabs = document.querySelector('#task-detail-tabs');
      const sessions = document.querySelector('#task-sessions');
      const composer = document.querySelector('#task-comment-form');
      const bounds = element => element.getBoundingClientRect();
      return {
        scrollTop: thread.scrollTop,
        viewport: innerWidth,
        scrollRange: thread.scrollHeight - thread.clientHeight,
        detailScrollTop: document.querySelector('#task-detail-content').scrollTop,
        detailScrollRange: document.querySelector('#task-detail-content').scrollHeight - document.querySelector('#task-detail-content').clientHeight,
        remaining: thread.scrollHeight - thread.clientHeight - thread.scrollTop,
        independentScroll: getComputedStyle(thread).overflowY === 'auto',
        headersVisible: bounds(tabs).top >= bounds(document.querySelector('#task-detail-content')).top && bounds(tabs).bottom <= innerHeight,
        composerVisible: bounds(composer).bottom <= innerHeight,
        tab: document.querySelector('#task-sessions-tab').getAttribute('aria-selected') === 'true' ? 'sessions' : 'activity',
        sessionsUnderTabs: !sessions.hidden && bounds(sessions).top >= bounds(tabs).bottom,
        draft: document.querySelector('#task-comment-text').value,
        focus: document.activeElement?.id,
        htmlChildren: thread.querySelectorAll('.task-comment-text b').length,
        multilinePreserved: [...thread.querySelectorAll('.task-comment-text')].some(text => text.textContent.includes('\n') && getComputedStyle(text).whiteSpace === 'pre-wrap'),
        commentCount: thread.querySelectorAll('article').length
      };
    """)
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

  defp wait_until(session, label, predicate) do
    deadline = System.monotonic_time(:millisecond) + 5_000
    wait_until(session, label, predicate, deadline)
  end

  defp wait_until(session, label, predicate, deadline) do
    snapshot = state(session)

    cond do
      predicate.(snapshot) ->
        snapshot

      System.monotonic_time(:millisecond) < deadline ->
        wait_until(session, label, predicate, deadline)

      true ->
        flunk("#{label}: #{inspect(snapshot)}")
    end
  end
end
