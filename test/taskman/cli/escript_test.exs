defmodule Taskman.CLI.EscriptTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.{Fixtures, RepositoryBinding}

  @moduletag :external_escript
  @binary System.get_env("TASKMAN_TEST_ESCRIPT", "")
  if @binary in [nil, ""], do: @moduletag(skip: "requires externally built TASKMAN_TEST_ESCRIPT")

  setup do
    root =
      Path.join(
        System.tmp_dir!(),
        "taskman-escript-" <> Base.url_encode64(:crypto.strong_rand_bytes(12), padding: false)
      )

    File.mkdir!(root)
    File.chmod!(root, 0o700)
    on_exit(fn -> File.rm_rf!(root) end)
    assert Path.type(@binary) == :absolute
    assert File.regular?(@binary)
    cwd = Path.join(root, "outside Git")
    home = Path.join(root, "home")
    xdg = Path.join(root, "xdg")
    File.mkdir_p!(cwd)
    File.mkdir_p!(home)

    unset =
      for {name, _} <- System.get_env(),
          String.starts_with?(name, ["TASKMAN_", "GIT_"]),
          do: {name, nil}

    env =
      unset ++
        [
          {"HOME", home},
          {"XDG_CONFIG_HOME", xdg},
          {"GIT_CONFIG_GLOBAL", "/dev/null"},
          {"GIT_CONFIG_NOSYSTEM", "1"},
          {"ERL_FLAGS", "+S 2:2 +SDcpu 1 +SDio 1"}
        ]

    assert {:ok, :outside_work_tree} = RepositoryBinding.discover(cwd: cwd, env: env)
    %{cwd: cwd, env: env, root: root, xdg: xdg}
  end

  test "built executable manages named hosts and effective context without a backend", fixture do
    {stdout, status} =
      System.cmd(@binary, ["config", "hosts", "list", "--json"],
        cd: fixture.cwd,
        env: fixture.env
      )

    assert status == 0
    assert Jason.decode!(stdout) == %{"data" => []}
    assert {version, 0} = command(fixture, ["--version"])
    assert version == Taskman.CLI.version() <> "\n"
    assert {help, 0} = command(fixture, ["--help"])
    assert help =~ "TASKMAN_API_KEY_HOST"
    assert help =~ "taskman repo"

    assert data(fixture, ~w(config hosts add personal --url https://taskman.example.invalid)) ==
             %{"saved" => true}

    assert data(fixture, ~w(config hosts show personal)) == %{
             "name" => "personal",
             "api_url" => "https://taskman.example.invalid",
             "api_key_configured" => false,
             "default" => false
           }

    assert data(fixture, ~w(config default-host personal)) == %{"saved" => true}

    assert data(fixture, ~w(config show)) == %{
             "config_format" => "v2",
             "default_host" => "personal",
             "host" => "personal",
             "api_url" => "https://taskman.example.invalid",
             "api_key_configured" => false,
             "project_id" => nil,
             "sources" => %{"host" => "default", "project" => "none", "key" => "none"}
           }

    assert data(fixture, ~w(config hosts set-url personal --url https://moved.example.invalid)) ==
             %{
               "saved" => true,
               "message" =>
                 "All repositories bound to this host use the updated URL. API key retained."
             }

    assert data(fixture, ~w(config hosts list)) == [
             %{
               "name" => "personal",
               "api_url" => "https://moved.example.invalid",
               "api_key_configured" => false,
               "default" => true
             }
           ]

    assert data(fixture, ~w(config hosts remove personal)) == %{"removed" => true}
    assert data(fixture, ~w(config show))["host"] == nil
    persisted = Jason.decode!(File.read!(Path.join([fixture.xdg, "taskman", "config.json"])))
    assert persisted == %{"version" => 2, "hosts" => %{}}

    {error, 2} = command(fixture, ~w(config hosts show personal --json), stderr_to_stdout: true)
    assert %{"error" => %{"code" => "invalid_invocation"}} = Jason.decode!(error)
  end

  test "built executable inspects and unbinds a dangling clone reference offline", fixture do
    repo = Fixtures.git_repo!(fixture.root, "disposable clone")
    dir = Path.join(repo.common_dir, "taskman")
    File.mkdir_p!(dir)
    binding = Path.join(dir, "binding.json")
    File.write!(binding, ~s({"version":1,"host":"missing","project_id":7}\n))
    fixture = %{fixture | cwd: repo.cwd}
    # Recovery ignores stale or incomplete transport-selection settings.
    fixture = %{
      fixture
      | env: fixture.env ++ [{"TASKMAN_API_URL", "retired"}, {"TASKMAN_API_KEY_HOST", "missing"}]
    }

    assert data(fixture, ~w(repo show)) == %{
             "common_git_dir" => repo.common_dir,
             "binding" => %{"host" => "missing", "project_id" => 7},
             "host_registered" => false
           }

    assert data(fixture, ~w(repo unbind)) == %{"unbound" => true}
    refute File.exists?(binding)

    assert data(fixture, ~w(repo show)) == %{
             "common_git_dir" => repo.common_dir,
             "binding" => nil,
             "host_registered" => nil
           }

    assert data(fixture, ~w(repo unbind)) == %{"unbound" => true}
  end

  test "built executable reads legacy state without writes then upgrades explicitly", fixture do
    path = Fixtures.legacy_config!(fixture.xdg, %{api_url: "https://legacy.example.invalid"})
    bytes = File.read!(path)
    assert data(fixture, ~w(config show))["config_format"] == "legacy"
    assert File.read!(path) == bytes

    assert data(fixture, ~w(config hosts add personal --url https://new.example.invalid)) == %{
             "saved" => true
           }

    assert Jason.decode!(File.read!(path)) == %{
             "version" => 2,
             "default_host" => "default",
             "hosts" => %{
               "default" => %{"api_url" => "https://legacy.example.invalid"},
               "personal" => %{"api_url" => "https://new.example.invalid"}
             }
           }
  end

  defp command(fixture, args, options \\ []),
    do: System.cmd(@binary, args, [cd: fixture.cwd, env: fixture.env] ++ options)

  defp data(fixture, args) do
    {stdout, status} = command(fixture, args ++ ["--json"])
    assert status == 0
    assert String.ends_with?(stdout, "\n")
    assert %{"data" => data} = Jason.decode!(stdout)
    data
  end
end
