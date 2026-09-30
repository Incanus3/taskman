defmodule TaskmanWeb.API.TaskSearchControllerTest do
  use TaskmanWeb.ConnCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts

  setup %{conn: conn} do
    user = user_fixture()
    now = DateTime.utc_now()

    {:ok, %{plaintext: key}} =
      Accounts.create_api_key(
        user,
        %{
          name: "Task search",
          expires_at: DateTime.add(now, 365 * 86_400, :second)
        },
        now: now
      )

    {:ok, conn: put_api_key(conn, key)}
  end

  test "GET search returns exact summary shape across Projects and respects Project filter", %{
    conn: conn
  } do
    first_project = project_fixture(%{name: "First"})
    second_project = project_fixture(%{name: "Second"})
    parent = list_fixture(second_project, %{name: "Parent"})
    child = list_fixture(second_project, parent, %{name: "Child"})
    first = task_fixture(first_project, %{title: "Publish", priority: :high})
    second = task_fixture(second_project, child, %{title: "publish", status: :done})

    assert %{"data" => [root, listed]} =
             conn |> get("/api/v1/tasks/search?q=publish") |> json_response(200)

    assert root == %{
             "id" => first.id,
             "title" => "Publish",
             "status" => "pending",
             "priority" => "high",
             "project_id" => first_project.id,
             "project_name" => "First",
             "location" => %{"kind" => "project", "list_id" => nil, "path" => []}
           }

    assert listed == %{
             "id" => second.id,
             "title" => "publish",
             "status" => "done",
             "priority" => "none",
             "project_id" => second_project.id,
             "project_name" => "Second",
             "location" => %{
               "kind" => "list",
               "list_id" => child.id,
               "path" => ["Parent", "Child"]
             }
           }

    assert %{"data" => [%{"id" => id}]} =
             conn
             |> recycle()
             |> get("/api/v1/tasks/search?q=publish&project_id=#{second_project.id}")
             |> json_response(200)

    assert id == second.id

    assert %{"data" => []} =
             conn |> recycle() |> get("/api/v1/tasks/search?q=missing") |> json_response(200)
  end

  test "GET search rejects malformed raw query and unknown Project", %{conn: conn} do
    project = project_fixture(%{})

    for suffix <- [
          "",
          "?q=",
          "?q=%20%09",
          "?q=x&q=y",
          "?q=x&project_id=1&project_id=2",
          "?q=x&project_id=0",
          "?q=x&project_id=no",
          "?q=x&project_id=%2B1",
          "?q=x&project_id[]=1",
          "?q=x&status=done",
          "?q[x]=y"
        ] do
      assert %{"error" => %{"code" => "invalid_request", "message" => "Invalid request"}} ==
               conn |> recycle() |> get("/api/v1/tasks/search" <> suffix) |> json_response(400)
    end

    assert %{"error" => %{"code" => "not_found", "message" => "Resource not found"}} ==
             conn
             |> recycle()
             |> get("/api/v1/tasks/search?q=x&project_id=#{project.id + 1}")
             |> json_response(404)
  end

  test "GET search requires API authentication", %{conn: conn} do
    assert %{"error" => %{"code" => "unauthorized"}} =
             conn
             |> delete_req_header("authorization")
             |> get("/api/v1/tasks/search?q=anything")
             |> json_response(401)
  end
end
