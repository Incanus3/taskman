defmodule TaskmanWeb.ProjectLive.CreationLocationTest do
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

  test "creation exposes an explicit location without changing its backdrop", %{conn: conn} do
    project = project_fixture(%{})
    current = list_fixture(project, nil, %{name: "Current"})
    other = list_fixture(project, nil, %{name: "Other"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{current.id}/tasks/new")

    assert has_element?(view, "#task-form #task-location")
    assert has_element?(view, "#task-location option[value='project']", "Project #{project.name}")
    assert has_element?(view, "#task-location option[value='list:#{current.id}'][selected]")

    view
    |> form("#task-form", location: "list:#{other.id}", task: %{title: "Elsewhere"})
    |> render_change()

    assert has_element?(view, "#task-location option[value='list:#{other.id}'][selected]")
  end

  test "Project-root and Add-subtask defaults keep location and parent independent", %{conn: conn} do
    project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Tasks"})
    other = list_fixture(project, nil, %{name: "Other"})
    parent = task_fixture(project, task_list, %{title: "Parent"})

    {:ok, root_view, _} = live(conn, ~p"/projects/#{project.id}/tasks/new")
    assert has_element?(root_view, "#task-location option[value='project'][selected]")

    {:ok, view, _} =
      live(
        conn,
        ~p"/projects/#{project.id}/lists/#{task_list.id}/tasks/new?parent_task_id=#{parent.id}"
      )

    assert has_element?(view, "#task-location option[value='list:#{task_list.id}'][selected]")
    assert has_element?(view, "#task-parent-trigger", "Parent")

    view
    |> form("#task-form", location: "list:#{other.id}", task: %{title: "Child"})
    |> render_change()

    assert has_element?(view, "#task-location option[value='list:#{other.id}'][selected]")
    assert has_element?(view, "#task-parent-trigger", "Parent")

    view |> element("#task-parent-trigger") |> render_click()
    view |> element("#task-parent-clear") |> render_click()

    assert has_element?(view, "#task-location option[value='list:#{other.id}'][selected]")
  end

  test "List loss keeps creation editable and requires another location", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/new")

    view
    |> form("#task-form",
      location: "list:#{lost.id}",
      task: %{
        title: "Keep this",
        description: "Still editable"
      }
    )
    |> render_change()

    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert has_element?(view, "#task-form #task-title[value='Keep this']")
    assert has_element?(view, "#task-description", "Still editable")

    assert has_element?(
             view,
             "#task-location option[value='list:#{lost.id}'][disabled]",
             "List Lost"
           )

    assert has_element?(view, "#task-location[aria-invalid='true']")
    assert has_element?(view, "#task-location-error", "This List is no longer available")
    assert has_element?(view, "#create-task[disabled]")
    refute has_element?(view, "#task-recovery")

    view
    |> form("#task-form",
      location: "list:#{destination.id}",
      task: %{
        title: "Keep this",
        description: "Edited after loss"
      }
    )
    |> render_change()

    refute has_element?(view, "#task-location-error")
    assert has_element?(view, "#create-task:not([disabled])")
  end

  test "repeated List loss preserves an ordinary draft with its selected parent", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    parent_list = list_fixture(project, nil, %{name: "Parent List"})
    parent = task_fixture(project, parent_list, %{title: "Parent"})

    {:ok, view, _} =
      live(
        conn,
        ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/new?parent_task_id=#{parent.id}"
      )

    view
    |> form("#task-form", task: %{title: "Keep this", description: "Still selected"})
    |> render_change()

    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert has_element?(view, "#task-form #task-title[value='Keep this']")
    assert has_element?(view, "#task-description", "Still selected")
    assert has_element?(view, "#task-parent-trigger", "Parent")
    assert view_assigns(view).task_parent_picker.selected_parent.id == parent.id
    refute has_element?(view, "#task-recovery")
  end

  test "invalid submitted locations retain the ordinary form without creating a Task", %{
    conn: conn
  } do
    project = project_fixture(%{})
    current = list_fixture(project)
    foreign = list_fixture(project_fixture(%{}))
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{current.id}/tasks/new")

    render_hook(view, "save_task", %{
      "location" => "list:#{foreign.id}",
      "task" => %{"title" => "Keep this"}
    })

    assert has_element?(view, "#task-form #task-title[value='Keep this']")
    assert has_element?(view, "#task-location[aria-invalid='true']")
    assert has_element?(view, "#task-location-error", "This List is no longer available")
    assert Taskman.Tasks.list_tasks_for_location(project, current) == {:ok, []}
  end

  test "malformed canonical locations are rejected without writing a Task", %{conn: conn} do
    project = project_fixture(%{})
    current = list_fixture(project)

    for location <- ["list:0", "list:01", "list:not-an-id", "unknown"] do
      {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{current.id}/tasks/new")

      render_hook(view, "save_task", %{
        "location" => location,
        "task" => %{"title" => "Keep this"}
      })

      assert has_element?(view, "#task-form #task-title[value='Keep this']")
      assert has_element?(view, "#task-location[aria-invalid='true']")
      assert Tasks.list_tasks_for_project(project) == []
    end
  end

  test "a location deleted between validation and submission keeps the ordinary draft", %{
    conn: conn
  } do
    project = project_fixture(%{})
    current = list_fixture(project, nil, %{name: "Current"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{current.id}/tasks/new")

    view
    |> form("#task-form", location: "list:#{destination.id}", task: %{title: "Keep this"})
    |> render_change()

    Taskman.Repo.delete!(destination)

    view
    |> form("#task-form", location: "list:#{destination.id}", task: %{title: "Keep this"})
    |> render_submit()

    assert has_element?(view, "#task-form #task-title[value='Keep this']")
    assert has_element?(view, "#task-location[aria-invalid='true']")
    assert Tasks.list_tasks_for_project(project) == []
  end

  test "creation navigation follows direct and transitive-descendant location visibility", %{
    conn: conn
  } do
    project = project_fixture(%{})
    current = list_fixture(project, nil, %{name: "Current"})
    child = list_fixture(project, current, %{name: "Child"})
    grandchild = list_fixture(project, child, %{name: "Grandchild"})

    {:ok, direct_view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{current.id}/tasks/new")

    render_hook(direct_view, "save_task", %{
      "location" => "list:#{current.id}",
      "task" => %{"title" => "Direct"}
    })

    assert_patch(direct_view, ~p"/projects/#{project.id}/lists/#{current.id}")

    {:ok, descendants_view, _} =
      live(conn, ~p"/projects/#{project.id}/lists/#{current.id}/tasks/new?include_children=true")

    render_hook(descendants_view, "save_task", %{
      "location" => "list:#{grandchild.id}",
      "task" => %{"title" => "Grandchild visible"}
    })

    assert_patch(
      descendants_view,
      ~p"/projects/#{project.id}/lists/#{current.id}"
    )

    {:ok, narrow_view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{current.id}/tasks/new")

    render_hook(narrow_view, "save_task", %{
      "location" => "list:#{grandchild.id}",
      "task" => %{"title" => "Grandchild hidden"}
    })

    assert_patch(narrow_view, ~p"/projects/#{project.id}/lists/#{grandchild.id}")
  end

  test "Project-root creation navigation ignores status filters", %{conn: conn} do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Destination"})
    {:ok, root_view, _} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    root_view |> element("#task-status-filter-button") |> render_click()

    root_view
    |> form("#task-status-filter-form", status_filter: %{statuses: ["done"]})
    |> render_change()

    render_hook(root_view, "save_task", %{
      "location" => "project",
      "task" => %{"title" => "Root task", "status" => "pending"}
    })

    assert_patch(root_view, ~p"/projects/#{project.id}")

    {:ok, hidden_list_view, _} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    render_hook(hidden_list_view, "save_task", %{
      "location" => "list:#{destination.id}",
      "task" => %{"title" => "List task"}
    })

    assert_patch(hidden_list_view, ~p"/projects/#{project.id}/lists/#{destination.id}")

    {:ok, visible_list_view, _} =
      live(conn, ~p"/projects/#{project.id}/tasks/new?include_children=true")

    render_hook(visible_list_view, "save_task", %{
      "location" => "list:#{destination.id}",
      "task" => %{"title" => "Visible list task"}
    })

    assert_patch(visible_list_view, ~p"/projects/#{project.id}")
  end

  test "unrelated and missing backdrops navigate to the selected location", %{conn: conn} do
    project = project_fixture(%{})
    current = list_fixture(project, nil, %{name: "Current"})
    unrelated = list_fixture(project, nil, %{name: "Unrelated"})

    {:ok, unrelated_view, _} =
      live(conn, ~p"/projects/#{project.id}/lists/#{current.id}/tasks/new?include_children=true")

    render_hook(unrelated_view, "save_task", %{
      "location" => "list:#{unrelated.id}",
      "task" => %{"title" => "Elsewhere"}
    })

    assert_patch(
      unrelated_view,
      ~p"/projects/#{project.id}/lists/#{unrelated.id}"
    )

    lost = list_fixture(project, nil, %{name: "Lost"})
    {:ok, missing_view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/new")

    Taskman.Repo.delete!(lost)
    send(missing_view.pid, list_event(project.id, lost.id))
    render(missing_view)

    render_hook(missing_view, "save_task", %{
      "location" => "list:#{unrelated.id}",
      "task" => %{"title" => "After loss"}
    })

    assert_patch(missing_view, ~p"/projects/#{project.id}/lists/#{unrelated.id}")
  end

  test "ordinary creation ignores hidden movement events", %{conn: conn} do
    project = project_fixture(%{})
    current = list_fixture(project)
    task = task_fixture(project, current, %{title: "Background Task"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{current.id}/tasks/new")

    render_hook(view, "open_move_task", %{"task-id" => Integer.to_string(task.id)})

    refute view_assigns(view).task_move.active_task
  end

  test "cancelling over a disappeared backdrop returns to the Project root", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/new")

    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)
    view |> element("#cancel-task") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}")
  end

  test "closing over a disappeared backdrop returns to the Project root", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/new")

    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    view |> element("#task-modal-close") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}")
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
