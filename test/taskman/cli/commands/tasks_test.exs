defmodule Taskman.CLI.Commands.TasksTest do
  use ExUnit.Case, async: true

  setup {Req.Test, :verify_on_exit!}

  test "lists tasks for a list with descendant filtering query parameters" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/7/tasks"
      assert conn.query_params == %{"list_id" => "11", "include_descendants" => "true"}

      Req.Test.json(conn, %{
        data: [
          %{
            id: 42,
            project_id: 7,
            list_id: 11,
            parent_task_id: nil,
            title: "Prepare launch",
            description: "Details",
            status: "pending",
            priority: "high",
            due_at: nil,
            location: %{kind: "list", list_id: 11, path: ["Planning"]}
          }
        ]
      })
    end)

    result =
      Taskman.CLI.run(
        ["tasks", "list", "--project", "7", "--list", "11", "--include-descendants"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""

    assert result.stdout ==
             "ID\tTITLE\tPARENT\tSTATUS\tPRIORITY\tLOCATION\tDUE\n" <>
               "42\tPrepare launch\t—\tpending\thigh\tPlanning\t—\n"
  end

  test "lists Tasks with repeated status filters and field sorting query parameters" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/7/tasks"

      assert conn.query_params == %{
               "statuses" => ["pending", "done"],
               "sort" => "title",
               "direction" => "asc"
             }

      Req.Test.json(conn, %{data: []})
    end)

    result =
      Taskman.CLI.run(
        [
          "tasks",
          "list",
          "--project",
          "7",
          "--status",
          "pending",
          "--status",
          "done",
          "--sort",
          "title",
          "--direction",
          "asc"
        ],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0, result.stderr
    assert result.stderr == ""
  end

  test "tasks list rejects malformed collection members as an invalid response" do
    Req.Test.expect(TaskCommands, fn conn ->
      Req.Test.json(conn, %{
        data: [
          %{
            id: 42,
            project_id: 7,
            list_id: 11,
            parent_task_id: nil,
            title: "Prepare launch",
            description: "Details",
            status: "unknown",
            priority: "high",
            due_at: nil,
            location: %{kind: "list", list_id: 11, path: ["Planning"]}
          }
        ]
      })
    end)

    result =
      Taskman.CLI.run(["tasks", "list", "--project", "7"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 5
    assert result.stdout == ""
    assert result.stderr =~ "invalid_response"
  end

  test "lists direct project tasks without absent query options" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/7/tasks"
      assert conn.query_string == ""

      Req.Test.json(conn, %{
        data: [
          %{
            id: 42,
            project_id: 7,
            list_id: nil,
            parent_task_id: nil,
            title: "Prepare launch",
            description: "",
            status: "pending",
            priority: "none",
            due_at: nil,
            location: %{kind: "project", list_id: nil, path: []}
          }
        ]
      })
    end)

    result =
      Taskman.CLI.run(["tasks", "list", "--project", "7"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout =~ "42\tPrepare launch\t—\tpending\tnone\tProject\t—"
  end

  test "shows a task and preserves every response field in JSON mode" do
    response = %{
      id: 42,
      project_id: 7,
      list_id: 11,
      parent_task_id: nil,
      title: "Prepare launch",
      description: "Details",
      status: "in_review",
      priority: "urgent",
      due_at: "2026-08-29T12:00:00",
      location: %{kind: "list", list_id: 11, path: ["Planning"]}
    }

    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/7/tasks/42"
      Req.Test.json(conn, %{data: response})
    end)

    result =
      Taskman.CLI.run(["tasks", "show", "--project", "7", "42", "--json"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""

    assert Jason.decode!(result.stdout) == %{
             "data" => %{
               "id" => 42,
               "project_id" => 7,
               "list_id" => 11,
               "parent_task_id" => nil,
               "title" => "Prepare launch",
               "description" => "Details",
               "status" => "in_review",
               "priority" => "urgent",
               "due_at" => "2026-08-29T12:00:00",
               "location" => %{"kind" => "list", "list_id" => 11, "path" => ["Planning"]}
             }
           }
  end

  test "creates a task with every present field, including an explicit empty description" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "POST"
      assert conn.request_path == "/api/v1/projects/7/tasks"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "task" => %{
                 "title" => "Prepare launch",
                 "description" => "",
                 "status" => "in_review",
                 "priority" => "urgent",
                 "due_at" => "2026-08-29T12:00:00",
                 "list_id" => 11
               }
             }

      conn
      |> Plug.Conn.put_status(201)
      |> Req.Test.json(%{
        data:
          task_response(%{
            description: "",
            status: "in_review",
            priority: "urgent",
            due_at: "2026-08-29T12:00:00"
          })
      })
    end)

    result =
      Taskman.CLI.run(
        [
          "tasks",
          "create",
          "--project",
          "7",
          "--title",
          "Prepare launch",
          "--description",
          "",
          "--status",
          "in_review",
          "--priority",
          "urgent",
          "--due-at",
          "2026-08-29T12:00:00",
          "--list",
          "11",
          "--json"
        ],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert %{"data" => %{"id" => 42}} = Jason.decode!(result.stdout)
  end

  test "creates a project-root task while omitting absent optional fields" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "POST"
      assert conn.request_path == "/api/v1/projects/7/tasks"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "task" => %{"title" => "Root task"}
             }

      conn
      |> Plug.Conn.put_status(201)
      |> Req.Test.json(%{
        data:
          task_response(%{
            id: 43,
            list_id: nil,
            title: "Root task",
            location: %{kind: "project", list_id: nil, path: []}
          })
      })
    end)

    result =
      Taskman.CLI.run(
        ["tasks", "create", "--project", "7", "--title", "Root task"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout =~ "ID: 43"
    assert result.stdout =~ "TITLE: Root task"
  end

  test "creates a Task with the selected parent ID" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "POST"
      assert conn.request_path == "/api/v1/projects/7/tasks"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "task" => %{"title" => "Child", "parent_task_id" => 42}
             }

      conn
      |> Plug.Conn.put_status(201)
      |> Req.Test.json(%{data: task_response(%{title: "Child", parent_task_id: 42})})
    end)

    result =
      Taskman.CLI.run(
        ["tasks", "create", "--project", "7", "--title", "Child", "--parent", "42", "--json"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert %{"data" => %{"parent_task_id" => 42}} = Jason.decode!(result.stdout)
  end

  test "updates selected fields and sends a null due date" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "PATCH"
      assert conn.request_path == "/api/v1/projects/7/tasks/42"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "task" => %{"status" => "in_review", "priority" => "urgent", "due_at" => nil}
             }

      Req.Test.json(conn, %{
        data: task_response(%{status: "in_review", priority: "urgent", due_at: nil})
      })
    end)

    result =
      Taskman.CLI.run(
        [
          "tasks",
          "update",
          "--project",
          "7",
          "42",
          "--status",
          "in_review",
          "--priority",
          "urgent",
          "--clear-due-at",
          "--json"
        ],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert %{"data" => %{"status" => "in_review", "due_at" => nil}} = Jason.decode!(result.stdout)
  end

  test "clear due date alone sends only the due_at null field" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "PATCH"
      assert conn.request_path == "/api/v1/projects/7/tasks/42"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{"task" => %{"due_at" => nil}}
      Req.Test.json(conn, %{data: task_response(%{due_at: nil})})
    end)

    result =
      Taskman.CLI.run(
        ["tasks", "update", "--project", "7", "42", "--clear-due-at"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
  end

  test "updates a Task parent and can clear it without another field" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "PATCH"
      assert conn.request_path == "/api/v1/projects/7/tasks/42"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "task" => %{"parent_task_id" => 41}
             }

      Req.Test.json(conn, %{data: task_response(%{parent_task_id: 41})})
    end)

    set_parent =
      Taskman.CLI.run(
        ["tasks", "update", "--project", "7", "42", "--parent", "41"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert set_parent.status == 0

    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "PATCH"
      assert conn.request_path == "/api/v1/projects/7/tasks/42"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "task" => %{"parent_task_id" => nil}
             }

      Req.Test.json(conn, %{data: task_response(%{parent_task_id: nil})})
    end)

    clear_parent =
      Taskman.CLI.run(
        ["tasks", "update", "--project", "7", "42", "--no-parent", "--json"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert clear_parent.status == 0
    assert %{"data" => %{"parent_task_id" => nil}} = Jason.decode!(clear_parent.stdout)
  end

  test "inspects a Task hierarchy through the dedicated endpoint" do
    response = hierarchy_response() |> Jason.encode!() |> Jason.decode!()

    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/7/tasks/51/hierarchy"
      Req.Test.json(conn, %{data: response})
    end)

    result =
      Taskman.CLI.run(["tasks", "hierarchy", "--project", "7", "51", "--json"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert Jason.decode!(result.stdout) == %{"data" => response}
  end

  test "moves a task to a list" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "POST"
      assert conn.request_path == "/api/v1/projects/7/tasks/42/move"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "destination" => %{"list_id" => 11}
             }

      Req.Test.json(conn, %{data: task_response()})
    end)

    result =
      Taskman.CLI.run(
        ["tasks", "move", "--project", "7", "42", "--to-list", "11", "--json"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert %{"data" => %{"list_id" => 11}} = Jason.decode!(result.stdout)
  end

  test "moves a task to the project root with a null list id" do
    Req.Test.expect(TaskCommands, fn conn ->
      assert conn.method == "POST"
      assert conn.request_path == "/api/v1/projects/7/tasks/42/move"

      assert conn |> Req.Test.raw_body() |> Jason.decode!() == %{
               "destination" => %{"list_id" => nil}
             }

      Req.Test.json(conn, %{
        data:
          task_response(%{
            list_id: nil,
            location: %{kind: "project", list_id: nil, path: []}
          })
      })
    end)

    result =
      Taskman.CLI.run(
        ["tasks", "move", "--project", "7", "42", "--to-project-root"],
        req_options: [plug: {Req.Test, TaskCommands}]
      )

    assert result.status == 0
    assert result.stderr == ""
  end

  defp task_response(overrides \\ %{}) do
    Map.merge(
      %{
        id: 42,
        project_id: 7,
        list_id: 11,
        parent_task_id: nil,
        title: "Prepare launch",
        description: "Details",
        status: "pending",
        priority: "high",
        due_at: nil,
        location: %{kind: "list", list_id: 11, path: ["Planning"]}
      },
      overrides
    )
  end

  defp hierarchy_response do
    %{
      "selected_task_id" => 51,
      "root" => %{
        "task" => task_response(%{id: 42, title: "Build import"}),
        "children" => [
          %{
            "task" => task_response(%{id: 51, parent_task_id: 42, title: "Implement parser"}),
            "children" => []
          }
        ]
      }
    }
  end
end
