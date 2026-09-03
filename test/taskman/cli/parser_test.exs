defmodule Taskman.CLI.ParserTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.{Invocation, Parser, Registry}

  test "registry keeps the declared command order" do
    assert Enum.map(Registry.commands(), & &1.path) == [
             ~w(projects list),
             ~w(projects show),
             ~w(projects create),
             ~w(lists list),
             ~w(lists show),
             ~w(lists create),
             ~w(lists rename),
             ~w(tasks list),
             ~w(tasks show),
             ~w(tasks hierarchy),
             ~w(tasks create),
             ~w(tasks update),
             ~w(tasks move),
             ~w(config set-url),
             ~w(config set-key),
             ~w(config show),
             ~w(completions bash),
             ~w(completions fish),
             ~w(agent onboarding),
             ~w(agent skill install)
           ]
  end

  test "completion commands declare JSON as an incompatible global option" do
    for shell <- ~w(bash fish) do
      assert {:ok, command} = Registry.find(["completions", shell])
      assert {:forbidden_global, :json} in command.constraints
    end
  end

  test "parses options, operands, and globals interspersed with a command path" do
    assert {:ok,
            %Invocation{
              command: %{path: ["tasks", "update"]},
              arguments: %{task_id: 42},
              options: %{project: 7, status: "in_progress", clear_due_at: true},
              globals: %{api_url: "http://localhost:4010", json: true}
            }} =
             Parser.parse(
               ~w(tasks update --project 7 42 --status in_progress --clear-due-at
                  --json --api-url http://localhost:4010),
               %{}
             )
  end

  test "accepts global options before and after the command path" do
    assert {:ok, first} =
             Parser.parse(
               ~w(--json --api-url http://before projects list),
               %{"TASKMAN_API_URL" => "http://environment"}
             )

    assert first.globals == %{api_url: "http://before", json: true}

    assert {:ok, second} =
             Parser.parse(
               ~w(projects list --api-url http://after --json),
               %{"TASKMAN_API_URL" => "http://environment"}
             )

    assert second.globals == %{api_url: "http://after", json: true}
  end

  test "rejects Bash completion with the global JSON flag" do
    assert {:error, "Completion output is shell source and cannot be combined with --json",
            ["completions", "bash"]} =
             Parser.parse(~w(completions bash --json), %{})
  end

  test "rejects Fish completion with the global JSON flag" do
    assert {:error, "Completion output is shell source and cannot be combined with --json",
            ["completions", "fish"]} =
             Parser.parse(~w(completions fish --json), %{})
  end

  test "leaves environment resolution to the protected configuration layer" do
    assert {:ok, invocation} =
             Parser.parse(~w(projects list), %{"TASKMAN_API_URL" => "http://environment:4000"})

    assert invocation.globals == %{}
  end

  test "does not materialize a default API URL before configuration resolution" do
    assert {:ok, invocation} = Parser.parse(~w(projects list), %{})
    assert invocation.globals == %{}
  end

  test "returns help and version control markers before validating operands" do
    assert {:help, ["tasks", "move"]} = Parser.parse(~w(tasks move --help), %{})
    assert {:help, []} = Parser.parse(~w(--help), %{})
    assert :version = Parser.parse(~w(tasks update --version), %{})
  end

  test "rejects missing required operands and options" do
    assert {:error, _message, ["tasks", "show"]} =
             Parser.parse(~w(tasks show --project 7), %{})

    assert {:error, _message, ["projects", "create"]} =
             Parser.parse(~w(projects create --name New), %{})
  end

  test "rejects unknown commands and options" do
    assert {:error, _message, ["tasks"]} = Parser.parse(~w(tasks unknown), %{})

    assert {:error, _message, ["projects", "list"]} =
             Parser.parse(~w(projects list --wat), %{})
  end

  test "distinguishes an unknown child command from an incomplete group" do
    assert {:error, "A leaf command is required", ["tasks"]} =
             Parser.parse(~w(tasks), %{})

    assert {:error, "Unknown command: tasks unknown", ["tasks"]} =
             Parser.parse(~w(tasks unknown extra), %{})
  end

  test "rejects invalid integer IDs" do
    assert {:error, _message, ["tasks", "show"]} =
             Parser.parse(~w(tasks show --project 7 nope), %{})

    assert {:error, _message, ["tasks", "show"]} =
             Parser.parse(~w(tasks show --project 0 42), %{})
  end

  test "rejects invalid fixed option values" do
    assert {:error, _message, ["tasks", "create"]} =
             Parser.parse(~w(tasks create --project 7 --title Work --status later), %{})

    assert {:error, _message, ["tasks", "create"]} =
             Parser.parse(~w(tasks create --project 7 --title Work --priority critical), %{})
  end

  test "parses repeatable status filters and a complete Task list sort" do
    assert {:ok,
            %Invocation{
              command: %{path: ["tasks", "list"]},
              options: %{
                project: 7,
                statuses: ["pending", "done"],
                sort: "title",
                direction: "asc"
              }
            }} =
             Parser.parse(
               ~w(tasks list --project 7 --status pending --status done --sort title --direction asc),
               %{}
             )
  end

  test "rejects invalid or incomplete Task list query options" do
    invalid_invocations = [
      ~w(tasks list --project 7 --status unknown),
      ~w(tasks list --project 7 --sort unknown --direction asc),
      ~w(tasks list --project 7 --sort title --direction sideways),
      ~w(tasks list --project 7 --sort title),
      ~w(tasks list --project 7 --direction asc)
    ]

    for argv <- invalid_invocations do
      assert {:error, _message, ["tasks", "list"]} = Parser.parse(argv, %{})
    end
  end

  test "requires at least one editable option for task updates" do
    assert {:error, _message, ["tasks", "update"]} =
             Parser.parse(~w(tasks update --project 7 42), %{})
  end

  test "parses parent assignment and removal as Task mutations" do
    assert {:ok,
            %Invocation{
              command: %{path: ["tasks", "create"]},
              options: %{project: 7, title: "Child", parent: 42}
            }} = Parser.parse(~w(tasks create --project 7 --title Child --parent 42), %{})

    assert {:ok,
            %Invocation{
              command: %{path: ["tasks", "update"]},
              arguments: %{task_id: 51},
              options: %{project: 7, parent: 42}
            }} = Parser.parse(~w(tasks update --project 7 51 --parent 42), %{})

    assert {:ok,
            %Invocation{
              command: %{path: ["tasks", "update"]},
              arguments: %{task_id: 51},
              options: %{project: 7, no_parent: true}
            }} = Parser.parse(~w(tasks update --project 7 51 --no-parent), %{})
  end

  test "rejects contradictory Task parent mutations" do
    assert {:error, "Options --parent, --no-parent are mutually exclusive", ["tasks", "update"]} =
             Parser.parse(~w(tasks update --project 7 51 --parent 42 --no-parent), %{})
  end

  test "parses hierarchy inspection with a Project-scoped Task ID" do
    assert {:ok,
            %Invocation{
              command: %{path: ["tasks", "hierarchy"]},
              arguments: %{task_id: 51},
              options: %{project: 7}
            }} = Parser.parse(~w(tasks hierarchy --project 7 51), %{})
  end

  test "rejects both due-date options" do
    assert {:error, _message, ["tasks", "update"]} =
             Parser.parse(
               ~w(tasks update --project 7 42 --due-at 2026-08-29T12:00:00 --clear-due-at),
               %{}
             )
  end

  test "requires exactly one task move destination" do
    assert {:error, _message, ["tasks", "move"]} =
             Parser.parse(~w(tasks move --project 7 42), %{})

    assert {:error, _message, ["tasks", "move"]} =
             Parser.parse(~w(tasks move --project 7 42 --to-list 11 --to-project-root), %{})
  end
end
