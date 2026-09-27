defmodule TaskmanWeb.ProjectLive.CommentsTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks

  setup %{conn: conn} do
    user = user_fixture()
    {:ok, conn: log_in_user(conn, user), user: user}
  end

  test "Activity opens with an explicit empty thread and accessible tabs", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(view, "#task-detail-tabs[role='tablist']")

    assert has_element?(
             view,
             "#task-activity-tab[role='tab'][aria-selected='true'][tabindex='0']"
           )

    assert has_element?(
             view,
             "#task-sessions-tab[role='tab'][aria-selected='false'][tabindex='-1']"
           )

    assert has_element?(
             view,
             "#task-activity[role='tabpanel'][aria-labelledby='task-activity-tab']"
           )

    assert has_element?(view, "#task-activity-empty", "No comments yet.")
    assert has_element?(view, "#task-comment-form[phx-submit='post_task_comment']")
    assert has_element?(view, "#task-comment-text[aria-label='Comment']")
    assert has_element?(view, "#task-comment-post[type='submit'][phx-disable-with]")
    assert has_element?(view, "#task-sessions[hidden][inert]")
  end

  test "tabs keep the same Task's draft and reset after a confirmed Task change", %{conn: conn} do
    project = project_fixture(%{})
    first = task_fixture(project)
    second = task_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{first.id}")

    view |> form("#task-comment-form", comment: %{text: "Unposted note"}) |> render_change()
    view |> element("#task-sessions-tab") |> render_click()
    assert has_element?(view, "#task-sessions-tab[aria-selected='true']")
    assert has_element?(view, "#task-activity[hidden][inert]")

    render_patch(view, "/projects/#{project.id}/tasks/#{first.id}?include_children=false")
    assert has_element?(view, "#task-sessions-tab[aria-selected='true']")
    assert has_element?(view, "#task-comment-text", "Unposted note")

    render_patch(view, ~p"/projects/#{project.id}/tasks/#{second.id}")
    assert has_element?(view, "#task-comment-departure")
    assert has_element?(view, "#task-sessions-tab[aria-selected='true']")
    assert has_element?(view, "#task-comment-text", "Unposted note")

    view |> element("#task-comment-departure-discard") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{second.id}")
    assert has_element?(view, "#task-activity-tab[aria-selected='true']")
    refute has_element?(view, "#task-comment-text", "Unposted note")
  end

  test "posting renders full plain text and the account with a local timestamp", %{
    conn: conn,
    user: user
  } do
    project = project_fixture(%{})
    task = task_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-comment-form", comment: %{text: "  First <b>line</b>\nSecond line  "})
    |> render_submit()

    assert {:ok, [comment]} = Tasks.list_comments(project, task)
    assert comment.text == "First <b>line</b>\nSecond line"

    assert has_element?(
             view,
             "#task-comment-#{comment.id} .task-comment-text",
             "First <b>line</b>"
           )

    assert has_element?(view, "#task-comment-#{comment.id} .task-comment-text", "Second line")
    refute has_element?(view, "#task-comment-#{comment.id} b")
    assert has_element?(view, "#task-comment-#{comment.id} time[datetime]")
    assert has_element?(view, "#task-comment-#{comment.id}", to_string(user.email))
    refute has_element?(view, "#task-activity-empty")
  end

  test "invalid draft remains in the independent composer", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :done})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> form("#task-comment-form", comment: %{text: "   "}) |> render_change()
    assert has_element?(view, "#task-comment-form [data-role=field-error]")

    view |> form("#task-comment-form", comment: %{text: "   "}) |> render_submit()
    assert view_assigns(view).comments.draft == "   "
    assert has_element?(view, "#task-comment-form [data-role=field-error]")
    assert {:ok, []} = Tasks.list_comments(project, task)

    view |> form("#task-comment-form", comment: %{text: "Done can discuss"}) |> render_submit()
    assert {:ok, [_]} = Tasks.list_comments(project, task)
  end

  test "opening a Task loads its existing ordered comment thread", %{conn: conn, user: user} do
    project = project_fixture(%{})
    task = task_fixture(project)
    {:ok, first} = Tasks.create_comment(project, task, user, %{text: "First note"})
    {:ok, second} = Tasks.create_comment(project, task, user, %{text: "Second note"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(view, "#task-comment-#{first.id}", "First note")
    assert has_element?(view, "#task-comment-#{second.id}", "Second note")
    refute has_element?(view, "#task-activity-empty")

    assert view
           |> render()
           |> LazyHTML.from_fragment()
           |> LazyHTML.query("#task-comment-thread article")
           |> LazyHTML.attribute("id") == [
             "task-comment-#{first.id}",
             "task-comment-#{second.id}"
           ]
  end

  test "a failed post keeps the draft and explains the failure", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    Taskman.Repo.delete!(task)

    view
    |> form("#task-comment-form", comment: %{text: "Keep this draft"})
    |> render_submit()

    assert has_element?(
             view,
             "#task-comment-error[role='alert']",
             "This Task is no longer available"
           )

    assert has_element?(view, "#task-comment-text", "Keep this draft")
    assert has_element?(view, "#task-modal")
  end

  test "a selected Task comment notification refreshes ordered comments and timestamp without editing state",
       %{
         conn: conn,
         user: user
       } do
    previous_delay = Application.get_env(:taskman, :task_autosave_delay_ms)
    Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)
    on_exit(fn -> Application.put_env(:taskman, :task_autosave_delay_ms, previous_delay) end)
    project = project_fixture(%{})
    task = task_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Pending Task title"})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> form("#task-comment-form", comment: %{text: "Keep this draft"}) |> render_change()
    view |> element("#task-sessions-tab") |> render_click()
    editing_before = view_assigns(view).editing

    {:ok, first} =
      Task.async(fn -> Tasks.create_comment(project, task, user, %{text: "First"}) end)
      |> Task.await()

    {:ok, second} =
      Task.async(fn -> Tasks.create_comment(project, task, user, %{text: "Second"}) end)
      |> Task.await()

    sync_view(view)

    assigns = view_assigns(view)
    assert assigns.editing.autosave == editing_before.autosave

    assert assigns.editing.selected_task.updated_at ==
             Tasks.get_task_for_project(project, task.id).updated_at

    assert assigns.comments.draft == "Keep this draft"
    assert has_element?(view, "#task-title[value='Pending Task title']")
    assert has_element?(view, "#task-sessions-tab[aria-selected='true']")
    assert has_element?(view, "#task-activity[hidden][inert]")
    assert comment_ids(view) == ["task-comment-#{first.id}", "task-comment-#{second.id}"]
  end

  test "another Task's comment notification leaves selected detail untouched", %{
    conn: conn,
    user: user
  } do
    project = project_fixture(%{})
    selected = task_fixture(project)
    other = task_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{selected.id}")
    before = view_assigns(view)

    {:ok, _} =
      Task.async(fn -> Tasks.create_comment(project, other, user, %{text: "Other Task"}) end)
      |> Task.await()

    sync_view(view)

    assert comment_ids(view) == []
    assert view_assigns(view).editing == before.editing
    assert view_assigns(view).comments == before.comments
  end

  test "a local post and its broadcast render the comment once", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    view |> form("#task-comment-form", comment: %{text: "Just once"}) |> render_submit()
    assert {:ok, [comment]} = Tasks.list_comments(project, task)

    :ok =
      Task.async(fn ->
        Taskman.ChangeNotifications.publish_comment(project.id, task.id, comment.id)
      end)
      |> Task.await()

    sync_view(view)

    assert comment_ids(view) == ["task-comment-#{comment.id}"]

    assert view_assigns(view).editing.selected_task.updated_at ==
             Tasks.get_task_for_project(project, task.id).updated_at
  end

  test "a selected Task removed before a comment notification enters not-found detail", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    Taskman.Repo.delete!(task)

    send(view.pid, %Event{
      entity: :comment,
      operation: :created,
      project_id: project.id,
      task_id: task.id,
      entity_id: 1,
      fields: []
    })

    sync_view(view)
    assert view_assigns(view).editing.not_found?
    assert view_assigns(view).comments.task_id == nil
  end

  defp comment_ids(view) do
    view
    |> render()
    |> LazyHTML.from_fragment()
    |> LazyHTML.query("#task-comment-thread article")
    |> LazyHTML.attribute("id")
  end

  defp sync_view(view), do: _ = :sys.get_state(view.pid)

  defp view_assigns(view) do
    %{socket: socket} = :sys.get_state(view.pid)
    socket.assigns
  end
end
