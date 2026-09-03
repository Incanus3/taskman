defmodule Taskman.CLI.Commands.ConfigTest do
  use ExUnit.Case, async: true

  alias Taskman.FakeTerminal

  @api_key "tm_prompted_config_credential"

  @tag :tmp_dir
  test "set-url, secret-prompted set-key, and show manage private configuration", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")

    set_url =
      Taskman.CLI.run(["config", "set-url", "https://taskman.example"], config_root: root)

    assert set_url.status == 0
    assert set_url.stderr == ""
    assert set_url.stdout == "Saved Taskman API URL.\n"

    FakeTerminal.set_responses([], [@api_key])

    set_key =
      Taskman.CLI.run(["config", "set-key"],
        config_root: root,
        terminal: FakeTerminal
      )

    assert set_key.status == 0
    assert set_key.stderr == ""
    assert set_key.stdout == "Saved Taskman API key.\n"
    assert FakeTerminal.prompts() == [secret: "Taskman API key: "]
    refute set_key.stdout =~ @api_key
    refute set_key.stderr =~ @api_key

    show = Taskman.CLI.run(["config", "show"], config_root: root)

    assert show.status == 0
    assert show.stderr == ""
    assert show.stdout == "API URL: https://taskman.example\nAPI key: configured\n"
    refute show.stdout =~ @api_key
  end

  @tag :tmp_dir
  test "config show applies environment overrides and JSON never reveals the key", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")
    File.mkdir_p!(Path.join(root, "taskman"))

    File.write!(
      Path.join([root, "taskman", "config.json"]),
      Jason.encode!(%{"api_url" => "https://file.example", "api_key" => @api_key})
    )

    :ok = File.chmod(Path.join([root, "taskman", "config.json"]), 0o600)

    result =
      Taskman.CLI.run(["config", "show", "--json"],
        config_root: root,
        env: %{
          "TASKMAN_API_URL" => "https://environment.example",
          "TASKMAN_API_KEY" => "tm_environment_config_credential"
        }
      )

    assert result.status == 0

    assert %{
             "data" => %{"api_url" => "https://environment.example", "api_key_configured" => true}
           } =
             Jason.decode!(result.stdout)

    refute result.stdout =~ @api_key
    refute result.stdout =~ "tm_environment_config_credential"
  end

  test "set-key accepts no positional value or api-key option" do
    assert %Taskman.CLI.Result{status: 2, stderr: positional_error} =
             Taskman.CLI.run(["config", "set-key", "tm_forbidden"])

    assert positional_error =~ "Expected 0 argument(s), got 1"

    assert %Taskman.CLI.Result{status: 2, stderr: option_error} =
             Taskman.CLI.run(["config", "set-key", "--api-key", "tm_forbidden"])

    assert option_error =~ "Unknown option --api-key"
    refute option_error =~ "tm_forbidden"
  end

  @tag :tmp_dir
  test "a missing secret input is reported without starting an API request", %{tmp_dir: tmp_dir} do
    FakeTerminal.set_responses([], [{:error, :input_unavailable}])

    result =
      Taskman.CLI.run(["config", "set-key"],
        config_root: Path.join(tmp_dir, "xdg"),
        terminal: FakeTerminal,
        env: %{}
      )

    assert result.status == 5
    assert result.stdout == ""
    assert result.stderr =~ "input_unavailable"
  end
end
