defmodule TaskmanWeb.ProjectLive.ActivityTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import TaskmanWeb.DescriptionEditorHelpers
  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Ecto.Adapters.SQL
  alias Taskman.ChangeNotifications.Event
  alias Taskman.{Repo, Tasks}
  alias Taskman.Tasks.Activity

  setup %{conn: conn} do
    user = user_fixture()
    project = project_fixture(%{})
    task = task_fixture(project, %{priority: :low})
    {:ok, conn: log_in_user(conn, user), user: user, project: project, task: task}
  end

  test "fresh Tasks show creation with actor and local-time metadata", context do
    %{conn: conn, project: project, task: task} = context
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    entry = event(project, task, "task_created")
    assert has_element?(view, "#task-#{entry.id}", "Task created")
    assert has_element?(view, "#task-#{entry.id}", entry.change.actor.login)
    assert has_element?(view, "#task-#{entry.id}-time[datetime][phx-hook]")
    refute has_element?(view, "#task-activity-empty")
  end

  test "ordinary autosave refreshes the initiating and subscribed windows", context do
    %{conn: conn, project: project, task: task, user: user} = context
    {:ok, author, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    {:ok, subscriber, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    author
    |> form("#task-form", task: %{priority: "high"})
    |> render_captured_change(%{"_target" => ["task", "priority"]})

    entry = event(project, task, "field_changed")
    assert has_element?(author, "#task-#{entry.id}", "Priority changed from Low to High")
    assert has_element?(author, "#task-#{entry.id}", to_string(user.email))

    assert has_captured_element?(
             subscriber,
             "#task-#{entry.id}",
             "Priority changed from Low to High"
           )
  end

  test "consolidation reorders around comments and cancellation removes the row without losing draft or tab",
       context do
    %{conn: conn, project: project, task: task, user: user} = context
    {:ok, first} = Tasks.update_task(project, task, %{priority: :medium}, actor: user)
    entry = event(project, task, "field_changed")
    now = DateTime.utc_now()
    set_event_time(event(project, task, "task_created"), DateTime.add(now, -30, :second))
    set_event_time(entry, DateTime.add(now, -20, :second))
    {:ok, comment} = Tasks.create_comment(project, first, user, %{text: "Between edits"})

    Repo.update!(
      Ecto.Changeset.change(comment,
        created_at: now |> DateTime.add(-10, :second) |> DateTime.truncate(:second)
      )
    )

    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-#{entry.id} + #task-comment-#{comment.id}")
    view |> form("#task-comment-form", comment: %{text: "Keep my draft"}) |> render_change()
    view |> element("#task-sessions-tab") |> render_click()
    revision = state(view).activity.revision
    {:ok, second} = Tasks.update_task(project, first, %{priority: :high}, actor: user)
    assert has_captured_element?(view, "#task-comment-#{comment.id} + #task-#{entry.id}")
    assert has_element?(view, "#task-#{entry.id}", "Low to High")
    assert has_element?(view, "#task-comment-text", "Keep my draft")
    assert has_element?(view, "#task-sessions-tab[aria-selected='true']")
    assert state(view).activity.revision > revision
    {:ok, _} = Tasks.update_task(project, second, %{priority: :low}, actor: user)
    render_captured(view)
    refute has_element?(view, "#task-#{entry.id}")
    assert has_element?(view, "#task-comment-#{comment.id}")
    assert has_element?(view, "#task-comment-text", "Keep my draft")
    assert has_element?(view, "#task-sessions-tab[aria-selected='true']")
  end

  test "intermediate parent subscribers lose the consolidated child event", context do
    %{conn: conn, project: project, task: child, user: user} = context
    original = task_fixture(project, %{title: "Original parent"})
    intermediate = task_fixture(project, %{title: "Intermediate parent"})
    final = task_fixture(project, %{title: "Final parent"})
    child = Repo.update!(Ecto.Changeset.change(child, parent_task_id: original.id))
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{intermediate.id}")
    {:ok, moved} = Tasks.update_task(project, child, %{}, parent: intermediate, actor: user)
    entry = event(project, child, "field_changed")
    assert has_captured_element?(view, "#task-#{entry.id}", "Child added")

    assert has_element?(
             view,
             "#task-#{entry.id} a[href='/projects/#{project.id}/tasks/#{child.id}']",
             child.title
           )

    {:ok, _} = Tasks.update_task(project, moved, %{}, parent: final, actor: user)
    render_captured(view)
    refute has_element?(view, "#task-#{entry.id}")
  end

  test "cross-Project blocking invalidations refresh both endpoint feeds", context do
    %{conn: conn, project: project, task: blocker, user: user} = context
    other = project_fixture(%{name: "Other Project"})
    blocked = task_fixture(other, %{title: "Blocked Task"})
    {:ok, source, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{blocker.id}")
    {:ok, target, _} = live(conn, ~p"/projects/#{other.id}/tasks/#{blocked.id}")
    {:ok, _} = Tasks.add_block(project, blocker, blocked, actor: user)
    entry = event(project, blocker, "block_changed")
    assert has_captured_element?(source, "#task-#{entry.id}", "Blocks added")
    assert has_captured_element?(target, "#task-#{entry.id}", "Blocked by added")

    assert has_element?(
             source,
             "#task-#{entry.id} a[href='/projects/#{other.id}/tasks/#{blocked.id}']",
             blocked.title
           )

    {:ok, _} = Tasks.remove_block(project, blocker, blocked, actor: user)
    render_captured(source)
    render_captured(target)
    refute has_element?(source, "#task-#{entry.id}")
    refute has_element?(target, "#task-#{entry.id}")
  end

  test "activity invalidation joins pending description capture and preserves departure draft",
       context do
    %{conn: conn, project: project, task: task, user: user} = context
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: "Unposted comment"
    })

    pending = state(view).description.pending_capture
    {:ok, _} = Tasks.update_task(project, task, %{priority: :high}, actor: user)
    render(view)
    assert state(view).description.pending_capture.ref == pending.ref
    complete_description_capture(view)
    entry = event(project, task, "field_changed")
    assert has_element?(view, "#task-#{entry.id}", "Low to High")
    assert has_element?(view, "#task-comment-departure")
    view |> element("#task-comment-departure-go-back") |> render_click()
    assert has_element?(view, "#task-comment-text", "Unposted comment")
  end

  test "malformed or unrelated activity notifications do not reset the open feed", context do
    %{conn: conn, project: project, task: task} = context
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    revision = state(view).activity.revision

    for attrs <- [
          %{task_ids: [task.id], fields: [:title]},
          %{task_ids: ["bad"]},
          %{task_ids: [task.id + 1]},
          %{project_id: project.id + 1, task_ids: [task.id]}
        ] do
      send(
        view.pid,
        struct!(
          %Event{
            entity: :activity,
            operation: :invalidated,
            project_id: project.id,
            entity_id: task.id,
            task_ids: [task.id],
            fields: []
          },
          attrs
        )
      )
    end

    render(view)
    assert state(view).activity.revision == revision
    assert has_element?(view, "#task-comment-form")
  end

  test "Done confirmation refreshes the authoring feed only after acceptance", context do
    %{conn: conn, project: project, task: task} = context
    _child = task_fixture(project, %{}, parent: task)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    assert has_element?(view, "#task-done-warning")
    assert event(project, task, "field_changed") == nil
    view |> element("#confirm-task-done") |> render_captured_click()
    entry = event(project, task, "field_changed")
    assert has_element?(view, "#task-#{entry.id}", "Status changed from Pending to Done")
  end

  test "historical labels remain escaped without navigation to deleted targets or accounts",
       context do
    %{conn: conn, project: project, task: task} = context
    actor = user_fixture()
    parent = task_fixture(project, %{title: "<script>alert('parent')</script>"})
    {:ok, attached} = Tasks.update_task(project, task, %{}, parent: parent, actor: actor)
    {:ok, _} = Tasks.update_task(project, attached, %{}, parent: nil, actor: user_fixture())
    Repo.delete!(parent)
    SQL.query!(Repo, "DELETE FROM users WHERE id = $1", [Ecto.UUID.dump!(actor.id)])
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    {:ok, entries} = Tasks.list_activity(project, task)
    entry = Enum.find(entries, &(&1.change.field == "parent_task_id" && &1.change.after != nil))
    assert has_element?(view, "#task-#{entry.id}", "Deleted user")
    assert has_element?(view, "#task-#{entry.id}", "<script>alert('parent')</script>")
    refute has_element?(view, "#task-#{entry.id} a")
    refute has_element?(view, "#task-#{entry.id} script")
  end

  test "field transitions show historical title, location and due-date values", context do
    %{conn: conn, project: project, task: task, user: user} = context
    task_list = Taskman.ListsFixtures.list_fixture(project, %{name: "<Review>"})

    {:ok, changed} =
      Tasks.update_task(project, task, %{title: "<Renamed>", due_at: ~N[2026-11-10 14:30:00]},
        actor: user
      )

    {:ok, _} = Tasks.move_task(project, changed, task_list, actor: user)

    {:ok, view, _} =
      live(conn, ~p"/projects/#{project.id}/lists/#{task_list.id}/tasks/#{task.id}")

    {:ok, entries} = Tasks.list_activity(project, task)

    for {field, label} <- [
          {"title", "<Renamed>"},
          {"due_at", "Nov 10, 2026 at 14:30"},
          {"list_id", "<Review>"}
        ] do
      entry = Enum.find(entries, &(&1.change.field == field))
      assert has_element?(view, "#task-#{entry.id}", label)
    end
  end

  test "moving the open Task refreshes its initiating feed and retains composer state", context do
    %{conn: conn, project: project, task: task} = context
    destination = Taskman.ListsFixtures.list_fixture(project, %{name: "Destination"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    view |> form("#task-comment-form", comment: %{text: "Move with my draft"}) |> render_change()
    view |> element("#task-sessions-tab") |> render_click()
    view |> element("#move-task-detail-button-#{task.id}") |> render_captured_click()
    view |> element("#move-task-search-#{task.id}") |> render_captured_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_captured_click()
    view |> element("#move-task-submit-#{task.id}") |> render_captured_click()
    assert_patch(view, ~p"/projects/#{project.id}/lists/#{destination.id}/tasks/#{task.id}")
    entry = event(project, task, "field_changed")
    assert has_element?(view, "#task-#{entry.id}", "Destination")
    assert has_element?(view, "#task-comment-text", "Move with my draft")
    assert has_element?(view, "#task-sessions-tab[aria-selected='true']")
  end

  defp event(project, task, type) do
    {:ok, entries} = Tasks.list_activity(project, task)
    Enum.find(entries, &(&1.change.type == type))
  end

  defp set_event_time(entry, time) do
    "event-" <> id = entry.id

    Repo.get!(Activity.Event, id)
    |> Ecto.Changeset.change(first_changed_at: time, last_changed_at: time)
    |> Repo.update!()
  end

  defp state(view), do: :sys.get_state(view.pid).socket.assigns
end
