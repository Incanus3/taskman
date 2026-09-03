defmodule Taskman.CLI.Commands.ListsTest do
  use ExUnit.Case, async: true

  setup {Req.Test, :verify_on_exit!}

  test "lists lists in readable tree order with parent and full path" do
    Req.Test.expect(ListCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/7/lists"
      assert conn.query_string == ""

      Req.Test.json(conn, %{
        data: [
          %{id: 11, project_id: 7, parent_list_id: nil, name: "Planning", path: ["Planning"]},
          %{
            id: 12,
            project_id: 7,
            parent_list_id: 11,
            name: "Launch",
            path: ["Planning", "Launch"]
          }
        ]
      })
    end)

    result =
      Taskman.CLI.run(["lists", "list", "--project", "7"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ListCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""

    assert result.stdout ==
             "ID\tNAME\tPARENT\tPATH\n" <>
               "11\tPlanning\t—\tPlanning\n" <>
               "12\tLaunch\t11\tPlanning / Launch\n"
  end

  test "lists list rejects malformed collection members as an invalid response" do
    Req.Test.expect(ListCommands, fn conn ->
      Req.Test.json(conn, %{
        data: [
          %{
            id: 12,
            project_id: 7,
            parent_list_id: 11,
            name: "Launch",
            path: ["Planning", nil]
          }
        ]
      })
    end)

    result =
      Taskman.CLI.run(["lists", "list", "--project", "7"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ListCommands}]
      )

    assert result.status == 5
    assert result.stdout == ""
    assert result.stderr =~ "invalid_response"
  end

  test "shows a list and preserves the API data envelope in JSON mode" do
    Req.Test.expect(ListCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/7/lists/12"

      Req.Test.json(conn, %{
        data: %{
          id: 12,
          project_id: 7,
          parent_list_id: 11,
          name: "Launch",
          path: ["Planning", "Launch"]
        }
      })
    end)

    result =
      Taskman.CLI.run(["lists", "show", "--project", "7", "12", "--json"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ListCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""

    assert Jason.decode!(result.stdout) == %{
             "data" => %{
               "id" => 12,
               "project_id" => 7,
               "parent_list_id" => 11,
               "name" => "Launch",
               "path" => ["Planning", "Launch"]
             }
           }
  end

  test "lists show rejects a malformed member as an invalid response" do
    Req.Test.expect(ListCommands, fn conn ->
      Req.Test.json(conn, %{data: %{}})
    end)

    result =
      Taskman.CLI.run(["lists", "show", "--project", "7", "12", "--json"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ListCommands}]
      )

    assert result.status == 5
    assert result.stdout == ""
    assert %{"error" => %{"code" => "invalid_response"}} = Jason.decode!(result.stderr)
  end

  test "creates a child list with its supplied parent" do
    Req.Test.expect(ListCommands, fn conn ->
      assert conn.method == "POST"
      assert conn.request_path == "/api/v1/projects/7/lists"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "list" => %{"name" => "Launch", "parent_list_id" => 11}
             }

      conn
      |> Plug.Conn.put_status(201)
      |> Req.Test.json(%{
        data: %{
          id: 12,
          project_id: 7,
          parent_list_id: 11,
          name: "Launch",
          path: ["Planning", "Launch"]
        }
      })
    end)

    result =
      Taskman.CLI.run(
        ["lists", "create", "--project", "7", "--name", "Launch", "--parent", "11", "--json"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ListCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert %{"data" => %{"id" => 12, "parent_list_id" => 11}} = Jason.decode!(result.stdout)
  end

  test "creates a root list without inventing a parent field" do
    Req.Test.expect(ListCommands, fn conn ->
      assert conn.method == "POST"
      assert conn.request_path == "/api/v1/projects/7/lists"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "list" => %{"name" => "Planning"}
             }

      conn
      |> Plug.Conn.put_status(201)
      |> Req.Test.json(%{
        data: %{id: 11, project_id: 7, parent_list_id: nil, name: "Planning", path: ["Planning"]}
      })
    end)

    result =
      Taskman.CLI.run(
        ["lists", "create", "--project", "7", "--name", "Planning"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ListCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout =~ "ID: 11"
    assert result.stdout =~ "NAME: Planning"
    assert result.stdout =~ "PARENT LIST ID: —"
    assert result.stdout =~ "PATH: Planning"
  end

  test "renames a list with only the replacement name" do
    Req.Test.expect(ListCommands, fn conn ->
      assert conn.method == "PATCH"
      assert conn.request_path == "/api/v1/projects/7/lists/12"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "list" => %{"name" => "Released"}
             }

      Req.Test.json(conn, %{
        data: %{
          id: 12,
          project_id: 7,
          parent_list_id: 11,
          name: "Released",
          path: ["Planning", "Released"]
        }
      })
    end)

    result =
      Taskman.CLI.run(
        ["lists", "rename", "--project", "7", "12", "--name", "Released", "--json"],
        env: %{"TASKMAN_API_KEY" => "tm_command_test_credential"},
        config_root: Path.join(System.tmp_dir!(), "taskman-cli-command-tests"),
        req_options: [plug: {Req.Test, ListCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert %{"data" => %{"name" => "Released"}} = Jason.decode!(result.stdout)
  end
end
