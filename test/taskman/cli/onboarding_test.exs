defmodule Taskman.CLI.OnboardingTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.Onboarding

  test "explains installation, backend, configuration, workflows, and agent usage offline" do
    text = Onboarding.text()

    assert text =~ "Taskman"
    assert text =~ "Projects"
    assert text =~ "Lists"
    assert text =~ "Tasks"
    assert text =~ "mix phx.server"
    assert text =~ "ordinary commands require a running backend"
    assert text =~ "mix do escript.build + escript.install --force"
    assert text =~ "~/.mix/escripts/taskman"
    assert text =~ "export PATH=\"$HOME/.mix/escripts:$PATH\""
    assert text =~ "http://localhost:4000"
    assert text =~ "TASKMAN_API_URL"
    assert text =~ "--api-url"
    assert text =~ "taskman projects list --json"
    assert text =~ "taskman lists list --project 7 --json"
    assert text =~ "taskman tasks list --project 7 --json"
    assert text =~ "taskman agent skill install"
    assert text =~ "taskman --help"

    assert text =~
             "mkdir -p ~/.local/share/bash-completion/completions\ntaskman completions bash > ~/.local/share/bash-completion/completions/taskman"

    assert text =~
             "mkdir -p ~/.config/fish/completions\ntaskman completions fish > ~/.config/fish/completions/taskman.fish"
  end

  test "onboarding JSON mode wraps the same text in one data envelope" do
    result = Taskman.CLI.run(["agent", "onboarding", "--json"])

    assert result.status == 0
    assert result.stderr == ""
    assert %{"data" => %{"onboarding" => text}} = Jason.decode!(result.stdout)
    assert text == Onboarding.text()
  end
end
