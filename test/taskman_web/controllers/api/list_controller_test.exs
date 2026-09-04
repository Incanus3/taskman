defmodule TaskmanWeb.API.ListControllerTest do
  use TaskmanWeb.ConnCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures

  alias Taskman.Accounts

  @api_key_lifetime_seconds 365 * 86_400

  setup %{conn: conn} do
    user = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{plaintext: plaintext}} =
             Accounts.create_api_key(
               user,
               %{
                 name: "List tests",
                 expires_at: DateTime.add(now, @api_key_lifetime_seconds, :second)
               },
               now: now
             )

    {:ok, conn: put_api_key(conn, plaintext)}
  end

  test "GET lists returns parent ids and root-to-node paths", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    project_id = project.id
    root_id = root.id
    child_id = child.id

    conn = get(conn, "/api/v1/projects/#{project.id}/lists")

    assert %{
             "data" => [
               %{
                 "id" => ^root_id,
                 "project_id" => ^project_id,
                 "parent_list_id" => nil,
                 "name" => "Planning",
                 "path" => ["Planning"]
               },
               %{
                 "id" => ^child_id,
                 "project_id" => ^project_id,
                 "parent_list_id" => ^root_id,
                 "name" => "Launch",
                 "path" => ["Planning", "Launch"]
               }
             ]
           } = json_response(conn, 200)
  end

  test "GET lists returns interleaved creations in stable tree order", %{conn: conn} do
    project = project_fixture(%{})
    first_root = list_fixture(project, %{name: "First"})
    second_root = list_fixture(project, %{name: "Second"})
    first_child = list_fixture(project, first_root, %{name: "First child"})

    conn = get(conn, "/api/v1/projects/#{project.id}/lists")

    assert %{"data" => lists} = json_response(conn, 200)

    assert Enum.map(lists, & &1["id"]) == [
             first_root.id,
             first_child.id,
             second_root.id
           ]
  end

  test "POST creates a root list and ignores ownership fields", %{conn: conn} do
    project = project_fixture(%{})
    other = project_fixture(%{})

    conn =
      post(conn, "/api/v1/projects/#{project.id}/lists", %{
        "list" => %{
          "name" => "  Planning  ",
          "project_id" => other.id,
          "parent_list_id" => nil
        }
      })

    assert %{
             "data" => %{
               "id" => id,
               "project_id" => project_id,
               "parent_list_id" => nil,
               "name" => "Planning",
               "path" => ["Planning"]
             }
           } = json_response(conn, 201)

    assert is_integer(id)
    assert project_id == project.id
  end

  test "POST creates a child list with its parent path", %{conn: conn} do
    project = project_fixture(%{})
    parent = list_fixture(project, %{name: "Planning"})
    parent_id = parent.id

    conn =
      post(conn, "/api/v1/projects/#{project.id}/lists", %{
        "list" => %{"name" => "Launch", "parent_list_id" => parent.id}
      })

    assert %{
             "data" => %{
               "id" => child_id,
               "project_id" => project_id,
               "parent_list_id" => ^parent_id,
               "name" => "Launch",
               "path" => ["Planning", "Launch"]
             }
           } = json_response(conn, 201)

    assert is_integer(child_id)
    assert project_id == project.id
  end

  test "POST rejects a malformed parent id", %{conn: conn} do
    project = project_fixture(%{})

    conn =
      post(conn, "/api/v1/projects/#{project.id}/lists", %{
        "list" => %{"name" => "Invalid", "parent_list_id" => "not-an-id"}
      })

    assert %{"error" => %{"code" => "invalid_request"}} = json_response(conn, 400)
  end

  test "POST child list rejects a parent from another Project", %{conn: conn} do
    project = project_fixture(%{})
    other = project_fixture(%{})
    parent = list_fixture(other)

    conn =
      post(conn, "/api/v1/projects/#{project.id}/lists", %{
        "list" => %{"name" => "Invalid", "parent_list_id" => parent.id}
      })

    assert %{"error" => %{"code" => "not_found"}} = json_response(conn, 404)
  end

  test "GET list returns its complete representation", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})

    conn = get(conn, "/api/v1/projects/#{project.id}/lists/#{child.id}")

    assert %{
             "data" => %{
               "id" => child_id,
               "project_id" => project_id,
               "parent_list_id" => parent_id,
               "name" => "Launch",
               "path" => ["Planning", "Launch"]
             }
           } = json_response(conn, 200)

    assert child_id == child.id
    assert project_id == project.id
    assert parent_id == root.id
  end

  test "PATCH renames a list and preserves its location", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})

    conn =
      patch(conn, "/api/v1/projects/#{project.id}/lists/#{child.id}", %{
        "list" => %{"name" => "  Released  ", "project_id" => project.id, "parent_list_id" => nil}
      })

    assert %{
             "data" => %{
               "id" => child_id,
               "project_id" => project_id,
               "parent_list_id" => parent_id,
               "name" => "Released",
               "path" => ["Planning", "Released"]
             }
           } = json_response(conn, 200)

    assert child_id == child.id
    assert project_id == project.id
    assert parent_id == root.id
  end

  test "POST rejects duplicate sibling names with validation fields", %{conn: conn} do
    project = project_fixture(%{})
    _existing = list_fixture(project, %{name: "Planning"})

    conn =
      post(conn, "/api/v1/projects/#{project.id}/lists", %{
        "list" => %{"name" => "planning", "parent_list_id" => nil}
      })

    assert %{
             "error" => %{
               "code" => "validation_failed",
               "fields" => %{"name" => [_]}
             }
           } = json_response(conn, 422)
  end

  test "nested list routes reject malformed project and list ids", %{conn: conn} do
    assert %{"error" => %{"code" => "invalid_request"}} =
             conn |> get("/api/v1/projects/not-an-id/lists") |> json_response(400)

    project = project_fixture(%{})

    assert %{"error" => %{"code" => "invalid_request"}} =
             recycle(conn)
             |> get("/api/v1/projects/#{project.id}/lists/not-an-id")
             |> json_response(400)

    assert %{"error" => %{"code" => "invalid_request"}} =
             recycle(conn)
             |> patch("/api/v1/projects/#{project.id}/lists/0", %{"list" => %{"name" => "Nope"}})
             |> json_response(400)
  end

  test "list lookup is scoped to its Project", %{conn: conn} do
    project = project_fixture(%{})
    other = project_fixture(%{})
    foreign = list_fixture(other, %{name: "Foreign"})

    conn = get(conn, "/api/v1/projects/#{project.id}/lists/#{foreign.id}")

    assert %{"error" => %{"code" => "not_found"}} = json_response(conn, 404)
  end
end
