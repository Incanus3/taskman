defmodule TaskmanWeb.API.ProjectControllerTest do
  use TaskmanWeb.ConnCase, async: true

  import Taskman.ProjectsFixtures

  test "GET /api/v1/projects returns ordered project data", %{conn: conn} do
    first = project_fixture(%{name: "First"})
    second = project_fixture(%{name: "Second"})
    first_id = first.id
    second_id = second.id

    conn = get(conn, "/api/v1/projects")

    assert %{
             "data" => [
               %{"id" => ^first_id, "name" => "First", "primary_directory" => _},
               %{"id" => ^second_id, "name" => "Second", "primary_directory" => _}
             ]
           } = json_response(conn, 200)
  end

  test "POST /api/v1/projects returns 201 and normalized data", %{conn: conn} do
    conn =
      post(conn, "/api/v1/projects", %{
        "project" => %{"name" => "CLI", "primary_directory" => File.cwd!()}
      })

    assert %{"data" => %{"id" => id, "name" => "CLI"}} = json_response(conn, 201)
    assert is_integer(id)
  end

  test "POST /api/v1/projects returns field errors", %{conn: conn} do
    conn = post(conn, "/api/v1/projects", %{"project" => %{"name" => ""}})

    assert %{
             "error" => %{
               "code" => "validation_failed",
               "fields" => %{"name" => [_], "primary_directory" => [_]}
             }
           } = json_response(conn, 422)
  end

  test "malformed and missing project ids use stable errors", %{conn: conn} do
    assert %{"error" => %{"code" => "invalid_request"}} =
             conn |> get("/api/v1/projects/not-an-id") |> json_response(400)

    assert %{"error" => %{"code" => "not_found"}} =
             build_conn() |> get("/api/v1/projects/999999999") |> json_response(404)
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
