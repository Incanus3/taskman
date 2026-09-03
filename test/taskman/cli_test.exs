defmodule Taskman.CLITest do
  use ExUnit.Case, async: true

  setup {Req.Test, :verify_on_exit!}

  test "help is an offline successful result" do
    result = Taskman.CLI.run(["tasks", "move", "--help"])

    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout =~ "taskman tasks move --project PROJECT_ID TASK_ID"
    assert result.stdout =~ "--to-list LIST_ID"
    assert result.stdout =~ "--to-project-root"
  end

  test "invalid invocation writes diagnostics to stderr with status 2" do
    result = Taskman.CLI.run(["tasks", "show", "--project", "not-an-id", "1"])

    assert result.status == 2
    assert result.stdout == ""
    assert result.stderr =~ "Invalid invocation"
    assert result.stderr =~ "Usage"
  end

  test "JSON invalid invocations use a single stable error envelope on stderr" do
    result = Taskman.CLI.run(["tasks", "show", "--json", "--project", "not-an-id", "1"])

    assert result.status == 2
    assert result.stdout == ""
    assert %{"error" => %{"code" => "invalid_invocation"}} = Jason.decode!(result.stderr)
  end

  test "version is kept in sync with the Mix project" do
    assert Taskman.CLI.version() == Mix.Project.config()[:version]

    result = Taskman.CLI.run(["--version"])
    assert result.status == 0
    assert result.stderr == ""
    assert String.trim(result.stdout) == Taskman.CLI.version()
  end

  test "completion Bash rejects JSON mode as a local invalid invocation" do
    result = Taskman.CLI.run(["completions", "bash", "--json"])

    assert result.status == 2
    assert result.stdout == ""
    assert %{"error" => %{"code" => "invalid_invocation"}} = Jason.decode!(result.stderr)
  end

  test "completion Fish rejects JSON mode as a local invalid invocation" do
    result = Taskman.CLI.run(["completions", "fish", "--json"])

    assert result.status == 2
    assert result.stdout == ""
    assert %{"error" => %{"code" => "invalid_invocation"}} = Jason.decode!(result.stderr)
  end

  @tag :tmp_dir
  test "ordinary API commands fail locally with status 7 before a missing credential can reach HTTP",
       %{
         tmp_dir: tmp_dir
       } do
    result =
      Taskman.CLI.run(["projects", "list", "--json"],
        config_root: Path.join(tmp_dir, "xdg"),
        env: %{}
      )

    assert result.status == 7
    assert result.stdout == ""

    assert %{"error" => %{"code" => "authentication_required", "message" => message}} =
             Jason.decode!(result.stderr)

    assert message =~ "TASKMAN_API_KEY"
  end

  @tag :tmp_dir
  test "ordinary API commands resolve an environment key and send it only as a bearer header", %{
    tmp_dir: tmp_dir
  } do
    credential = "tm_runner_environment_credential"

    Req.Test.expect(TaskmanCLIRunner, fn conn ->
      assert Plug.Conn.get_req_header(conn, "authorization") == ["Bearer " <> credential]
      refute conn.request_path =~ credential
      Req.Test.json(conn, %{data: []})
    end)

    result =
      Taskman.CLI.run(["projects", "list", "--json"],
        config_root: Path.join(tmp_dir, "xdg"),
        env: %{"TASKMAN_API_KEY" => credential},
        req_options: [plug: {Req.Test, TaskmanCLIRunner}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout == "{\"data\":[]}\n"
    refute result.stdout =~ credential
  end
end
