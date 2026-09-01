defmodule Taskman.CLI.EndToEndTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  test "creates and inspects a Project through the loopback API" do
    server =
      start_supervised!(
        {Bandit,
         plug: TaskmanWeb.Endpoint, scheme: :http, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)
    api_url = "http://127.0.0.1:#{port}"

    create =
      Taskman.CLI.run([
        "projects",
        "create",
        "--name",
        "HTTP smoke",
        "--directory",
        File.cwd!(),
        "--api-url",
        api_url,
        "--json"
      ])

    assert create.status == 0
    assert create.stderr == ""
    assert String.ends_with?(create.stdout, "\n")

    assert %{
             "data" => %{
               "id" => id,
               "name" => "HTTP smoke",
               "primary_directory" => directory
             }
           } = Jason.decode!(create.stdout)

    assert directory == File.cwd!()

    show =
      Taskman.CLI.run([
        "projects",
        "show",
        Integer.to_string(id),
        "--api-url",
        api_url,
        "--json"
      ])

    assert show.status == 0
    assert show.stderr == ""
    assert String.ends_with?(show.stdout, "\n")

    assert Jason.decode!(show.stdout) == %{
             "data" => %{
               "id" => id,
               "name" => "HTTP smoke",
               "primary_directory" => File.cwd!()
             }
           }
  end

  test "updates Task parentage and inspects hierarchy through the loopback API" do
    server =
      start_supervised!(
        {Bandit,
         plug: TaskmanWeb.Endpoint, scheme: :http, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)
    api_url = "http://127.0.0.1:#{port}"

    project =
      Taskman.CLI.run([
        "projects",
        "create",
        "--name",
        "Hierarchy smoke",
        "--directory",
        File.cwd!(),
        "--api-url",
        api_url,
        "--json"
      ])

    assert %{"data" => %{"id" => project_id}} = Jason.decode!(project.stdout)

    parent =
      Taskman.CLI.run([
        "tasks",
        "create",
        "--project",
        Integer.to_string(project_id),
        "--title",
        "Parent",
        "--api-url",
        api_url,
        "--json"
      ])

    assert %{"data" => %{"id" => parent_id, "parent_task_id" => nil}} =
             Jason.decode!(parent.stdout)

    child =
      Taskman.CLI.run([
        "tasks",
        "create",
        "--project",
        Integer.to_string(project_id),
        "--title",
        "Child",
        "--parent",
        Integer.to_string(parent_id),
        "--api-url",
        api_url,
        "--json"
      ])

    assert %{"data" => %{"id" => child_id, "parent_task_id" => ^parent_id}} =
             Jason.decode!(child.stdout)

    hierarchy =
      Taskman.CLI.run([
        "tasks",
        "hierarchy",
        "--project",
        Integer.to_string(project_id),
        Integer.to_string(child_id),
        "--api-url",
        api_url,
        "--json"
      ])

    assert hierarchy.status == 0

    assert %{
             "data" => %{
               "selected_task_id" => ^child_id,
               "root" => %{
                 "task" => %{"id" => ^parent_id, "parent_task_id" => nil},
                 "children" => [%{"task" => %{"id" => ^child_id, "parent_task_id" => ^parent_id}}]
               }
             }
           } = Jason.decode!(hierarchy.stdout)

    clear_parent =
      Taskman.CLI.run([
        "tasks",
        "update",
        "--project",
        Integer.to_string(project_id),
        Integer.to_string(child_id),
        "--no-parent",
        "--api-url",
        api_url,
        "--json"
      ])

    assert %{"data" => %{"parent_task_id" => nil}} = Jason.decode!(clear_parent.stdout)
  end

  test "CLI writes refresh connected Project views through the loopback API", %{conn: conn} do
    server =
      start_supervised!(
        {Bandit,
         plug: TaskmanWeb.Endpoint, scheme: :http, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)
    api_url = "http://127.0.0.1:#{port}"
    project = project_fixture(%{name: "Browser Project"})
    parent = task_fixture(project, %{title: "CLI parent"})
    project_path = ~p"/projects/#{project.id}"

    {:ok, project_view, _html} = live(conn, project_path)

    project_create =
      Taskman.CLI.run([
        "projects",
        "create",
        "--name",
        "CLI Project",
        "--directory",
        File.cwd!(),
        "--api-url",
        api_url,
        "--json"
      ])

    assert project_create.status == 0
    assert project_create.stderr == ""
    assert String.ends_with?(project_create.stdout, "\n")

    assert %{"data" => %{"id" => created_project_id, "name" => "CLI Project"}} =
             Jason.decode!(project_create.stdout)

    sync_view(project_view)
    assert has_element?(project_view, "#project-#{created_project_id}", "CLI Project")
    refute_patched(project_view, project_path)

    list_create =
      Taskman.CLI.run([
        "lists",
        "create",
        "--project",
        Integer.to_string(project.id),
        "--name",
        "CLI List",
        "--api-url",
        api_url,
        "--json"
      ])

    assert list_create.status == 0
    assert list_create.stderr == ""
    assert String.ends_with?(list_create.stdout, "\n")

    assert %{
             "data" => %{
               "id" => list_id,
               "project_id" => project_id,
               "parent_list_id" => nil,
               "name" => "CLI List",
               "path" => ["CLI List"]
             }
           } = Jason.decode!(list_create.stdout)

    assert project_id == project.id

    sync_view(project_view)
    project_view |> element("#toggle-project-#{project.id}") |> render_click()
    assert has_element?(project_view, "#list-#{list_id}", "CLI List")
    refute_patched(project_view, project_path)

    list_rename =
      Taskman.CLI.run([
        "lists",
        "rename",
        "--project",
        Integer.to_string(project.id),
        Integer.to_string(list_id),
        "--name",
        "CLI Renamed List",
        "--api-url",
        api_url,
        "--json"
      ])

    assert list_rename.status == 0
    assert list_rename.stderr == ""
    assert String.ends_with?(list_rename.stdout, "\n")

    assert %{"data" => %{"id" => ^list_id, "name" => "CLI Renamed List"}} =
             Jason.decode!(list_rename.stdout)

    sync_view(project_view)
    assert has_element?(project_view, "#list-#{list_id}", "CLI Renamed List")
    refute_patched(project_view, project_path)

    list_path = ~p"/projects/#{project.id}/lists/#{list_id}"
    {:ok, list_view, _html} = live(conn, list_path)

    task_create =
      Taskman.CLI.run([
        "tasks",
        "create",
        "--project",
        Integer.to_string(project.id),
        "--list",
        Integer.to_string(list_id),
        "--title",
        "CLI Task",
        "--api-url",
        api_url,
        "--json"
      ])

    assert task_create.status == 0
    assert task_create.stderr == ""
    assert String.ends_with?(task_create.stdout, "\n")

    assert %{
             "data" => %{
               "id" => task_id,
               "project_id" => ^project_id,
               "list_id" => ^list_id,
               "parent_task_id" => nil,
               "title" => "CLI Task",
               "location" => %{"kind" => "list", "list_id" => ^list_id}
             }
           } = Jason.decode!(task_create.stdout)

    sync_view(list_view)
    assert has_element?(list_view, "#task-#{task_id}", "CLI Task")
    refute_patched(list_view, list_path)

    detail_path = ~p"/projects/#{project.id}/lists/#{list_id}/tasks/#{task_id}"
    {:ok, detail, _html} = live(conn, detail_path)

    task_update =
      Taskman.CLI.run([
        "tasks",
        "update",
        "--project",
        Integer.to_string(project.id),
        Integer.to_string(task_id),
        "--title",
        "CLI Updated Task",
        "--api-url",
        api_url,
        "--json"
      ])

    assert task_update.status == 0
    assert task_update.stderr == ""
    assert String.ends_with?(task_update.stdout, "\n")

    assert %{"data" => %{"id" => ^task_id, "title" => "CLI Updated Task"}} =
             Jason.decode!(task_update.stdout)

    sync_view(detail)
    assert has_element?(detail, "#task-modal")
    assert has_element?(detail, "#task-title[value='CLI Updated Task']")
    refute_patched(detail, detail_path)

    parent_change =
      Taskman.CLI.run([
        "tasks",
        "update",
        "--project",
        Integer.to_string(project.id),
        Integer.to_string(task_id),
        "--parent",
        Integer.to_string(parent.id),
        "--api-url",
        api_url,
        "--json"
      ])

    assert parent_change.status == 0
    assert parent_change.stderr == ""
    assert String.ends_with?(parent_change.stdout, "\n")

    assert %{"data" => %{"id" => ^task_id, "parent_task_id" => parent_id}} =
             Jason.decode!(parent_change.stdout)

    assert parent_id == parent.id

    sync_view(detail)
    assert has_element?(detail, "#task-modal")
    assert has_element?(detail, "#task-parent-trigger", "CLI parent")
    assert has_element?(detail, "#task-hierarchy-node-#{parent.id}")
    assert has_element?(detail, "#task-hierarchy-link-#{task_id}[aria-current='true']")
    refute_patched(detail, detail_path)

    task_move =
      Taskman.CLI.run([
        "tasks",
        "move",
        "--project",
        Integer.to_string(project.id),
        Integer.to_string(task_id),
        "--to-project-root",
        "--api-url",
        api_url,
        "--json"
      ])

    assert task_move.status == 0
    assert task_move.stderr == ""
    assert String.ends_with?(task_move.stdout, "\n")

    assert %{
             "data" => %{
               "id" => ^task_id,
               "project_id" => ^project_id,
               "list_id" => nil,
               "parent_task_id" => ^parent_id,
               "location" => %{"kind" => "project", "list_id" => nil, "path" => []}
             }
           } = Jason.decode!(task_move.stdout)

    sync_view(detail)
    assert has_element?(detail, "#task-modal")
    assert has_element?(detail, "#task-title[value='CLI Updated Task']")
    assert has_element?(detail, "#task-hierarchy-node-#{parent.id}")
    refute has_element?(detail, "#task-#{task_id}")
    refute_patched(detail, detail_path)
  end

  defp sync_view(view), do: _ = :sys.get_state(view.pid)
end
