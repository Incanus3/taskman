defmodule Taskman.CLI.Presentation.CompletionsTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.Presentation.Completions
  alias Taskman.CLI.Registry
  alias Taskman.CLI.Registry.Command

  @lifecycle_values ~w(icebox pending in_progress in_review done will_not_do)
  @priority_values ~w(none low medium high urgent)

  @tag :tmp_dir
  test "generates registry-complete Bash and Fish scripts that parse offline", %{tmp_dir: tmp_dir} do
    bash = Completions.bash()
    fish = Completions.fish()

    for command <- Registry.commands() do
      path = Enum.join(command.path, " ")
      assert bash =~ path
      assert fish =~ path
    end

    options =
      Registry.global_options() ++
        Enum.flat_map(Registry.commands(), & &1.options)

    for option <- Enum.uniq_by(options, & &1.long) do
      assert bash =~ option.long
      assert fish =~ option.long
    end

    for value <- @lifecycle_values ++ @priority_values do
      assert bash =~ value
      assert fish =~ value
    end

    refute bash =~ "http://localhost:4000"
    refute fish =~ "http://localhost:4000"
    refute bash =~ ~r/(?m)^\s*(taskman|curl)(?:\s|$)/
    refute fish =~ ~r/(?m)^\s*(taskman|curl)(?:\s|$)/

    bash_path = Path.join(tmp_dir, "taskman.bash")
    fish_path = Path.join(tmp_dir, "taskman.fish")
    File.write!(bash_path, bash)
    File.write!(fish_path, fish)

    assert {_output, 0} = System.cmd("bash", ["-n", bash_path], stderr_to_stdout: true)
    assert {_output, 0} = System.cmd("fish", ["-n", fish_path], stderr_to_stdout: true)
  end

  test "fixed values are sourced from command option metadata" do
    fixed_values =
      Registry.commands()
      |> Enum.flat_map(& &1.options)
      |> Enum.flat_map(& &1.values)
      |> Enum.uniq()

    for value <- fixed_values do
      assert Completions.bash() =~ value
      assert Completions.fish() =~ value
    end
  end

  test "Fish command predicates use the registry path resolver" do
    fish = Completions.fish()

    assert fish =~ "__taskman_command_path"
    assert fish =~ "__taskman_command_path_matches"
    refute fish =~ "__fish_seen_subcommand_from"
    refute fish =~ "__fish_use_subcommand"
  end

  test "CLI dispatches both completion commands without a backend" do
    for {shell, marker} <- [
          {"bash", "complete -F _taskman taskman"},
          {"fish", "complete -c taskman"}
        ] do
      result = Taskman.CLI.run(["completions", shell])

      assert result.status == 0
      assert result.stderr == ""
      assert result.stdout =~ marker
    end
  end

  @tag :tmp_dir
  test "Fish omits the other move destination after --to-list", %{tmp_dir: tmp_dir} do
    fish_path = Path.join(tmp_dir, "taskman.fish")
    File.write!(fish_path, Completions.fish())

    refute "--to-project-root" in fish_query(fish_path, "taskman tasks move --to-list 1 --")
  end

  @tag :tmp_dir
  test "Fish omits --clear-due-at after an explicit due date", %{tmp_dir: tmp_dir} do
    fish_path = Path.join(tmp_dir, "taskman.fish")
    File.write!(fish_path, Completions.fish())

    refute "--clear-due-at" in fish_query(
             fish_path,
             "taskman tasks update --project 1 2 --due-at 2026-08-29T12:00:00 --"
           )
  end

  @tag :tmp_dir
  test "Fish keeps a global for an allowed descendant in a mixed group", %{tmp_dir: tmp_dir} do
    commands = [
      %Command{path: ~w(agent onboarding)},
      %Command{
        path: ~w(agent skill install),
        constraints: [{:forbidden_global, :json}]
      }
    ]

    fish_path = Path.join(tmp_dir, "taskman-mixed.fish")
    File.write!(fish_path, Completions.fish(commands))

    assert "--json" in fish_query(fish_path, "taskman agent onboarding --")
    refute "--json" in fish_query(fish_path, "taskman agent skill install --")
  end

  @tag :tmp_dir
  test "Fish keeps JSON for agent onboarding but omits it for completions", %{tmp_dir: tmp_dir} do
    fish_path = Path.join(tmp_dir, "taskman.fish")
    File.write!(fish_path, Completions.fish())

    assert "--json" in fish_query(fish_path, "taskman agent onboarding --")
    refute "--json" in fish_query(fish_path, "taskman completions --")
  end

  @tag :tmp_dir
  test "Fish does not treat an option value as a forbidden command path", %{tmp_dir: tmp_dir} do
    fish_path = Path.join(tmp_dir, "taskman.fish")
    File.write!(fish_path, Completions.fish())

    assert "--json" in fish_query(
             fish_path,
             "taskman projects create --name completions --directory /tmp --"
           )
  end

  @tag :tmp_dir
  test "Fish keeps project create options after a child-named option value", %{tmp_dir: tmp_dir} do
    fish_path = Path.join(tmp_dir, "taskman.fish")
    File.write!(fish_path, Completions.fish())

    assert "--directory" in fish_query(
             fish_path,
             "taskman projects create --name show --"
           )
  end

  @tag :tmp_dir
  test "Fish keeps task update options after a sibling-named option value", %{tmp_dir: tmp_dir} do
    fish_path = Path.join(tmp_dir, "taskman.fish")
    File.write!(fish_path, Completions.fish())

    assert "--title" in fish_query(
             fish_path,
             "taskman tasks update --project 1 2 --description move --"
           )
  end

  @tag :tmp_dir
  test "Fish keeps top-level children after a group-named global option value", %{
    tmp_dir: tmp_dir
  } do
    fish_path = Path.join(tmp_dir, "taskman.fish")
    File.write!(fish_path, Completions.fish())

    suggestions = fish_query(fish_path, "taskman --api-url projects ")

    assert "projects" in suggestions
    assert "lists" in suggestions
    refute "create" in suggestions
  end

  @tag :tmp_dir
  test "Bash omits the other destination for inline move options", %{tmp_dir: tmp_dir} do
    bash_path = Path.join(tmp_dir, "taskman.bash")
    File.write!(bash_path, Completions.bash())

    refute "--to-project-root" in bash_query(bash_path, [
             "taskman",
             "tasks",
             "move",
             "--to-list=1",
             "--"
           ])
  end

  @tag :tmp_dir
  test "Bash omits --clear-due-at for an inline due date", %{tmp_dir: tmp_dir} do
    bash_path = Path.join(tmp_dir, "taskman.bash")
    File.write!(bash_path, Completions.bash())

    refute "--clear-due-at" in bash_query(
             bash_path,
             ["taskman", "tasks", "update", "--due-at=2026-08-29T12:00:00", "--"]
           )
  end

  @tag :tmp_dir
  test "Bash omits --no-parent after an inline parent selection", %{tmp_dir: tmp_dir} do
    bash_path = Path.join(tmp_dir, "taskman.bash")
    File.write!(bash_path, Completions.bash())

    refute "--no-parent" in bash_query(
             bash_path,
             ["taskman", "tasks", "update", "--parent=42", "--"]
           )
  end

  @tag :tmp_dir
  test "Fish offers parent assignment for creation and removal for updates", %{tmp_dir: tmp_dir} do
    fish_path = Path.join(tmp_dir, "taskman.fish")
    File.write!(fish_path, Completions.fish())

    assert "--parent" in fish_query(fish_path, "taskman tasks create --project 7 --")
    assert "--no-parent" in fish_query(fish_path, "taskman tasks update --project 7 51 --")
  end

  @tag :tmp_dir
  test "Bash omits forbidden globals at a constrained command prefix", %{tmp_dir: tmp_dir} do
    bash_path = Path.join(tmp_dir, "taskman.bash")
    File.write!(bash_path, Completions.bash())

    refute "--json" in bash_query(bash_path, ["taskman", "completions", ""])
  end

  @tag :tmp_dir
  test "Bash does not suggest commands or options as separate free-form values", %{
    tmp_dir: tmp_dir
  } do
    bash_path = Path.join(tmp_dir, "taskman.bash")
    File.write!(bash_path, Completions.bash())

    assert bash_query(bash_path, ["taskman", "projects", "create", "--name", ""]) == []
    assert bash_query(bash_path, ["taskman", "--api-url", ""]) == []
  end

  @tag :tmp_dir
  test "Bash completes inline fixed option values with the full option token", %{tmp_dir: tmp_dir} do
    bash_path = Path.join(tmp_dir, "taskman.bash")
    File.write!(bash_path, Completions.bash())

    assert bash_query(bash_path, ["taskman", "tasks", "create", "--status=in_"]) == [
             "--status=in_progress",
             "--status=in_review"
           ]
  end

  @tag :tmp_dir
  test "Bash and Fish complete Task list filter and sort options", %{tmp_dir: tmp_dir} do
    bash_path = Path.join(tmp_dir, "taskman.bash")
    fish_path = Path.join(tmp_dir, "taskman.fish")
    File.write!(bash_path, Completions.bash())
    File.write!(fish_path, Completions.fish())

    bash_options = bash_query(bash_path, ["taskman", "tasks", "list", "--"])
    fish_options = fish_query(fish_path, "taskman tasks list --")

    for option <- ["--status", "--sort", "--direction"] do
      assert option in bash_options
      assert option in fish_options
    end

    assert bash_query(bash_path, ["taskman", "tasks", "list", "--sort", ""]) ==
             ~w(id title status priority location)

    assert fish_query(fish_path, "taskman tasks list --direction ") == ~w(asc desc)

    assert "--status" in bash_query(
             bash_path,
             ["taskman", "tasks", "list", "--status", "pending", "--"]
           )
  end

  defp fish_query(path, command_line) do
    script = "source #{shell_quote(path)}; complete -C #{shell_quote(command_line)}"
    {output, status} = System.cmd("fish", ["-c", script], stderr_to_stdout: true)
    assert status == 0
    String.split(output, "\n", trim: true)
  end

  defp bash_query(path, words) do
    script = """
    source #{shell_quote(path)}
    COMP_WORDS=(#{Enum.map_join(words, " ", &shell_quote/1)})
    COMP_CWORD=#{length(words) - 1}
    _taskman
    printf '%s\\n' "${COMPREPLY[@]}"
    """

    {output, status} = System.cmd("bash", ["-c", script], stderr_to_stdout: true)
    assert status == 0
    String.split(output, "\n", trim: true)
  end

  defp shell_quote(value) do
    "'" <> String.replace(to_string(value), "'", "'\\''") <> "'"
  end
end
