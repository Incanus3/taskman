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
    assert text =~ "Ordinary commands require a running backend"
    assert text =~ "mix do escript.build + escript.install --force"
    assert text =~ "TASKMAN_HOST"
    assert text =~ "TASKMAN_API_KEY_HOST"
    assert text =~ "TASKMAN_API_KEY"
    assert text =~ "config.json"
    assert text =~ "taskman config hosts add personal --url https://taskman.example.com"
    assert text =~ "taskman config hosts set-key personal"
    assert text =~ "one-time"
    assert text =~ "status 7"
    assert text =~ "taskman projects list --json"
    assert text =~ "taskman projects create --name \"My Project\" --json"
    assert text =~ "taskman lists list --project 7 --json"
    assert text =~ "taskman tasks list --project 7 --json"
    assert text =~ "taskman tasks update --project 7 42 --parent 41 --json"
    assert text =~ "taskman tasks hierarchy --project 7 42 --json"
    assert text =~ "taskman agent skill install"
    assert text =~ "taskman --help"

    assert text =~
             "mkdir -p ~/.local/share/bash-completion/completions\ntaskman completions bash > ~/.local/share/bash-completion/completions/taskman"

    assert text =~
             "mkdir -p ~/.config/fish/completions\ntaskman completions fish > ~/.config/fish/completions/taskman.fish"
  end

  for {name, mix_home, mix_xdg} <- [
        {"unset MIX_HOME", nil, nil},
        {"empty MIX_HOME", "", nil},
        {"absolute MIX_HOME with spaces", :absolute, nil},
        {"relative MIX_HOME with spaces", "relative Mix home", nil},
        {"XDG Mix home", nil, "true"}
      ] do
    @mix_home mix_home
    @mix_xdg mix_xdg

    test "installation commands reach the Mix-installed executable with #{name}" do
      root =
        Path.join(
          System.tmp_dir!(),
          "taskman-install-" <> Base.url_encode64(:crypto.strong_rand_bytes(12), padding: false)
        )

      File.mkdir!(root)
      File.chmod!(root, 0o700)
      on_exit(fn -> File.rm_rf!(root) end)
      cwd = Path.join(root, "installation directory")
      home = Path.join(root, "home with spaces")
      File.mkdir_p!(cwd)
      File.mkdir_p!(home)

      # Use the running runtime's real bins; isolated HOME must not redirect mise shims.
      elixir_bin =
        :elixir |> :code.lib_dir() |> to_string() |> Path.join("../../bin") |> Path.expand()

      erlang_bin = Path.join(to_string(:code.root_dir()), "bin")
      runtime_path = Enum.join([elixir_bin, erlang_bin, "/usr/bin", "/bin"], ":")

      mix_home =
        if @mix_home == :absolute, do: Path.join(root, "custom Mix home"), else: @mix_home

      env = [
        {"HOME", home},
        {"MIX_HOME", if(mix_home == "", do: nil, else: mix_home)},
        {"MIX_XDG", @mix_xdg},
        {"XDG_CONFIG_HOME", Path.join(root, "xdg config")},
        {"XDG_CACHE_HOME", Path.join(root, "xdg cache")},
        {"XDG_DATA_HOME", Path.join(root, "xdg data")},
        {"PATH", runtime_path},
        {"ERL_FLAGS", "+S 2:2 +SDcpu 1 +SDio 1"}
      ]

      probe = Path.join(root, "taskman")

      source = Path.join(root, "taskman_probe.erl")

      File.write!(source, """
      -module(taskman_probe).
      -export([main/1]).
      main(_) -> io:format("~s~n", [filename:absname(escript:script_name())]).
      """)

      assert {:ok, :taskman_probe, beam} = :compile.file(String.to_charlist(source), [:binary])

      assert {:ok, {_, archive}} =
               :zip.create(~c"probe.zip", [{~c"taskman_probe.beam", beam}], [:memory])

      assert :ok =
               :escript.create(String.to_charlist(probe), [
                 {:shebang, :default},
                 {:comment, ~c""},
                 {:emu_args, ~c"-escript main taskman_probe"},
                 {:archive, archive}
               ])

      options = [env: env, cd: cwd, stderr_to_stdout: true]

      # Erlang ports normalize an empty env value to unset. Set it inside the child
      # so this case exercises an explicitly empty MIX_HOME in Mix and the shell.
      run = fn command, args ->
        prefix = if mix_home == "", do: ["MIX_HOME="], else: []
        System.cmd("/usr/bin/env", prefix ++ [command | args], options)
      end

      if mix_home == "" do
        assert {~s(""\n), 0} =
                 run.(Path.join(elixir_bin, "elixir"), [
                   "-e",
                   ~s|IO.inspect(System.get_env("MIX_HOME"))|
                 ])
      end

      assert {install_output, 0} =
               run.(Path.join(elixir_bin, "mix"), ["escript.install", "--force", probe])

      assert install_output =~ "creating"

      assert {escripts, 0} =
               run.(
                 Path.join(elixir_bin, "elixir"),
                 ["-e", "IO.write(Path.expand(Mix.path_for(:escripts)))"]
               )

      executable = Path.join(escripts, "taskman")
      assert File.regular?(executable)
      lines = String.split(Onboarding.text(), "\n")
      assignment = Enum.find(lines, &String.starts_with?(&1, "taskman_escripts="))
      path_command = Enum.find(lines, &String.starts_with?(&1, "export PATH="))

      direct_command =
        Enum.find(lines, &(String.starts_with?(&1, "\"") and String.ends_with?(&1, " --help")))

      assert is_binary(path_command)
      assert is_binary(direct_command)

      for invocation <- [direct_command, path_command <> "\ntaskman --help"] do
        command = [assignment, invocation] |> Enum.reject(&is_nil/1) |> Enum.join("\n")
        {output, status} = run.("/bin/sh", ["-c", command])
        assert status == 0, output
        assert String.trim(output) == executable
      end
    end
  end

  test "guidance covers clone context, explicit recovery and legacy binary ordering" do
    text = Onboarding.text()
    assert text =~ "taskman repo bind --host personal --project 7"
    assert text =~ "linked worktrees"
    assert text =~ "both --host and --project"
    assert text =~ "taskman tasks search publish --all-projects"
    assert text =~ "taskman repo unbind"
    assert text =~ "same logical backend"
    assert text =~ "Update the binary before"
    assert text =~ "Legacy"
  end

  test "onboarding JSON mode wraps the same text in one data envelope" do
    result = Taskman.CLI.run(["agent", "onboarding", "--json"])

    assert result.status == 0
    assert result.stderr == ""
    assert %{"data" => %{"onboarding" => text}} = Jason.decode!(result.stdout)
    assert text == Onboarding.text()
  end

  test "Project onboarding describes identity fields and update workflow" do
    text = Onboarding.text()

    assert text =~ "list, inspect, create, and update Projects"
    assert text =~ "Project description, icon, and color"

    assert text =~
             "taskman projects create --name \"My Project\" --description Delivery --icon rocket-launch --color '#6366F1' --json"

    assert text =~ "taskman projects update 7 --description Revised --json"
  end

  test "comment onboarding gives append and verification examples with account attribution" do
    text = Onboarding.text()
    assert text =~ "taskman tasks comments list --project 7 42 --json"
    assert text =~ "taskman tasks comments add --project 7 42 --text"
    assert text =~ "taskman tasks show --project 7 42 --include-comments"
    assert text =~ "posting account"
    assert text =~ "human"
    refute text =~ "comments search"
    refute text =~ "comments edit"
    refute text =~ "comments delete"
    refute text =~ "impersonate"
  end
end
