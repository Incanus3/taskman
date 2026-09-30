defmodule TaskmanWeb.API.TaskBlockingControllerTest do
  use TaskmanWeb.ConnCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts
  alias Taskman.Tasks

  setup %{conn: conn} do
    user = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{plaintext: key}} =
             Accounts.create_api_key(
               user,
               %{name: "Blocking API tests", expires_at: DateTime.add(now, 86_400, :second)},
               now: now
             )

    {:ok, conn: put_api_key(conn, key)}
  end

  test "GET reads both directions with ordered endpoint summaries across Projects", %{conn: conn} do
    source_project = project_fixture(%{name: "Alpha"})
    target_project = project_fixture(%{name: "Beta"})
    list = list_fixture(source_project, %{name: "Review"})
    selected = task_fixture(source_project, %{title: "Selected", priority: :medium})
    incoming = task_fixture(source_project, list, %{title: "Approve", priority: :high})
    outgoing = task_fixture(target_project, %{title: "Publish", priority: :urgent})
    assert {:ok, _} = Tasks.add_block(source_project, incoming, selected)
    assert {:ok, _} = Tasks.add_block(source_project, selected, outgoing)

    assert %{"data" => %{"blocks" => [blocks], "blocked_by" => [blocked_by]}} =
             conn |> get(blocking_path(source_project, selected)) |> json_response(200)

    assert blocks == %{
             "id" => outgoing.id,
             "project_id" => target_project.id,
             "project_name" => "Beta",
             "title" => "Publish",
             "status" => "pending",
             "priority" => "urgent",
             "location" => %{"kind" => "project", "list_id" => nil, "path" => []}
           }

    assert blocked_by == %{
             "id" => incoming.id,
             "project_id" => source_project.id,
             "project_name" => "Alpha",
             "title" => "Approve",
             "status" => "pending",
             "priority" => "high",
             "location" => %{"kind" => "list", "list_id" => list.id, "path" => ["Review"]}
           }

    assert %{"data" => %{"blocks" => [%{"id" => selected_id}], "blocked_by" => []}} =
             conn |> get(blocking_path(source_project, incoming)) |> json_response(200)

    assert selected_id == selected.id
  end

  test "POST and DELETE use the blocker Project and return exact named endpoints", %{conn: conn} do
    source_project = project_fixture(%{name: "Launch"})
    target_project = project_fixture(%{name: "Website"})
    blocker = task_fixture(source_project, %{title: "Approve", priority: :high})
    blocked = task_fixture(target_project, %{title: "Publish", priority: :urgent})
    path = blocks_path(source_project, blocker, blocked.id)

    assert %{"data" => edge} = conn |> post(path) |> json_response(201)
    assert Map.keys(edge) |> Enum.sort() == ~w(blocked_task blocking_task)
    assert edge["blocking_task"]["id"] == blocker.id
    assert edge["blocked_task"]["id"] == blocked.id
    assert edge["blocking_task"]["project_name"] == "Launch"
    assert edge["blocked_task"]["priority"] == "urgent"

    assert Map.keys(edge["blocking_task"]) |> Enum.sort() ==
             ~w(id location priority project_id project_name status title)

    assert %{"data" => %{"blocks" => [], "blocked_by" => [%{"id" => blocker_id}]}} =
             conn |> get(blocking_path(target_project, blocked)) |> json_response(200)

    assert blocker_id == blocker.id
    assert %{"data" => ^edge} = conn |> delete(path) |> json_response(200)

    assert %{"data" => %{"blocks" => [], "blocked_by" => []}} =
             conn |> get(blocking_path(target_project, blocked)) |> json_response(200)
  end

  test "relationship routes reject malformed IDs, query keys and mutation bodies", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)
    path = blocking_path(project, task)
    target_path = blocks_path(project, task, task.id + 1)

    for invalid_path <- [
          "/api/v1/projects/no/tasks/#{task.id}/blocking",
          "/api/v1/projects/#{project.id}/tasks/0/blocking",
          blocks_path(project, task, "no"),
          blocks_path(project, task, "0")
        ] do
      method = if String.ends_with?(invalid_path, "/blocking"), do: :get, else: :post

      assert %{"error" => %{"code" => "invalid_request"}} =
               conn |> request(method, invalid_path) |> json_response(400)
    end

    for query_path <- [path <> "?page=1", target_path <> "?include=true"] do
      method = if String.contains?(query_path, "/blocks/"), do: :post, else: :get

      assert %{"error" => %{"code" => "invalid_request"}} =
               conn |> request(method, query_path) |> json_response(400)
    end

    assert %{"error" => %{"code" => "invalid_request"}} =
             conn |> delete(target_path <> "?other=1") |> json_response(400)

    for method <- [:post, :delete] do
      assert %{"error" => %{"code" => "invalid_request"}} =
               conn |> request(method, target_path, %{"ignored" => true}) |> json_response(400)
    end
  end

  test "relationship routes return 404 for missing Project, scoped Task, target or edge", %{
    conn: conn
  } do
    project = project_fixture(%{})
    other = project_fixture(%{})
    source = task_fixture(other)
    target = task_fixture(project)

    for {route_project, route_task, target_id} <- [
          {999_999_999, source.id, target.id},
          {project.id, source.id, target.id},
          {other.id, source.id, 999_999_999}
        ] do
      assert %{"error" => %{"code" => "not_found"}} =
               conn
               |> post(blocks_path(route_project, route_task, target_id))
               |> json_response(404)
    end

    assert %{"error" => %{"code" => "not_found"}} =
             conn |> get(blocking_path(project, source)) |> json_response(404)

    assert %{"error" => %{"code" => "not_found"}} =
             conn |> delete(blocks_path(other, source, target.id)) |> json_response(404)
  end

  test "POST reports duplicate, self, cycle and parent direction on target_task_id", %{
    conn: conn
  } do
    project = project_fixture(%{})
    first = task_fixture(project)
    second = task_fixture(project)
    child = task_fixture(project, %{}, parent: first)
    assert {:ok, _} = Tasks.add_block(project, first, second)

    for {blocker, target, message} <- [
          {first, second, "already blocks this Task"},
          {first, first, "cannot block itself"},
          {second, first, "would create a cycle"},
          {first, child, "a parent cannot block its child"}
        ] do
      assert %{
               "error" => %{
                 "code" => "validation_failed",
                 "fields" => %{"target_task_id" => [^message]}
               }
             } =
               conn |> post(blocks_path(project, blocker, target.id)) |> json_response(422)
    end
  end

  test "relationship routes require an API credential", %{conn: conn} do
    project = project_fixture(%{})
    first = task_fixture(project)
    second = task_fixture(project)
    anonymous = Plug.Conn.delete_req_header(conn, "authorization")

    for {method, path} <- [
          {:get, blocking_path(project, first)},
          {:post, blocks_path(project, first, second.id)},
          {:delete, blocks_path(project, first, second.id)}
        ] do
      assert %{"error" => %{"code" => "unauthorized"}} =
               anonymous |> request(method, path) |> json_response(401)
    end
  end

  defp blocking_path(project, task),
    do: "/api/v1/projects/#{project.id}/tasks/#{task.id}/blocking"

  defp blocks_path(%{id: _} = project, %{id: _} = task, target_id),
    do: blocks_path(project.id, task.id, target_id)

  defp blocks_path(project_id, task_id, target_id),
    do: "/api/v1/projects/#{project_id}/tasks/#{task_id}/blocks/#{target_id}"

  defp request(conn, :get, path), do: get(conn, path)
  defp request(conn, :post, path), do: post(conn, path)
  defp request(conn, :delete, path), do: delete(conn, path)
  defp request(conn, :post, path, body), do: post(conn, path, body)
  defp request(conn, :delete, path, body), do: delete(conn, path, body)
end
