defmodule TaskmanWeb.ProjectLive.MovementRecoveryTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "destination loss keeps the row popover and its query while disabling submission", %{
    conn: conn
  } do
    project = project_fixture(%{})
    source = list_fixture(project, nil, %{name: "Source"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, source, %{title: "Move me"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{source.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()

    Taskman.Repo.delete!(destination)
    send(view.pid, list_event(project.id, destination.id))
    render(view)

    assert has_element?(view, "#move-task-#{task.id}")
    assert has_element?(view, "#move-task-search-#{task.id}[value='Destination']")

    assert has_element?(
             view,
             "#move-task-error-#{task.id}",
             "That destination is no longer available. Choose another destination."
           )

    assert has_element?(view, "#move-task-submit-#{task.id}[disabled]")
    assert Tasks.get_task_for_project(project, task.id).list_id == source.id
  end

  test "the next move interaction detects destination loss when no notification arrived", %{
    conn: conn
  } do
    project = project_fixture(%{})
    source = list_fixture(project, nil, %{name: "Source"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, source, %{title: "Move me"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{source.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()
    Taskman.Repo.delete!(destination)

    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert view_assigns(view).task_move.error ==
             "That destination is no longer available. Choose another destination."

    assert has_element?(view, "#move-task-search-#{task.id}[value='Destination']")

    assert has_element?(
             view,
             "#move-task-error-#{task.id}",
             "That destination is no longer available. Choose another destination."
           )

    assert has_element?(view, "#move-task-submit-#{task.id}[disabled]")
    assert Tasks.get_task_for_project(project, task.id).list_id == source.id
  end

  test "lost row anchor relocates the active move to fresh detail without moving the Task", %{
    conn: conn
  } do
    project = project_fixture(%{})
    source = list_fixture(project, nil, %{name: "Source"})
    actual = list_fixture(project, nil, %{name: "Actual"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, source, %{title: "Move me"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{source.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()
    assert {:ok, moved} = Tasks.move_task(project, task, actual)

    send(view.pid, task_event(moved))
    render(view)

    assert_patch(view, ~p"/projects/#{project.id}/lists/#{actual.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-modal #move-task-#{task.id}")
    assert has_element?(view, "#move-task-search-#{task.id}[value='Destination']")
    assert Tasks.get_task_for_project(project, task.id).list_id == actual.id
    refute has_element?(view, "#move-task-current-location-#{task.id}")

    view |> element("#cancel-move-task-#{task.id}") |> render_click()
    assert has_element?(view, "#task-modal #task-form")
    refute has_element?(view, "#move-task-#{task.id}")

    view |> element("#task-modal-close") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}/lists/#{actual.id}")
  end

  test "a move into the selected destination explains why submission is disabled after source loss",
       %{conn: conn} do
    project = project_fixture(%{})
    source = list_fixture(project, nil, %{name: "Source"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, source, %{title: "Move me"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{source.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()
    refute has_element?(view, "#move-task-current-location-#{task.id}")

    task
    |> Ecto.Changeset.change(%{list_id: destination.id})
    |> Taskman.Repo.update!()

    Taskman.Repo.delete!(source)
    send(view.pid, list_event(project.id, source.id))
    render(view)

    assert_patch(view, ~p"/projects/#{project.id}/lists/#{destination.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-modal #move-task-#{task.id}")
    assert has_element?(view, "#move-task-search-#{task.id}[value='Destination']")

    assert has_element?(
             view,
             "#move-task-current-location-#{task.id}[role='status']",
             "This Task is already at the selected destination."
           )

    assert has_element?(view, "#move-task-submit-#{task.id}[disabled]")
    assert Tasks.get_task_for_project(project, task.id).list_id == destination.id
  end

  test "selected List loss relocates a surviving row move onto the Project-root detail backdrop",
       %{
         conn: conn
       } do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, lost, %{title: "Move me"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()

    task
    |> Ecto.Changeset.change(%{list_id: nil})
    |> Taskman.Repo.update!()

    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-modal #move-task-#{task.id}")
    assert has_element?(view, "#move-task-search-#{task.id}[value='Destination']")
    assert Tasks.get_task_for_project(project, task.id).list_id == nil
  end

  test "an included descendant row remains anchored after an external move", %{conn: conn} do
    project = project_fixture(%{})
    parent = list_fixture(project, nil, %{name: "Parent"})
    first = list_fixture(project, parent, %{name: "First"})
    second = list_fixture(project, parent, %{name: "Second"})
    task = task_fixture(project, first, %{title: "Move me"})

    {:ok, view, _} =
      live(conn, ~p"/projects/#{project.id}/lists/#{parent.id}?include_children=true")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_keyup(%{"value" => "Second"})
    assert {:ok, moved} = Tasks.move_task(project, task, second)
    send(view.pid, task_event(moved))
    render(view)

    refute_patched(
      view,
      ~p"/projects/#{project.id}/lists/#{second.id}/tasks/#{task.id}?include_children=true"
    )

    assert has_element?(view, "#tasks-#{task.id} #move-task-#{task.id}")
    assert has_element?(view, "#move-task-search-#{task.id}[value='Second']")
  end

  test "missing moving Task clears the row move and shows the standard error flash", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Move me"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    Taskman.Repo.delete!(task)
    send(view.pid, task_event(task))
    render(view)

    refute has_element?(view, "#move-task-#{task.id}")
    assert has_element?(view, "#flash-error", "This Task is no longer available.")
    assert has_element?(view, "#location-heading", project.name)
  end

  test "missing moving Task and selected List loss patches to the Project root", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    task = task_fixture(project, lost, %{title: "Move me"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#move-task-#{task.id}")
    assert has_element?(view, "#flash-error", "This Task is no longer available.")
    assert has_element?(view, "#location-heading", project.name)
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

  defp task_event(task) do
    %Event{
      entity: :task,
      operation: :moved,
      project_id: task.project_id,
      entity_id: task.id,
      lock_version: task.lock_version,
      fields: [:list_id]
    }
  end

  defp view_assigns(view) do
    %{socket: socket} = :sys.get_state(view.pid)
    socket.assigns
  end
end
