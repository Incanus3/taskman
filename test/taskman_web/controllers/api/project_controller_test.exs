defmodule TaskmanWeb.API.ProjectControllerTest do
  use TaskmanWeb.ConnCase, async: true

  import Taskman.AccountsFixtures
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
                 name: "Project tests",
                 expires_at: DateTime.add(now, @api_key_lifetime_seconds, :second)
               },
               now: now
             )

    {:ok, conn: put_api_key(conn, plaintext)}
  end

  test "GET /api/v1/projects returns ordered project data", %{conn: conn} do
    first =
      project_fixture(%{
        name: "First",
        description: "First work",
        icon: "rocket-launch",
        color: "#2468AC"
      })

    second = project_fixture(%{name: "Second"})
    first_id = first.id
    second_id = second.id

    conn = get(conn, "/api/v1/projects")

    assert %{
             "data" => [
               %{
                 "id" => ^first_id,
                 "name" => "First",
                 "description" => "First work",
                 "icon" => "rocket-launch",
                 "color" => "#2468AC"
               },
               %{
                 "id" => ^second_id,
                 "name" => "Second",
                 "description" => "",
                 "icon" => "briefcase",
                 "color" => "#6366F1"
               }
             ]
           } = json_response(conn, 200)
  end

  test "GET /api/v1/projects/:id returns project data", %{conn: conn} do
    project =
      project_fixture(%{
        name: "Shown",
        description: "A shown project",
        icon: "beaker",
        color: "#AB12CD"
      })

    id = project.id

    conn = get(conn, "/api/v1/projects/#{id}")

    assert %{
             "data" => %{
               "id" => id,
               "name" => "Shown",
               "description" => "A shown project",
               "icon" => "beaker",
               "color" => "#AB12CD"
             }
           } == json_response(conn, 200)
  end

  test "POST /api/v1/projects returns 201 for a name-only request", %{conn: conn} do
    conn = post(conn, "/api/v1/projects", %{"project" => %{"name" => "CLI"}})

    assert %{
             "data" => %{
               "id" => id,
               "name" => "CLI",
               "description" => "",
               "icon" => "briefcase",
               "color" => "#6366F1"
             }
           } = json_response(conn, 201)

    assert is_integer(id)
  end

  test "POST /api/v1/projects accepts identity fields and normalizes them", %{conn: conn} do
    conn =
      post(conn, "/api/v1/projects", %{
        "project" => %{
          "name" => "CLI",
          "description" => "  Delivery  ",
          "icon" => "wrench-screwdriver",
          "color" => "#aabbcc"
        }
      })

    assert %{
             "data" => %{
               "id" => id,
               "name" => "CLI",
               "description" => "Delivery",
               "icon" => "wrench-screwdriver",
               "color" => "#AABBCC"
             }
           } = json_response(conn, 201)

    assert is_integer(id)
  end

  test "PATCH /api/v1/projects/:id updates supplied fields and returns all metadata", %{
    conn: conn
  } do
    project = project_fixture(%{name: "Original"})

    conn =
      patch(conn, "/api/v1/projects/#{project.id}", %{
        "project" => %{
          "description" => "  Roadmap  ",
          "icon" => "rocket-launch",
          "color" => "#a1b2c3"
        }
      })

    assert %{
             "data" => %{
               "id" => project.id,
               "name" => "Original",
               "description" => "Roadmap",
               "icon" => "rocket-launch",
               "color" => "#A1B2C3"
             }
           } == json_response(conn, 200)
  end

  test "PATCH /api/v1/projects/:id accepts an empty object as a no-op", %{conn: conn} do
    project = project_fixture(%{name: "Unchanged", description: "Keep me"})

    conn = patch(conn, "/api/v1/projects/#{project.id}", %{"project" => %{}})

    assert %{
             "data" => %{
               "id" => id,
               "name" => "Unchanged",
               "description" => "Keep me",
               "icon" => "briefcase",
               "color" => "#6366F1"
             }
           } = json_response(conn, 200)

    assert id == project.id
  end

  test "PATCH /api/v1/projects/:id ignores unknown fields", %{conn: conn} do
    project = project_fixture(%{name: "Unchanged"})

    conn =
      patch(conn, "/api/v1/projects/#{project.id}", %{
        "project" => %{"unexpected" => "ignored", "directory" => "/tmp/not-a-project-field"}
      })

    assert %{
             "data" => %{
               "id" => id,
               "name" => "Unchanged",
               "description" => "",
               "icon" => "briefcase",
               "color" => "#6366F1"
             }
           } = json_response(conn, 200)

    assert id == project.id
  end

  test "PATCH /api/v1/projects/:id reports identity validation errors", %{conn: conn} do
    project = project_fixture(%{})

    for {field, value, expected} <- [
          {"icon", "unknown", %{"icon" => ["is invalid"]}},
          {"color", "red", %{"color" => ["has invalid format"]}},
          {"description", String.duplicate("x", 161),
           %{"description" => ["should be at most 160 character(s)"]}}
        ] do
      response =
        conn
        |> recycle()
        |> patch("/api/v1/projects/#{project.id}", %{"project" => %{field => value}})
        |> json_response(422)

      assert %{
               "error" => %{
                 "code" => "validation_failed",
                 "message" => "Validation failed",
                 "fields" => expected
               }
             } == response
    end
  end

  test "PATCH /api/v1/projects/:id rejects malformed payloads", %{conn: conn} do
    project = project_fixture(%{})

    for payload <- [%{}, %{"project" => "invalid"}] do
      assert %{"error" => %{"code" => "invalid_request", "message" => "Invalid request"}} =
               conn
               |> recycle()
               |> patch("/api/v1/projects/#{project.id}", payload)
               |> json_response(400)
    end
  end

  test "PATCH /api/v1/projects/:id distinguishes malformed and missing ids", %{conn: conn} do
    assert %{"error" => %{"code" => "invalid_request", "message" => "Invalid request"}} =
             conn
             |> patch("/api/v1/projects/not-an-id", %{"project" => %{}})
             |> json_response(400)

    assert %{"error" => %{"code" => "not_found", "message" => "Resource not found"}} =
             conn
             |> recycle()
             |> patch("/api/v1/projects/999999999", %{"project" => %{}})
             |> json_response(404)
  end

  test "POST /api/v1/projects ignores unrelated input keys", %{conn: conn} do
    conn =
      post(conn, "/api/v1/projects", %{"project" => %{"name" => "CLI", "other" => "ignored"}})

    assert %{"data" => %{"id" => id, "name" => "CLI"}} = json_response(conn, 201)
    assert is_integer(id)
  end

  test "POST /api/v1/projects returns a name error when name is missing", %{conn: conn} do
    conn = post(conn, "/api/v1/projects", %{"project" => %{}})

    assert %{"error" => %{"code" => "validation_failed", "fields" => fields}} =
             json_response(conn, 422)

    assert fields == %{"name" => ["can't be blank"]}
  end

  test "POST /api/v1/projects returns a name error for whitespace", %{conn: conn} do
    conn = post(conn, "/api/v1/projects", %{"project" => %{"name" => "   "}})

    assert %{"error" => %{"code" => "validation_failed", "fields" => fields}} =
             json_response(conn, 422)

    assert fields == %{"name" => ["can't be blank"]}
  end

  test "malformed and missing project ids use stable errors", %{conn: conn} do
    assert %{"error" => %{"code" => "invalid_request"}} =
             conn |> get("/api/v1/projects/not-an-id") |> json_response(400)

    assert %{"error" => %{"code" => "not_found"}} =
             recycle(conn) |> get("/api/v1/projects/999999999") |> json_response(404)
  end

  test "malformed JSON requests use the invalid_request error envelope", %{conn: conn} do
    {400, _headers, body} =
      assert_error_sent 400, fn ->
        conn
        |> put_req_header("accept", "application/json")
        |> put_req_header("content-type", "application/json")
        |> post("/api/v1/projects", "{")
      end

    assert Jason.decode!(body) == %{
             "error" => %{"code" => "invalid_request", "message" => "Invalid request"}
           }
  end

  test "unsupported API routes retain the ordinary 404 response", %{conn: conn} do
    conn =
      conn
      |> put_req_header("accept", "application/json")
      |> get("/api/v1/not-a-route")

    assert %{"errors" => %{"detail" => "Not Found"}} = json_response(conn, 404)
  end
end
