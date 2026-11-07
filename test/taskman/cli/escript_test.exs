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

  @tag timeout: 120_000
  test "built executable manages hosts offline and reads activity through the API", fixture do
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

    for leaf <- ~w(create update list) do
      assert {help, 0} = command(fixture, ["tasks", leaf, "--help"])

      assert help =~
               "icebox|pending|in_analysis|in_progress|in_review|done|not_planned|will_not_do"
    end

    for shell <- ~w(bash fish) do
      assert {completion, 0} = command(fixture, ["completions", shell])
      assert completion =~ "not_planned"
      assert completion =~ "in_analysis"
    end

    {error, 2} =
      command(fixture, ~w(tasks update 42 --status unknown --json), stderr_to_stdout: true)

    assert %{"error" => %{"code" => "invalid_invocation"}} = Jason.decode!(error)

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

    server =
      start_supervised!(
        {Bandit, plug: {__MODULE__, self()}, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)

    Fixtures.write_hosts!(fixture.xdg, %{
      format: :v2,
      default_host: "personal",
      hosts: %{
        "personal" => %{
          api_url: "http://127.0.0.1:#{port}",
          api_key: Fixtures.resolved_context().api_key
        }
      }
    })

    assert data(fixture, ~w(tasks activity list --project 7 42)) == [Fixtures.activity_entry()]
    assert_receive {:activity_request, "", [authorization]}
    assert authorization == "Bearer " <> Fixtures.resolved_context().api_key

    assert {mixed, 0} =
             command(fixture, ~w(tasks activity list --project 7 42 --include-comments))

    assert mixed =~ ~r/Priority changed from low to high.*Research agent.*Review notes/s
    assert_receive {:activity_request, "include_comments=true", [_authorization]}
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

  def init(test_pid), do: test_pid

  def call(conn, test_pid) do
    if conn.method == "GET" and conn.request_path == "/api/v1/projects/7/tasks/42/activity" do
      send(
        test_pid,
        {:activity_request, conn.query_string, Plug.Conn.get_req_header(conn, "authorization")}
      )

      entries =
        [Fixtures.activity_entry()] ++
          if(conn.query_string == "include_comments=true",
            do: [Fixtures.activity_comment()],
            else: []
          )

      conn
      |> Plug.Conn.put_resp_content_type("application/json")
      |> Plug.Conn.send_resp(200, Jason.encode!(%{data: entries}))
    else
      Plug.Conn.send_resp(conn, 404, "unexpected request")
    end
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
