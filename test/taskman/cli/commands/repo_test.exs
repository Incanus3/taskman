defmodule Taskman.CLI.Commands.RepoTest do
  use ExUnit.Case, async: true
  alias Taskman.CLI.{Config, Fixtures, RepositoryBinding}
  import Taskman.ProjectsFixtures, only: [project_response_fixture: 1]
  setup {Req.Test, :verify_on_exit!}
  @key "tm_b1BWqBOKyDOvG9UmpFc40u2BTKuySXY5HGGKYZGrIf23jgINXtEy2AqT94Y6W7pu_bGXTfd"
  @moduletag :tmp_dir
  setup %{tmp_dir: tmp_dir} do
    repo = Fixtures.git_repo!(tmp_dir, "clone")
    root = Path.join(tmp_dir, "config")

    Fixtures.write_hosts!(root, %{
      format: :v2,
      default_host: "personal",
      hosts: %{"personal" => %{api_url: "https://personal.example.invalid", api_key: @key}}
    })

    %{
      repo: repo,
      root: root,
      opts: [
        cwd: repo.cwd,
        env: %{},
        config_root: root,
        req_options: [plug: {Req.Test, RepoCommands}]
      ]
    }
  end

  test "bind validates matching Project before saving", %{repo: repo, opts: opts} do
    Req.Test.expect(RepoCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/7"
      refute File.exists?(Path.join(repo.common_dir, "taskman/binding.json.lock"))
      Req.Test.json(conn, %{data: project_response_fixture(%{"id" => 7})})
    end)

    opts =
      Keyword.put(opts, :git_runner, fn argv, cwd, env ->
        assert cwd == repo.cwd
        assert env["LC_ALL"] == "C"
        send(self(), {:discovered, argv})

        case argv do
          ["rev-parse", "--is-inside-work-tree"] ->
            {"true\n", 0}

          ["rev-parse", "--path-format=absolute", "--git-common-dir"] ->
            {repo.common_dir <> "\n", 0}
        end
      end)

    result = Taskman.CLI.run(~w(repo bind --host personal --project 7 --json), opts)
    assert result.status == 0
    assert_received {:discovered, ["rev-parse", "--is-inside-work-tree"]}
    assert_received {:discovered, ["rev-parse", "--path-format=absolute", "--git-common-dir"]}
    refute_received {:discovered, _}

    assert Jason.decode!(result.stdout) == %{
             "data" => %{"bound" => true, "host" => "personal", "project_id" => 7}
           }

    assert {:ok, %{host: "personal", project_id: 7}} = RepositoryBinding.read(repo.common_dir)
  end

  test "failed validation preserves the previous binding", %{repo: repo, opts: opts} do
    :ok = RepositoryBinding.put(repo.common_dir, %{version: 1, host: "personal", project_id: 8})

    for response <- [:mismatch, :connection, :domain, :auth, :server] do
      Req.Test.expect(RepoCommands, fn conn ->
        case response do
          :mismatch ->
            Req.Test.json(conn, %{data: project_response_fixture(%{"id" => 9})})

          :connection ->
            Req.Test.transport_error(conn, :econnrefused)

          :domain ->
            conn
            |> Plug.Conn.put_status(404)
            |> Req.Test.json(%{error: %{code: "not_found", message: "Missing"}})

          :auth ->
            conn
            |> Plug.Conn.put_status(401)
            |> Req.Test.json(%{error: %{code: "unauthorized", message: "Denied"}})

          :server ->
            conn
            |> Plug.Conn.put_status(500)
            |> Req.Test.json(%{error: %{code: "internal_error", message: "Failure"}})
        end
      end)

      result = Taskman.CLI.run(~w(repo bind --host personal --project 7 --json), opts)

      assert result.status ==
               %{mismatch: 5, connection: 4, domain: 3, auth: 7, server: 5}[response]

      assert {:ok, %{project_id: 8}} = RepositoryBinding.read(repo.common_dir)
    end
  end

  test "readable binding receipts and inspection remain newline terminated", %{
    repo: repo,
    opts: opts
  } do
    Req.Test.expect(RepoCommands, fn conn ->
      Req.Test.json(conn, %{data: project_response_fixture(%{"id" => 7})})
    end)

    for {argv, stdout} <- [
          {~w(repo show), "Common Git directory: #{repo.common_dir}\nBinding: unbound\n"},
          {~w(repo bind --host personal --project 7),
           "Bound repository to personal Project 7.\n"},
          {~w(repo show),
           "Common Git directory: #{repo.common_dir}\nBinding: personal Project 7; host registered: true\n"},
          {~w(repo unbind), "Removed repository binding.\n"},
          {~w(repo unbind), "Removed repository binding.\n"}
        ] do
      result = Taskman.CLI.run(argv, opts)
      assert result.status == 0
      assert result.stdout == stdout
      assert result.stderr == ""
    end
  end

  test "repo show exposes dangling hosts offline", %{repo: repo, root: root, opts: opts} do
    :ok = RepositoryBinding.put(repo.common_dir, %{version: 1, host: "personal", project_id: 7})
    :ok = Config.remove_host("personal", config_root: root)

    opts =
      Keyword.put(opts, :env, %{"TASKMAN_API_URL" => "invalid", "TASKMAN_API_KEY" => "secret"})

    result = Taskman.CLI.run(~w(repo show --json), opts)
    assert result.status == 0

    assert Jason.decode!(result.stdout) == %{
             "data" => %{
               "common_git_dir" => repo.common_dir,
               "binding" => %{"host" => "personal", "project_id" => 7},
               "host_registered" => false
             }
           }

    File.write!(Config.path(opts), "invalid")
    assert Taskman.CLI.run(~w(repo show --json), opts).status == 2
    File.write!(Path.join(repo.common_dir, "taskman/binding.json"), "invalid")

    assert Jason.decode!(Taskman.CLI.run(~w(repo unbind --json), opts).stdout) == %{
             "data" => %{"unbound" => true}
           }

    assert Jason.decode!(Taskman.CLI.run(~w(repo show --json), opts).stdout) == %{
             "data" => %{
               "common_git_dir" => repo.common_dir,
               "binding" => nil,
               "host_registered" => nil
             }
           }
  end

  test "repo commands require a working tree and bind requires explicit selectors", %{opts: opts} do
    for argv <- [~w(repo bind --host personal), ~w(repo bind --project 7)] do
      assert Taskman.CLI.run(argv, opts).status == 2
    end

    for argv <- [~w(repo show), ~w(repo unbind), ~w(repo bind --host personal --project 7)] do
      assert Taskman.CLI.run(argv, Keyword.put(opts, :git_runner, &Fixtures.outside_git/3)).status ==
               2
    end
  end
end
