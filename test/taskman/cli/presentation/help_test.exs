defmodule Taskman.CLI.Presentation.HelpTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.Presentation.Help
  alias Taskman.CLI.Registry

  test "top-level help discovers every command group and global option" do
    help = Help.render([])

    for group <- ~w(projects lists tasks config completions agent) do
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
    assert help =~ "TASKMAN_API_KEY"
    assert help =~ "config.json"
  end

  test "group help lists every child command" do
    for group <- ~w(projects lists tasks config completions agent) do
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

  test "Task hierarchy help documents the exact inspect invocation" do
    assert Help.render(~w(tasks hierarchy)) =~
             "taskman tasks hierarchy --project PROJECT_ID TASK_ID"
  end

  test "Task search help shows optional Project filtering and summary output" do
    group_help = Help.render(~w(tasks))
    help = Help.render(~w(tasks search))

    assert group_help =~ "tasks search"
    assert help =~ "taskman tasks search QUERY [--project PROJECT_ID] [--json]"
    assert help =~ "--project PROJECT_ID"
    assert help =~ "ID, TITLE, STATUS, PRIORITY, PROJECT, and LOCATION"
    assert help =~ "taskman tasks search publish --project 9"
  end

  test "relationship leaf help names blocker-first source and exact target IDs" do
    show = Help.render(~w(tasks blocking show))
    add = Help.render(~w(tasks blocks add))
    remove = Help.render(~w(tasks blocks remove))

    assert show =~ "taskman tasks blocking show --project PROJECT_ID TASK_ID"
    assert show =~ "Tasks blocking it"

    for help <- [add, remove] do
      assert help =~ "--project PROJECT_ID TASK_ID --target TARGET_TASK_ID"
      assert help =~ "Blocking Task's Project ID"
      assert help =~ "Blocked Task ID, even in another Project"
    end
  end

  test "Task update help explains explicit Done confirmation and force override" do
    help = Help.render(~w(tasks update))

    assert help =~ "--confirm-unresolved-blockers ID,ID"
    assert help =~ "--force-done-with-unresolved-blockers"
    assert help =~ "Done request"
  end

  test "Task comment help discovers commands and opt-in fields" do
    assert Help.render(~w(tasks)) =~ "tasks comments list"
    assert Help.render(~w(tasks comments)) =~ "tasks comments add"
    assert Help.render(~w(tasks comments list)) =~ "--project PROJECT_ID TASK_ID"
    assert Help.render(~w(tasks comments add)) =~ "--text TEXT"
    assert Help.render(~w(tasks comments add)) =~ "--author-name NAME"
    assert Help.render(~w(tasks show)) =~ "--include-comments"
  end

  test "Project creation help documents name-only creation" do
    help = Help.render(~w(projects create))

    assert help =~ "taskman projects create --name NAME"
    assert help =~ "--name NAME"
    assert help =~ "taskman projects create --name CLI"
  end

  test "Task parent help distinguishes parent assignment from removal" do
    create_help = Help.render(~w(tasks create))
    update_help = Help.render(~w(tasks update))

    assert create_help =~
             "taskman tasks create --project PROJECT_ID --title TITLE [--parent TASK_ID]"

    assert update_help =~ "[--parent PARENT_TASK_ID | --no-parent]"
    assert update_help =~ "--parent PARENT_TASK_ID"
    assert update_help =~ "--no-parent"
  end

  test "Task list help documents repeatable statuses and paired sorting" do
    help = Help.render(~w(tasks list))

    assert help =~ "[--status STATUS]... [--sort FIELD --direction asc|desc]"
    assert help =~ "--status STATUS"
    assert help =~ "(repeatable)"
    assert help =~ "--sort FIELD (id|title|status|priority|location)"
    assert help =~ "--direction DIRECTION (asc|desc)"
  end

  test "completion leaf help describes shell output and rejects JSON mode" do
    for shell <- ~w(bash fish) do
      help = Help.render(["completions", shell])

      assert help =~ "shell source"
      assert help =~ "--json is invalid"
      refute help =~ "add --json for one API-compatible JSON envelope"
    end
  end

  test "config leaf help documents secret input, redaction, and authentication status 7" do
    set_key = Help.render(~w(config set-key))
    show = Help.render(~w(config show))

    assert set_key =~ "taskman config set-key"
    assert set_key =~ "7 authentication required"
    assert show =~ "without revealing its key"
  end

  test "every registered leaf has usage and an example" do
    for command <- Registry.commands() do
      help = Help.render(command.path)
      assert help =~ "Usage"
      assert help =~ "Example"
    end
  end

  test "Project help documents identity options and update" do
    create = Help.render(~w(projects create))
    update = Help.render(~w(projects update))

    for option <- ["--description TEXT", "--icon ICON", "--color '#RRGGBB'"] do
      assert create =~ option
      assert update =~ option
    end

    assert update =~ "taskman projects update PROJECT_ID"
    assert update =~ "--name NAME"
    assert update =~ "rocket-launch"
  end
end
