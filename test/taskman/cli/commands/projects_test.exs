defmodule Taskman.CLI.Commands.ProjectsTest do
  use ExUnit.Case, async: true

  setup {Req.Test, :verify_on_exit!}

  test "projects list requests the collection and renders readable identifying fields" do
    Req.Test.expect(ProjectCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects"

      Req.Test.json(conn, %{
        data: [%{id: 7, name: "CLI", primary_directory: "/work/cli"}]
      })
    end)

    result =
      Taskman.CLI.run(["projects", "list"],
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout == "ID\tNAME\tPRIMARY DIRECTORY\n7\tCLI\t/work/cli\n"
  end

  test "projects list rejects malformed collection members as an invalid response" do
    Req.Test.expect(ProjectCommands, fn conn ->
      Req.Test.json(conn, %{data: [nil]})
    end)

    result =
      Taskman.CLI.run(["projects", "list"],
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 5
    assert result.stdout == ""
    assert result.stderr =~ "invalid_response"
  end

  test "projects show requests the project member and preserves all fields in JSON mode" do
    Req.Test.expect(ProjectCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/7"

      Req.Test.json(conn, %{
        data: %{id: 7, name: "CLI", primary_directory: "/work/cli"}
      })
    end)

    result =
      Taskman.CLI.run(["projects", "show", "7", "--json"],
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""

    assert Jason.decode!(result.stdout) == %{
             "data" => %{"id" => 7, "name" => "CLI", "primary_directory" => "/work/cli"}
           }
  end

  test "projects show rejects a malformed member as an invalid response" do
    Req.Test.expect(ProjectCommands, fn conn ->
      Req.Test.json(conn, %{data: %{}})
    end)

    result =
      Taskman.CLI.run(["projects", "show", "7"],
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 5
    assert result.stdout == ""
    assert result.stderr =~ "invalid_response"
  end

  test "projects create sends the exact API body and returns its data envelope" do
    Req.Test.expect(ProjectCommands, fn conn ->
      assert conn.method == "POST"
      assert conn.request_path == "/api/v1/projects"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "project" => %{"name" => "CLI", "primary_directory" => "/work/cli"}
             }

      conn
      |> Plug.Conn.put_status(201)
      |> Req.Test.json(%{data: %{id: 8, name: "CLI", primary_directory: "/work/cli"}})
    end)

    result =
      Taskman.CLI.run(
        ["projects", "create", "--name", "CLI", "--directory", "/work/cli", "--json"],
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert %{"data" => %{"id" => 8, "name" => "CLI"}} = Jason.decode!(result.stdout)
  end

  test "projects create rejects a malformed member as an invalid response" do
    Req.Test.expect(ProjectCommands, fn conn ->
      conn
      |> Plug.Conn.put_status(201)
      |> Req.Test.json(%{data: %{}})
    end)

    result =
      Taskman.CLI.run(
        ["projects", "create", "--name", "CLI", "--directory", "/work/cli", "--json"],
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 5
    assert result.stdout == ""

    assert %{"error" => %{"code" => "invalid_response"}} = Jason.decode!(result.stderr)
  end

  test "server errors are written only to stderr with their mapped exit status" do
    Req.Test.expect(ProjectCommands, fn conn ->
      conn
      |> Plug.Conn.put_status(404)
      |> Req.Test.json(%{error: %{code: "not_found", message: "Resource not found"}})
    end)

    result =
      Taskman.CLI.run(["projects", "show", "7", "--json"],
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 3
    assert result.stdout == ""

    assert Jason.decode!(result.stderr) == %{
             "error" => %{"code" => "not_found", "message" => "Resource not found"}
           }
  end
end
