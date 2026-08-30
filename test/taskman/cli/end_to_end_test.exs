defmodule Taskman.CLI.EndToEndTest do
  use Taskman.DataCase, async: false

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
end
