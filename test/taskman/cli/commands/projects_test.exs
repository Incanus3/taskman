defmodule Taskman.CLI.Commands.ProjectsTest do
  use ExUnit.Case, async: true

  import Taskman.ProjectsFixtures,
    only: [project_response_fixture: 0, project_response_fixture: 1]

  setup {Req.Test, :verify_on_exit!}

  test "projects list requests the collection and renders readable identifying fields" do
    Req.Test.expect(ProjectCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects"

      Req.Test.json(conn, %{data: [project_response_fixture()]})
    end)

    result =
      Taskman.CLI.run(["projects", "list"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout == "ID\tNAME\tDESCRIPTION\tICON\tCOLOR\n7\tCLI\t\tbriefcase\t#6366F1\n"
  end

  test "projects list rejects malformed collection members as an invalid response" do
    Req.Test.expect(ProjectCommands, fn conn ->
      Req.Test.json(conn, %{data: [%{}]})
    end)

    result =
      Taskman.CLI.run(["projects", "list"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 5
    assert result.stdout == ""
    assert result.stderr =~ "invalid_response"
  end

  test "projects show requests the project member and preserves its data envelope in JSON mode" do
    Req.Test.expect(ProjectCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/7"

      Req.Test.json(conn, %{data: project_response_fixture()})
    end)

    result =
      Taskman.CLI.run(["projects", "show", "7", "--json"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""

    assert Jason.decode!(result.stdout) == %{
             "data" => %{
               "id" => 7,
               "name" => "CLI",
               "description" => "",
               "icon" => "briefcase",
               "color" => "#6366F1"
             }
           }
  end

  test "projects show rejects a malformed member as an invalid response" do
    Req.Test.expect(ProjectCommands, fn conn ->
      Req.Test.json(conn, %{data: %{}})
    end)

    result =
      Taskman.CLI.run(["projects", "show", "7"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
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
               "project" => %{"name" => "CLI"}
             }

      conn
      |> Plug.Conn.put_status(201)
      |> Req.Test.json(%{data: project_response_fixture(%{"id" => 8})})
    end)

    result =
      Taskman.CLI.run(
        ["projects", "create", "--name", "CLI", "--json"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""

    assert Jason.decode!(result.stdout) == %{
             "data" => %{
               "id" => 8,
               "name" => "CLI",
               "description" => "",
               "icon" => "briefcase",
               "color" => "#6366F1"
             }
           }
  end

  test "projects create rejects a malformed member as an invalid response" do
    Req.Test.expect(ProjectCommands, fn conn ->
      conn
      |> Plug.Conn.put_status(201)
      |> Req.Test.json(%{data: %{}})
    end)

    result =
      Taskman.CLI.run(
        ["projects", "create", "--name", "CLI", "--json"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
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
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 3
    assert result.stdout == ""

    assert Jason.decode!(result.stderr) == %{
             "error" => %{"code" => "not_found", "message" => "Resource not found"}
           }
  end

  test "create sends only supplied identity fields" do
    Req.Test.expect(ProjectCommands, fn conn ->
      assert conn.method == "POST"
      assert conn.request_path == "/api/v1/projects"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() ==
               %{
                 "project" => %{
                   "name" => "CLI",
                   "description" => "Delivery",
                   "icon" => "rocket-launch",
                   "color" => "#ABCDEF"
                 }
               }

      conn
      |> Plug.Conn.put_status(201)
      |> Req.Test.json(%{
        data:
          project_response_fixture(%{
            "id" => 8,
            "description" => "Delivery",
            "icon" => "rocket-launch",
            "color" => "#ABCDEF"
          })
      })
    end)

    result =
      Taskman.CLI.run(
        [
          "projects",
          "create",
          "--name",
          "CLI",
          "--description",
          "Delivery",
          "--icon",
          "rocket-launch",
          "--color",
          "#ABCDEF",
          "--json"
        ],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 0
    assert Jason.decode!(result.stdout)["data"]["icon"] == "rocket-launch"
  end

  test "update PATCH sends only supplied description clear" do
    Req.Test.expect(ProjectCommands, fn conn ->
      assert conn.method == "PATCH"
      assert conn.request_path == "/api/v1/projects/7"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "project" => %{"description" => ""}
             }

      Req.Test.json(conn, %{data: project_response_fixture()})
    end)

    result =
      Taskman.CLI.run(["projects", "update", "7", "--description", "", "--json"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ProjectCommands}]
      )

    assert result.status == 0
    assert Jason.decode!(result.stdout)["data"]["description"] == ""
  end
end
