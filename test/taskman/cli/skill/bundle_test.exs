defmodule Taskman.CLI.Skill.BundleTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.{Parser, Registry}
  alias Taskman.CLI.Skill.Bundle

  test "bundles a valid, registry-covered skill at compile time" do
    files = Bundle.files()
    assert Map.keys(files) == ["SKILL.md"]

    skill = Map.fetch!(files, "SKILL.md")

    assert skill =~ ~r/\A---\nname: taskman-cli\ndescription: Use when .+\n---\n/s
    assert skill =~ "Taskman is the system of record"
    assert skill =~ "Projects, Lists, Tasks, later relationships, and Agent Sessions"
    assert skill =~ "taskman --help"
    assert skill =~ "taskman agent onboarding"
    assert skill =~ "ordinary commands require a running backend"
    assert skill =~ "--json"
    assert skill =~ "stdout only on status 0"
    assert skill =~ "TASKMAN_API_URL"
    assert skill =~ "connection_failed"
    assert skill =~ "status 2"
    assert skill =~ "status 3"
    assert skill =~ "status 4"
    assert skill =~ "status 5"
    assert skill =~ "status 6"
    assert skill =~ "read stderr on failure"
    assert skill =~ "exact ID operands"
    assert skill =~ "never guess by name"
    assert skill =~ "Inspect before mutating"
    assert skill =~ "preserve its returned name and primary directory"
    assert skill =~ "never infer registration from the current working directory"
    assert skill =~ "taskman tasks list --project 7 --include-descendants --json"
    assert skill =~ "lists only Tasks directly at the Project root"

    assert skill =~
             "An empty direct-location result does not establish that the Project has no Tasks"

    assert skill =~ "explicit authority"
    assert skill =~ "Agent launch or completion is evidence only, not authority"

    assert skill =~
             "any Task lifecycle change requires a separate, user-authorized Task-status decision"

    assert skill =~ "Do not treat agent work as automatic Task completion"

    for command <- Registry.commands() do
      path = Enum.join(command.path, " ")
      group_help = "taskman #{hd(command.path)} --help"
      assert skill =~ path or skill =~ group_help
    end

    for example <- examples(skill) do
      argv = example |> tokenize() |> tl()

      case Parser.parse(argv, %{}) do
        {:ok, _invocation} -> :ok
        {:help, _path} -> :ok
        :version -> :ok
        other -> flunk("invalid skill example #{example}: #{inspect(other)}")
      end
    end

    assert Bundle.cli_version() == Taskman.CLI.version()
  end

  defp examples(skill) do
    inline =
      Regex.scan(~r/`(taskman [^`]+)`/, skill, capture: :all_but_first)
      |> List.flatten()

    code_block =
      Regex.scan(~r/^\s*(taskman .+)$/m, skill, capture: :all_but_first)
      |> List.flatten()

    Enum.uniq(inline ++ code_block)
    |> Enum.filter(&(String.trim(&1) == &1))
  end

  defp tokenize(command) do
    command
    |> String.replace(~r/["']/, "")
    |> String.split(~r/\s+/, trim: true)
  end
end
