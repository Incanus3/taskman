defmodule Taskman.CLI.EntrypointTest do
  use ExUnit.Case, async: true

  setup {Req.Test, :verify_on_exit!}

  test "help is an offline successful result" do
    result = Taskman.CLI.run(["tasks", "move", "--help"])

    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout =~ "taskman tasks move [--project PROJECT_ID] TASK_ID"
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
    Taskman.CLI.Fixtures.write_hosts!(Path.join(tmp_dir, "xdg"), %{
      format: :v2,
      default_host: "personal",
      hosts: %{"personal" => %{api_url: "https://personal.example.invalid", api_key: nil}}
    })

    result =
      Taskman.CLI.run(["projects", "list", "--json"],
        config_root: Path.join(tmp_dir, "xdg"),
        git_runner: &Taskman.CLI.Fixtures.outside_git/3,
        env: %{}
      )

    assert result.status == 7
    assert result.stdout == ""

    assert %{"error" => %{"code" => "authentication_required", "message" => message}} =
             Jason.decode!(result.stderr)

    assert message =~ "API key"
  end

  @tag :tmp_dir
  test "ordinary API commands resolve an environment key and send it only as a bearer header", %{
    tmp_dir: tmp_dir
  } do
    credential = "tm_b1BWqBOKyDOvG9UmpFc40u2BTKuySXY5HGGKYZGrIf23jgINXtEy2AqT94Y6W7pu_bGXTfd"

    Req.Test.expect(TaskmanCLIRunner, fn conn ->
      assert Plug.Conn.get_req_header(conn, "authorization") == ["Bearer " <> credential]
      refute conn.request_path =~ credential
      Req.Test.json(conn, %{data: []})
    end)

    Taskman.CLI.Fixtures.write_hosts!(Path.join(tmp_dir, "xdg"), %{
      format: :v2,
      default_host: "personal",
      hosts: %{"personal" => %{api_url: "https://personal.example.invalid", api_key: nil}}
    })

    result =
      Taskman.CLI.run(["projects", "list", "--json"],
        config_root: Path.join(tmp_dir, "xdg"),
        git_runner: &Taskman.CLI.Fixtures.outside_git/3,
        env: %{"TASKMAN_API_KEY" => credential, "TASKMAN_API_KEY_HOST" => "personal"},
        req_options: [plug: {Req.Test, TaskmanCLIRunner}]
      )

    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout == "{\"data\":[]}\n"
    refute result.stdout =~ credential
  end

  @tag :tmp_dir
  test "ordinary CLI commands seal the resolved key against runtime authentication overrides", %{
    tmp_dir: tmp_dir
  } do
    resolved = "tm_ResolvedPayload_123456"
    injected = "tm_InjectedPayload_654321"

    Req.Test.expect(TaskmanCLIRunner, fn conn ->
      assert conn.host == "personal.example.invalid"
      assert Plug.Conn.get_req_header(conn, "authorization") == ["Bearer " <> resolved]
      Req.Test.json(conn, %{data: []})
    end)

    Taskman.CLI.Fixtures.write_hosts!(Path.join(tmp_dir, "xdg"), %{
      format: :v2,
      default_host: "personal",
      hosts: %{"personal" => %{api_url: "https://personal.example.invalid", api_key: nil}}
    })

    result =
      Taskman.CLI.run(["projects", "list", "--json"],
        api_key: injected,
        config_root: Path.join(tmp_dir, "xdg"),
        git_runner: &Taskman.CLI.Fixtures.outside_git/3,
        env: %{"TASKMAN_API_KEY" => resolved, "TASKMAN_API_KEY_HOST" => "personal"},
        req_options: [
          plug: {Req.Test, TaskmanCLIRunner},
          auth: {:bearer, injected},
          headers: [{"authorization", "Bearer " <> injected}],
          request_steps: [
            rewrite_actor: fn request ->
              Req.Request.put_header(request, "authorization", "Bearer " <> injected)
            end
          ]
        ]
      )

    assert result.status == 0
    assert result.stdout == "{\"data\":[]}\n"
  end

  @tag :tmp_dir
  test "one invocation reuses its resolved context across requests", %{tmp_dir: tmp_dir} do
    alias Taskman.CLI.{Config, Context, Client, Fixtures}
    alias Taskman.CLI.Execution.Parser
    root = Path.join(tmp_dir, "config")
    original = Fixtures.resolved_context()

    Fixtures.write_hosts!(root, %{
      format: :v2,
      default_host: "personal",
      hosts: %{"personal" => %{api_url: original.api_url, api_key: original.api_key}}
    })

    opts = [
      config_root: root,
      env: %{},
      git_runner: &Fixtures.outside_git/3,
      req_options: [plug: {Req.Test, SnapshotRequests}]
    ]

    assert {:ok, invocation} = Parser.parse(~w(projects list --json))
    assert {:ok, context} = Context.resolve(invocation, opts)
    sealed_opts = Keyword.put(opts, :resolved_context, context)

    for request <- 1..2 do
      Req.Test.expect(SnapshotRequests, fn conn ->
        assert conn.host == "personal.example.invalid"
        assert Plug.Conn.get_req_header(conn, "authorization") == ["Bearer " <> original.api_key]
        Req.Test.json(conn, %{data: []})
      end)

      assert {:ok, []} = Client.request(:get, "/api/v1/projects", [], sealed_opts)

      if request == 1 do
        :ok = Config.set_host_url("personal", "https://work.example.invalid", opts)
        :ok = Config.set_host_key("personal", "tm_WorkPayload_123456", opts)
      end
    end

    Req.Test.expect(SnapshotRequests, fn conn ->
      assert conn.host == "work.example.invalid"
      assert Plug.Conn.get_req_header(conn, "authorization") == ["Bearer tm_WorkPayload_123456"]
      Req.Test.json(conn, %{data: []})
    end)

    result =
      Taskman.CLI.run(
        ~w(projects list --json),
        Keyword.put(sealed_opts, :api_url, "https://injected.example.invalid")
      )

    assert result.status == 0
  end

  @tag :tmp_dir
  test "complete explicit targets reach the selected host without Git discovery", %{
    tmp_dir: tmp_dir
  } do
    alias Taskman.CLI.Fixtures
    root = Path.join(tmp_dir, "config")

    Fixtures.write_hosts!(root, %{
      format: :v2,
      default_host: nil,
      hosts: %{
        "work" => %{api_url: "https://work.example.invalid", api_key: "tm_WorkPayload_123456"}
      }
    })

    opts = %{
      config_root: root,
      env: %{},
      git_runner: fn _, _, _ -> flunk("complete explicit targets must bypass Git") end,
      resolved_context: Fixtures.resolved_context(),
      api_url: "https://injected.example.invalid",
      api_key: "tm_InjectedPayload_654321",
      req_options: [plug: {Req.Test, ExplicitRequests}]
    }

    for {argv, path, query} <- [
          {~w(projects list --host work --json), "/api/v1/projects", %{}},
          {~w(lists list --host work --project 9 --json), "/api/v1/projects/9/lists", %{}},
          {~w(tasks search publish --host work --all-projects --json), "/api/v1/tasks/search",
           %{"q" => "publish"}}
        ] do
      Req.Test.expect(ExplicitRequests, fn conn ->
        conn = Plug.Conn.fetch_query_params(conn)
        assert conn.host == "work.example.invalid"
        assert conn.request_path == path
        assert conn.query_params == query
        assert Plug.Conn.get_req_header(conn, "authorization") == ["Bearer tm_WorkPayload_123456"]
        Req.Test.json(conn, %{data: []})
      end)

      result = Taskman.CLI.run(argv, opts)
      assert result.status == 0
      assert result.stdout == "{\"data\":[]}\n"
      assert result.stderr == ""
    end
  end

  @tag :tmp_dir
  test "each invocation uses its supplied working directory and environment", %{tmp_dir: tmp_dir} do
    alias Taskman.CLI.{Fixtures, RepositoryBinding}

    for {name, project, host, credential, shape} <- [
          {"first", 7, "personal", "tm_PersonalPayload_123456", :map},
          {"second", 9, "work", "tm_WorkPayload_654321", :keyword}
        ] do
      repo = Fixtures.git_repo!(tmp_dir, name)
      root = Path.join(tmp_dir, name <> "-config")

      Fixtures.write_hosts!(root, %{
        format: :v2,
        default_host: nil,
        hosts: %{host => %{api_url: "https://#{host}.example.invalid", api_key: credential}}
      })

      :ok = RepositoryBinding.put(repo.common_dir, %{version: 1, host: host, project_id: project})

      Req.Test.expect(PerCallRequests, fn conn ->
        assert conn.host == "#{host}.example.invalid"
        assert conn.request_path == "/api/v1/projects/#{project}/tasks"
        assert Plug.Conn.get_req_header(conn, "authorization") == ["Bearer " <> credential]
        Req.Test.json(conn, %{data: []})
      end)

      env = %{
        "XDG_CONFIG_HOME" => root,
        "TASKMAN_HOST" => "INVALID",
        "GIT_CEILING_DIRECTORIES" => tmp_dir
      }

      env = if shape == :map, do: Map.to_list(env), else: env

      opts = %{
        cwd: repo.cwd,
        env: env,
        req_options: [plug: {Req.Test, PerCallRequests}]
      }

      opts = if shape == :keyword, do: Map.to_list(opts), else: opts
      common_dir = repo.common_dir
      assert {:ok, ^common_dir} = RepositoryBinding.discover(opts)

      result = Taskman.CLI.run(~w(tasks list --json), opts)
      assert result.status == 0
      assert result.stdout == "{\"data\":[]}\n"
      assert result.stderr == ""
    end
  end

  @tag :tmp_dir
  test "utilities work independently of connection configuration", %{tmp_dir: tmp_dir} do
    alias Taskman.CLI.Fixtures
    root = Path.join(tmp_dir, "config")
    path = Fixtures.legacy_config!(root, %{})
    File.write!(path, "malformed")

    opts = %{
      config_root: root,
      skills_root: Path.join(tmp_dir, "skills"),
      env: %{"TASKMAN_API_URL" => "invalid", "TASKMAN_API_KEY" => "invalid"},
      git_runner: fn _, _, _ -> flunk("utilities must not discover Git") end
    }

    for argv <- [
          ~w(completions bash),
          ~w(completions fish),
          ~w(agent onboarding --json),
          ~w(agent skill install --json)
        ] do
      result = Taskman.CLI.run(argv, opts)
      assert result.status == 0
      assert result.stdout != ""
      assert result.stderr == ""
    end
  end

  @tag :tmp_dir
  test "local recovery ignores transport variables", %{tmp_dir: tmp_dir} do
    alias Taskman.CLI.Fixtures
    repo = Fixtures.git_repo!(tmp_dir, "clone")

    opts = [
      config_root: Path.join(tmp_dir, "config"),
      cwd: repo.cwd,
      env: %{
        "TASKMAN_API_URL" => "private invalid",
        "TASKMAN_API_KEY" => "private invalid",
        "TASKMAN_HOST" => "INVALID"
      }
    ]

    for argv <- [
          ~w(config hosts add personal --url https://personal.example.invalid --json),
          ~w(config hosts list --json),
          ~w(repo show --json),
          ~w(repo unbind --json)
        ] do
      result = Taskman.CLI.run(argv, opts)
      assert result.status == 0
      refute result.stdout =~ "private invalid"
      assert result.stderr == ""
    end

    assert Taskman.CLI.run(~w(config show --json), opts).status == 2
  end

  @tag :tmp_dir
  test "bound resource reads writes search and positional Project IDs retain API scope", %{
    tmp_dir: tmp_dir
  } do
    alias Taskman.CLI.{Fixtures, RepositoryBinding}
    import Taskman.ProjectsFixtures, only: [project_response_fixture: 1]
    repo = Fixtures.git_repo!(tmp_dir, "clone")
    root = Path.join(tmp_dir, "config")
    context = Fixtures.resolved_context()

    Fixtures.write_hosts!(root, %{
      format: :v2,
      default_host: "personal",
      hosts: %{
        "personal" => %{api_url: context.api_url, api_key: context.api_key},
        "work" => %{api_url: "https://work.example.invalid", api_key: "tm_WorkPayload_123456"}
      }
    })

    :ok = RepositoryBinding.put(repo.common_dir, %{version: 1, host: "personal", project_id: 7})

    opts = [
      config_root: root,
      cwd: repo.cwd,
      env: %{},
      req_options: [plug: {Req.Test, BoundRequests}]
    ]

    task = %{
      id: 42,
      project_id: 7,
      list_id: nil,
      parent_task_id: nil,
      title: "Bound task",
      description: "",
      status: "pending",
      priority: "none",
      due_at: nil,
      location: %{kind: "project", list_id: nil, path: []}
    }

    for {argv, method, path, data} <- [
          {~w(tasks show 42 --json), "GET", "/api/v1/projects/7/tasks/42", task},
          {["tasks", "update", "42", "--title", "Updated", "--json"], "PATCH",
           "/api/v1/projects/7/tasks/42", %{task | title: "Updated"}},
          {~w(projects show 9 --json), "GET", "/api/v1/projects/9",
           project_response_fixture(%{"id" => 9})},
          {~w(projects update 9 --name Other --json), "PATCH", "/api/v1/projects/9",
           project_response_fixture(%{"id" => 9, "name" => "Other"})}
        ] do
      Req.Test.expect(BoundRequests, fn conn ->
        assert conn.method == method
        assert conn.request_path == path
        assert conn.host == "personal.example.invalid"
        Req.Test.json(conn, %{data: data})
      end)

      assert Taskman.CLI.run(argv, opts).status == 0
    end

    Req.Test.expect(BoundRequests, fn conn ->
      assert conn.request_path == "/api/v1/projects/7/tasks/42/blocks/99"
      assert conn.method == "POST"
      assert conn.host == "personal.example.invalid"

      summary = fn id, project ->
        %{
          id: id,
          title: "Task",
          status: "pending",
          priority: "none",
          project_id: project,
          project_name: "Project",
          location: %{kind: "project", list_id: nil, path: []}
        }
      end

      Req.Test.json(conn, %{
        data: %{blocking_task: summary.(42, 7), blocked_task: summary.(99, 9)}
      })
    end)

    assert Taskman.CLI.run(~w(tasks blocks add 42 --target 99 --json), opts).status == 0

    for {argv, host, project} <- [
          {~w(tasks search publish --json), "personal.example.invalid", "7"},
          {~w(tasks search publish --all-projects --json), "personal.example.invalid", nil},
          {~w(tasks search publish --host work --all-projects --json), "work.example.invalid",
           nil}
        ] do
      Req.Test.expect(BoundRequests, fn conn ->
        conn = Plug.Conn.fetch_query_params(conn)
        assert conn.request_path == "/api/v1/tasks/search"
        assert conn.host == host
        assert conn.query_params["project_id"] == project
        Req.Test.json(conn, %{data: []})
      end)

      assert Taskman.CLI.run(argv, opts).status == 0
    end

    for argv <- [
          ~w(tasks list --project 7 --json),
          ~w(tasks list --host personal --json),
          ~w(tasks search publish --all-projects --project 7 --json)
        ] do
      result = Taskman.CLI.run(argv, opts)
      assert result.status == 2
      assert Jason.decode!(result.stderr)["error"]["code"] == "invalid_invocation"
    end
  end
end
