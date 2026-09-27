defmodule TaskmanWeb.ProjectLive.CommentDepartureTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "a whitespace draft closes immediately while a comment draft keeps its exact destination",
       %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)
    other = task_fixture(project)
    path = "/projects/#{project.id}/tasks/#{task.id}"
    destination = "/projects/#{project.id}/tasks/#{other.id}?include_children=false"
    {:ok, view, _} = live(conn, path)

    view |> form("#task-comment-form", comment: %{text: "  "}) |> render_change()
    view |> element("#task-modal-close") |> render_click()
    refute has_element?(view, "#task-comment-departure")
    assert_patch(view, "/projects/#{project.id}")

    {:ok, view, _} = live(conn, path)
    view |> form("#task-comment-form", comment: %{text: " Unposted "}) |> render_change()
    view |> render_hook("request_task_departure", %{"destination" => destination})
    assert has_element?(view, "#task-comment-departure[role='dialog']")
    assert has_element?(view, "#task-comment-departure-go-back")
    assert has_element?(view, "#task-comment-departure-submit")
    assert has_element?(view, "#task-comment-departure-discard")
    assert view_assigns(view).comment_departure.destination == destination
    view |> render_hook("request_task_departure", %{"destination" => "/projects/#{project.id}"})
    assert view_assigns(view).comment_departure.destination == destination
    assert view_assigns(view).comments.draft == " Unposted "
  end

  test "same Task filter route keeps the draft, and history fallback restores detail", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project)
    other = task_fixture(project)
    path = "/projects/#{project.id}/tasks/#{task.id}"
    {:ok, view, _} = live(conn, path)
    view |> form("#task-comment-form", comment: %{text: "Keep me"}) |> render_change()

    render_patch(view, path <> "?include_children=false")
    refute has_element?(view, "#task-comment-departure")
    assert has_element?(view, "#task-comment-text", "Keep me")

    task_list = list_fixture(project)
    render_patch(view, "/projects/#{project.id}/lists/#{task_list.id}/tasks/#{task.id}")
    refute has_element?(view, "#task-comment-departure")
    assert has_element?(view, "#task-comment-text", "Keep me")
    render_patch(view, path <> "?include_children=false")

    render_patch(view, "/projects/#{project.id}/tasks/#{other.id}?include_children=true")
    assert has_element?(view, "#task-comment-departure")

    assert view_assigns(view).comment_departure.destination ==
             "/projects/#{project.id}/tasks/#{other.id}?include_children=true"

    assert_patch(view, path <> "?include_children=false")
    assert has_element?(view, "#task-comment-text", "Keep me")
  end

  test "the same Task ID under another Project is a guarded departure", %{conn: conn} do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task = task_fixture(project)
    path = "/projects/#{project.id}/tasks/#{task.id}"
    destination = "/projects/#{other_project.id}/tasks/#{task.id}?include_children=false"
    {:ok, view, _} = live(conn, path)

    view |> form("#task-comment-form", comment: %{text: "Keep this draft"}) |> render_change()
    render_patch(view, destination)

    assert has_element?(view, "#task-comment-departure")
    assert view_assigns(view).comment_departure.destination == destination
    assert view_assigns(view).comments.draft == "Keep this draft"
    assert_patch(view, path)
  end

  test "a stale same-Task List history route is a guarded departure", %{conn: conn} do
    project = project_fixture(%{})
    stale_list = list_fixture(project)
    task = task_fixture(project)
    path = "/projects/#{project.id}/tasks/#{task.id}?statuses=open"

    destination =
      "/projects/#{project.id}/lists/#{stale_list.id}/tasks/#{task.id}?statuses=done"

    {:ok, view, _} = live(conn, path)

    view
    |> form("#task-comment-form", comment: %{text: "Keep stale history draft"})
    |> render_change()

    Taskman.Repo.delete!(stale_list)

    render_patch(view, destination)

    assert has_element?(view, "#task-comment-departure")
    assert view_assigns(view).comment_departure.destination == destination
    assert view_assigns(view).comments.draft == "Keep stale history draft"
    assert_patch(view, path)
  end

  test "Go back retains the draft; discard and submit navigate only after their work succeeds", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project)
    path = "/projects/#{project.id}/tasks/#{task.id}"
    destination = "/projects/#{project.id}?include_children=false"
    {:ok, view, _} = live(conn, path)
    view |> form("#task-comment-form", comment: %{text: "Hold"}) |> render_change()
    view |> render_hook("request_task_departure", %{"destination" => destination})
    view |> element("#task-comment-departure-go-back") |> render_click()
    refute has_element?(view, "#task-comment-departure")
    assert has_element?(view, "#task-comment-text", "Hold")

    view |> render_hook("request_task_departure", %{"destination" => destination})
    view |> element("#task-comment-departure-discard") |> render_click()
    assert_patch(view, destination)
    assert {:ok, []} = Tasks.list_comments(project, task)

    {:ok, view, _} = live(conn, path)
    view |> form("#task-comment-form", comment: %{text: "Post once"}) |> render_change()
    view |> render_hook("request_task_departure", %{"destination" => destination})
    view |> element("#task-comment-departure-submit") |> render_click()
    assert_patch(view, destination)
    assert {:ok, [comment]} = Tasks.list_comments(project, task)
    assert comment.text == "Post once"
  end

  test "a failed Task flush keeps the draft and destination for either final action", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    destination = "/projects/#{project.id}?statuses=done"
    {:ok, view, _} = live(conn, "/projects/#{project.id}/tasks/#{task.id}")

    view |> form("#task-comment-form", comment: %{text: "Keep comment"}) |> render_change()

    view
    |> form("#task-form", task: %{title: ""})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> render_hook("request_task_departure", %{"destination" => destination})
    view |> element("#task-comment-departure-submit") |> render_click()
    assert has_element?(view, "#task-comment-departure-error", "Save the Task fields")
    assert view_assigns(view).comment_departure.destination == destination
    assert view_assigns(view).comments.draft == "Keep comment"
    assert {:ok, []} = Tasks.list_comments(project, task)

    view |> element("#task-comment-departure-discard") |> render_click()
    assert view_assigns(view).comment_departure.destination == destination
    assert view_assigns(view).comments.draft == "Keep comment"
    assert Tasks.get_task_for_project(project, task.id).title == "Before"
  end

  test "a failed post from Sessions selects Activity and retains the pending route", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)
    destination = "/projects/#{project.id}?include_children=false"
    {:ok, view, _} = live(conn, "/projects/#{project.id}/tasks/#{task.id}")
    view |> form("#task-comment-form", comment: %{text: "Keep comment"}) |> render_change()
    view |> element("#task-sessions-tab") |> render_click()
    view |> render_hook("request_task_departure", %{"destination" => destination})
    Taskman.Repo.delete!(task)

    view |> element("#task-comment-departure-submit") |> render_click()
    assert has_element?(view, "#task-activity-tab[aria-selected='true']")
    assert has_element?(view, "#task-comment-error", "no longer available")
    refute has_element?(view, "#task-comment-departure")
    refute has_element?(view, "#task-modal[inert]")
    assert view_assigns(view).comments.draft == "Keep comment"
    assert view_assigns(view).comment_departure.destination == destination

    view
    |> render_hook("request_task_departure", %{
      "destination" => "/projects/#{project.id}?statuses=done"
    })

    assert has_element?(view, "#task-comment-departure")
    assert view_assigns(view).comment_departure.destination == destination
  end

  test "a successful ordinary post clears a destination retained by a failed close submit", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project)
    other = task_fixture(project)
    retained_destination = "/projects/#{project.id}?include_children=false"
    next_destination = "/projects/#{project.id}/tasks/#{other.id}"
    {:ok, view, _} = live(conn, "/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-comment-form", comment: %{text: String.duplicate("a", 10_001)})
    |> render_change()

    view |> render_hook("request_task_departure", %{"destination" => retained_destination})
    view |> element("#task-comment-departure-submit") |> render_click()
    refute has_element?(view, "#task-comment-departure")
    assert view_assigns(view).comment_departure.destination == retained_destination

    view
    |> form("#task-comment-form", comment: %{text: "Recovered ordinary post"})
    |> render_submit()

    assert {:ok, [comment]} = Tasks.list_comments(project, task)
    assert comment.text == "Recovered ordinary post"
    assert view_assigns(view).comment_departure.destination == nil

    view |> form("#task-comment-form", comment: %{text: "A new draft"}) |> render_change()
    view |> render_hook("request_task_departure", %{"destination" => next_destination})
    assert has_element?(view, "#task-comment-departure")
    assert view_assigns(view).comment_departure.destination == next_destination
  end

  defp view_assigns(view) do
    %{socket: socket} = :sys.get_state(view.pid)
    socket.assigns
  end
end
