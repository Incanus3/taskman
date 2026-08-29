defmodule Taskman.CLI.HelpTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.{Help, Registry}

  test "top-level help discovers every command group and global option" do
    help = Help.render([])

    for group <- ~w(projects lists tasks completions agent) do
      assert help =~ "taskman #{group}"
    end

    for utility <- [
          "taskman completions bash",
          "taskman completions fish",
          "taskman agent onboarding",
          "taskman agent skill install"
        ] do
      assert help =~ utility
    end

    for option <- ["--api-url", "--json", "--help", "--version"] do
      assert help =~ option
    end

    assert help =~ "TASKMAN_API_URL"
  end

  test "group help lists every child command" do
    for group <- ~w(projects lists tasks completions agent) do
      help = Help.render([group])

      Registry.commands()
      |> Enum.filter(&(List.first(&1.path) == group))
      |> Enum.each(fn command ->
        assert help =~ Enum.join(command.path, " ")
      end)
    end
  end

  test "leaf help documents usage, options, output, statuses, and an example" do
    help = Help.render(~w(tasks move))

    assert help =~ "taskman tasks move --project PROJECT_ID TASK_ID"
    assert help =~ "--to-list LIST_ID"
    assert help =~ "--to-project-root"
    assert help =~ "Output"
    assert help =~ "Exit statuses"
    assert help =~ "Example"
  end

  test "completion leaf help describes shell output and rejects JSON mode" do
    for shell <- ~w(bash fish) do
      help = Help.render(["completions", shell])

      assert help =~ "shell source"
      assert help =~ "--json is invalid"
      refute help =~ "add --json for one API-compatible JSON envelope"
    end
  end

  test "every registered leaf has usage and an example" do
    for command <- Registry.commands() do
      help = Help.render(command.path)
      assert help =~ "Usage"
      assert help =~ "Example"
    end
  end
end
