defmodule TaskmanWeb.ProjectLiveExternalUpdatesTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  test "controller Project and List writes refresh connected navigation without navigation", %{
    conn: conn
  } do
    {:ok, view, _html} = live(conn, ~p"/")

    project_response =
      post_request("/api/v1/projects", %{
        "project" => %{"name" => "Controller Project", "primary_directory" => File.cwd!()}
      })

    assert %{"data" => %{"id" => project_id, "name" => "Controller Project"}} =
             json_response(project_response, 201)

    sync_view(view)
    assert has_element?(view, "#project-#{project_id}", "Controller Project")
    refute_patched(view, ~p"/")

    project_path = ~p"/projects/#{project_id}"
    {:ok, view, _html} = live(conn, project_path)

    list_response =
      post_request("/api/v1/projects/#{project_id}/lists", %{
        "list" => %{"name" => "Controller List"}
      })

    assert %{
             "data" => %{
               "id" => list_id,
               "project_id" => ^project_id,
               "parent_list_id" => nil,
               "name" => "Controller List",
               "path" => ["Controller List"]
             }
           } = json_response(list_response, 201)

    sync_view(view)
    view |> element("#toggle-project-#{project_id}") |> render_click()
    assert has_element?(view, "#list-#{list_id}", "Controller List")
    refute_patched(view, project_path)

    rename_response =
      patch_request("/api/v1/projects/#{project_id}/lists/#{list_id}", %{
        "list" => %{"name" => "Renamed by controller"}
      })

    assert %{
             "data" => %{
               "id" => ^list_id,
               "project_id" => ^project_id,
               "name" => "Renamed by controller",
               "path" => ["Renamed by controller"]
             }
           } = json_response(rename_response, 200)

    sync_view(view)
    assert has_element?(view, "#list-#{list_id}", "Renamed by controller")
    refute_patched(view, project_path)
  end

  test "controller Task writes refresh a selected detail, hierarchy, and List membership without navigation",
       %{
         conn: conn
       } do
    project = project_fixture(%{})
    planning = list_fixture(project, %{name: "Planning"})
    archive = list_fixture(project, %{name: "Archive"})
    parent = task_fixture(project, %{title: "Controller parent"})
    list_path = ~p"/projects/#{project.id}/lists/#{planning.id}"

    {:ok, view, _html} = live(conn, list_path)

    create_response =
      post_request("/api/v1/projects/#{project.id}/tasks", %{
        "task" => %{"title" => "Created by controller", "list_id" => planning.id}
      })

    assert %{
             "data" => %{
               "id" => task_id,
               "project_id" => project_id,
               "list_id" => planning_id,
               "parent_task_id" => nil,
               "title" => "Created by controller",
               "location" => %{"kind" => "list", "list_id" => location_list_id}
             }
           } = json_response(create_response, 201)

    assert project_id == project.id
    assert planning_id == planning.id
    assert location_list_id == planning.id

    sync_view(view)
    assert has_element?(view, "#task-#{task_id}", "Created by controller")
    refute_patched(view, list_path)

    detail_path = ~p"/projects/#{project.id}/lists/#{planning.id}/tasks/#{task_id}"
    {:ok, detail, _html} = live(conn, detail_path)

    update_response =
      patch_request("/api/v1/projects/#{project.id}/tasks/#{task_id}", %{
        "task" => %{"title" => "Updated by controller"}
      })

    assert %{
             "data" => %{
               "id" => ^task_id,
               "project_id" => ^project_id,
               "list_id" => ^planning_id,
               "parent_task_id" => nil,
               "title" => "Updated by controller"
             }
           } = json_response(update_response, 200)

    sync_view(detail)
    assert has_element?(detail, "#task-modal")
    assert has_element?(detail, "#task-title[value='Updated by controller']")
    refute_patched(detail, detail_path)

    parent_response =
      patch_request("/api/v1/projects/#{project.id}/tasks/#{task_id}", %{
        "task" => %{"parent_task_id" => parent.id}
      })

    assert %{
             "data" => %{
               "id" => ^task_id,
               "parent_task_id" => parent_id,
               "title" => "Updated by controller"
             }
           } = json_response(parent_response, 200)

    assert parent_id == parent.id

    sync_view(detail)
    assert has_element?(detail, "#task-modal")
    assert has_element?(detail, "#task-parent-trigger", "Controller parent")
    assert has_element?(detail, "#task-hierarchy-node-#{parent.id}")
    assert has_element?(detail, "#task-hierarchy-link-#{task_id}[aria-current='true']")
    refute_patched(detail, detail_path)

    move_response =
      post_request("/api/v1/projects/#{project.id}/tasks/#{task_id}/move", %{
        "destination" => %{"list_id" => archive.id}
      })

    assert %{
             "data" => %{
               "id" => ^task_id,
               "project_id" => ^project_id,
               "list_id" => archive_id,
               "parent_task_id" => ^parent_id,
               "location" => %{"kind" => "list", "list_id" => location_list_id}
             }
           } = json_response(move_response, 200)

    assert archive_id == archive.id
    assert location_list_id == archive.id

    sync_view(detail)
    assert has_element?(detail, "#task-modal")
    assert has_element?(detail, "#task-title[value='Updated by controller']")
    assert has_element?(detail, "#task-hierarchy-node-#{parent.id}")
    refute has_element?(detail, "#task-#{task_id}")
    refute_patched(detail, detail_path)
  end

  defp post_request(path, params) do
    Task.async(fn -> build_conn() |> post(path, params) end)
    |> Task.await()
  end

  defp patch_request(path, params) do
    Task.async(fn -> build_conn() |> patch(path, params) end)
    |> Task.await()
  end

  defp sync_view(view), do: _ = :sys.get_state(view.pid)
end
