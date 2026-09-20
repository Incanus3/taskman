defmodule TaskmanWeb.ProjectLive.RecoveryTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks

  setup %{conn: conn} do
    previous = Application.get_env(:taskman, :task_autosave_delay_ms)
    Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)

    on_exit(fn ->
      case previous do
        nil -> Application.delete_env(:taskman, :task_autosave_delay_ms)
        value -> Application.put_env(:taskman, :task_autosave_delay_ms, value)
      end
    end)

    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "an unresolved dirty detail enters exceptional recovery after List loss", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project)
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Unsaved"})
    |> render_change(%{"_target" => ["task", "title"]})

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert has_element?(view, "#task-recovery #task-title[value='Unsaved']")
    assert has_element?(view, "#task-recovery-title", "This task is no longer available")

    assert has_element?(
             view,
             "#task-recovery-explanation",
             "Copy your unsaved input or discard it. This recovery state is temporary. Reloading, reconnecting, or leaving Taskman may lose it."
           )

    assert has_element?(view, "#task-recovery-copy", "Copy input")
    assert has_element?(view, "#task-recovery-discard", "Discard input")
    refute has_element?(view, "#task-recovery-resume")
    refute has_element?(view, "#task-recovery-source")
    refute has_element?(view, "#task-recovery h2:not(#task-recovery-title)")
    refute has_element?(view, "#task-recovery h3")
    refute has_element?(view, "#task-recovery [id$='-save-status']")
    refute has_element?(view, "#task-recovery-destination")
    refute has_element?(view, "#task-recovery-form")
  end

  test "detail recovery makes only the workspace background natively inert", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project)
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Unsaved"})
    |> render_change(%{"_target" => ["task", "title"]})

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert has_element?(view, "#workspace-content[inert]")
    refute has_element?(view, "#task-modal[inert]")
  end

  test "a stale move event stays inert after an inactive List backdrop disappears", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}")

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    render_hook(view, "open_move_task", %{"task-id" => Integer.to_string(task.id)})

    refute has_element?(view, "#move-task-#{task.id}")
  end

  test "detail recovery retains copy and focus then discards to the Project root", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Unsaved detail"})
    |> render_change(%{"_target" => ["task", "title"]})

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert has_element?(view, "#task-recovery[data-copy-value*='Unsaved detail']")

    assert has_element?(
             view,
             "#task-modal[phx-mounted*='focus'] #task-modal-content[tabindex='-1']"
           )

    view |> element("#task-recovery-discard") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#task-recovery")
  end

  test "stale detail events cannot alter a captured recovery snapshot", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Unsaved detail"})
    |> render_change(%{"_target" => ["task", "title"]})

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)
    captured = view_assigns(view).recovery

    for {event, params} <- [
          {"validate_task", %{"task" => %{"title" => "Replace"}}},
          {"submit_task_edit", %{}},
          {"select_task_parent", %{"parent-id" => "1"}},
          {"open_move_task", %{"task-id" => Integer.to_string(task.id)}}
        ] do
      render_hook(view, event, params)
      assert view_assigns(view).recovery == captured
    end

    assert Tasks.get_task_for_project(project, task.id) == nil
    assert has_element?(view, "#task-recovery #task-title[value='Unsaved detail']")
  end

  defp list_event(project_id, list_id) do
    %Event{
      entity: :list,
      operation: :updated,
      project_id: project_id,
      entity_id: list_id,
      lock_version: nil,
      fields: [:name]
    }
  end

  defp view_assigns(view) do
    %{socket: socket} = :sys.get_state(view.pid)
    socket.assigns
  end
end
