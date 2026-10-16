defmodule Taskman.CLI.ContextTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.{Context, Fixtures, RepositoryBinding}
  alias Taskman.CLI.Execution.Invocation
  alias Taskman.CLI.Registry.Command

  @personal_key "tm_personal_111"
  @work_key "tm_work_222"
  @injected_key "tm_injected_333"

  setup do
    tmp_dir =
      Path.join(System.tmp_dir!(), "context-#{System.unique_integer([:positive, :monotonic])}")

    File.mkdir_p!(tmp_dir)
    on_exit(fn -> File.rm_rf!(tmp_dir) end)
    repo = Fixtures.git_repo!(tmp_dir, "clone")
    root = Path.join(tmp_dir, "config")

    Fixtures.write_hosts!(root, %{
      format: :v2,
      default_host: "work",
      hosts: %{
        "personal" => %{api_url: "https://personal.example.invalid", api_key: @personal_key},
        "work" => %{api_url: "https://work.example.invalid", api_key: @work_key},
        "alias" => %{api_url: "https://personal.example.invalid", api_key: nil}
      }
    })

    {:ok,
     opts: [config_root: root, cwd: repo.cwd, env: %{}], repo: repo, root: root, tmp_dir: tmp_dir}
  end

  test "repository target wins over shell selection but credentials must match", %{
    opts: opts,
    repo: repo
  } do
    bind!(repo)
    shell = with_env(opts, %{"TASKMAN_HOST" => "work"})
    assert {:ok, context} = Context.resolve(invocation(:project_required), shell)

    assert {context.host, context.project_id, context.api_url, context.api_key} ==
             {"personal", 7, "https://personal.example.invalid", @personal_key}

    assert context.sources == %{host: :repository, project: :repository, key: :stored}

    assert {:ok, context} =
             Context.resolve(invocation(:project_required, %{project: 9}, %{host: "work"}), shell)

    assert {context.host, context.project_id, context.api_key} == {"work", 9, @work_key}
    assert context.sources == %{host: :flag, project: :flag, key: :stored}

    assert {:ok, context} =
             Context.resolve(
               invocation(:project_required, %{project: 9}, %{host: "work"}),
               with_env(opts, %{"TASKMAN_HOST" => "INVALID secret selector"})
             )

    assert context.host == "work"

    assert {:ok, context} =
             Context.resolve(
               invocation(:project_required),
               with_env(opts, %{"TASKMAN_HOST" => "INVALID secret selector"})
             )

    assert context.host == "personal"

    assert :ok = RepositoryBinding.unbind(repo.common_dir)
    assert {:ok, context} = Context.resolve(invocation(:project_required, %{project: 9}), shell)
    assert context.sources == %{host: :environment, project: :flag, key: :stored}
    assert {:ok, context} = Context.resolve(invocation(:project_required, %{project: 9}), opts)
    assert context.sources == %{host: :default, project: :flag, key: :stored}

    assert {:error, :invalid_configuration, _} =
             Context.resolve(
               invocation(:host_only),
               with_env(opts, %{"TASKMAN_HOST" => "missing"})
             )

    assert {:error, :invalid_configuration, _} =
             Context.resolve(
               invocation(:host_only),
               with_env(opts, %{"TASKMAN_HOST" => "INVALID secret selector"})
             )

    assert {:error, :invalid_configuration, _} =
             Context.resolve(invocation(:host_only, %{}, %{host: "missing"}), opts)
  end

  test "injected credentials require a complete matching name pair without stored fallback", %{
    opts: opts,
    repo: repo
  } do
    bind!(repo)

    for env <- [
          %{"TASKMAN_API_KEY" => @injected_key},
          %{"TASKMAN_API_KEY_HOST" => "personal"},
          %{"TASKMAN_API_KEY" => "", "TASKMAN_API_KEY_HOST" => "personal"},
          %{"TASKMAN_API_KEY" => @injected_key, "TASKMAN_API_KEY_HOST" => ""},
          %{"TASKMAN_API_KEY" => @injected_key, "TASKMAN_API_KEY_HOST" => "work"},
          %{"TASKMAN_API_KEY" => @injected_key, "TASKMAN_API_KEY_HOST" => "alias"},
          %{"TASKMAN_API_KEY" => "secret invalid key", "TASKMAN_API_KEY_HOST" => "personal"},
          %{"TASKMAN_API_KEY" => @injected_key, "TASKMAN_API_KEY_HOST" => "INVALID secret label"}
        ] do
      assert {:error, :invalid_configuration, message} =
               Context.resolve(invocation(:project_required), with_env(opts, env))

      refute message =~ @injected_key
      refute message =~ "secret"
    end

    assert {:ok, context} =
             Context.resolve(
               invocation(:project_required),
               with_env(opts, %{
                 "TASKMAN_API_KEY" => @injected_key,
                 "TASKMAN_API_KEY_HOST" => "personal"
               })
             )

    assert context.api_key == @injected_key
    assert context.sources.key == :environment

    for env <- [%{}, %{"TASKMAN_API_KEY" => "", "TASKMAN_API_KEY_HOST" => ""}] do
      assert {:ok, context} = Context.resolve(invocation(:project_required), with_env(opts, env))
      assert context.api_key == @personal_key
      assert context.sources.key == :stored
    end

    assert {:error, :authentication_required, _} =
             Context.resolve(invocation(:host_only, %{}, %{host: "alias"}), opts)

    assert {:ok, context} =
             Context.resolve(
               invocation(:host_only, %{}, %{host: "alias"}),
               with_env(opts, %{
                 "TASKMAN_API_KEY" => @injected_key,
                 "TASKMAN_API_KEY_HOST" => "alias"
               })
             )

    assert {context.host, context.api_key, context.sources.key} ==
             {"alias", @injected_key, :environment}
  end

  test "bound scope overrides require the full explicit pair", %{opts: opts, repo: repo} do
    bind!(repo)
    assert {:ok, context} = Context.resolve(invocation(:project_required), opts)
    assert {context.host, context.project_id} == {"personal", 7}

    for {options, globals} <- [
          {%{project: 9}, %{}},
          {%{project: 7}, %{}},
          {%{}, %{host: "work"}},
          {%{}, %{host: "personal"}}
        ] do
      assert {:error, :invalid_invocation, _} =
               Context.resolve(invocation(:project_required, options, globals), opts)
    end

    failure =
      Keyword.put(opts, :git_runner, fn _, _, _ ->
        flunk("complete domain context must bypass Git")
      end)

    assert {:ok, context} =
             Context.resolve(
               invocation(:project_required, %{project: 9}, %{host: "work"}),
               failure
             )

    assert {context.host, context.project_id, context.common_git_dir} == {"work", 9, nil}

    assert {:error, :invalid_invocation, _} =
             Context.resolve(invocation(:project_required, %{}, %{host: "work"}), failure)

    assert :ok = RepositoryBinding.unbind(repo.common_dir)
    assert {:ok, context} = Context.resolve(invocation(:project_required, %{project: 9}), opts)
    assert {context.host, context.project_id} == {"work", 9}
    assert {:error, :invalid_invocation, _} = Context.resolve(invocation(:project_required), opts)
  end

  test "search global escape never carries a bound Project to another host", %{
    opts: opts,
    repo: repo
  } do
    bind!(repo)
    assert {:ok, context} = Context.resolve(invocation(:project_filter), opts)
    assert {context.host, context.project_id} == {"personal", 7}

    assert {:ok, context} =
             Context.resolve(invocation(:project_filter, %{all_projects: true}), opts)

    assert {context.host, context.project_id, context.sources.project} == {"personal", nil, :none}

    assert {:ok, context} =
             Context.resolve(
               invocation(:project_filter, %{all_projects: true}, %{host: "work"}),
               opts
             )

    assert {context.host, context.project_id} == {"work", nil}

    for {options, globals} <- [
          {%{project: 9}, %{}},
          {%{}, %{host: "work"}},
          {%{}, %{host: "personal"}}
        ] do
      assert {:error, :invalid_invocation, _} =
               Context.resolve(invocation(:project_filter, options, globals), opts)
    end

    assert {:error, :invalid_invocation, _} =
             Context.resolve(invocation(:project_filter, %{all_projects: true, project: 9}), opts)

    assert :ok = RepositoryBinding.unbind(repo.common_dir)
    assert {:ok, context} = Context.resolve(invocation(:project_filter), opts)
    assert {context.host, context.project_id} == {"work", nil}
    assert {:ok, context} = Context.resolve(invocation(:project_filter, %{project: 9}), opts)
    assert {context.host, context.project_id} == {"work", 9}

    assert {:ok, context} =
             Context.resolve(invocation(:project_filter, %{}, %{host: "personal"}), opts)

    assert {context.host, context.project_id} == {"personal", nil}
  end

  test "partial search requires discovery while complete explicit domain targets bypass it", %{
    opts: opts
  } do
    failure = Keyword.put(opts, :git_runner, fn _, _, _ -> {:error, :unavailable} end)

    for {options, globals} <- [
          {%{}, %{}},
          {%{project: 9}, %{}},
          {%{}, %{host: "work"}},
          {%{all_projects: true}, %{}}
        ] do
      assert {:error, :invalid_configuration, _} =
               Context.resolve(invocation(:project_filter, options, globals), failure)
    end

    for options <- [%{project: 9}, %{all_projects: true}] do
      assert {:ok, context} =
               Context.resolve(invocation(:project_filter, options, %{host: "work"}), failure)

      assert context.host == "work"
      assert context.common_git_dir == nil
    end

    assert {:ok, context} = Context.resolve(invocation(:host_only, %{}, %{host: "work"}), failure)
    assert context.project_id == nil
  end

  test "binding context preflights Git and stored state before credentials", %{
    opts: opts,
    repo: repo,
    tmp_dir: tmp_dir
  } do
    bind = invocation(:bind, %{project: 9}, %{host: "alias"})

    assert {:error, :invalid_invocation, _} =
             Context.resolve(bind, Keyword.put(opts, :cwd, tmp_dir))

    assert {:error, :invalid_invocation, _} =
             Context.resolve(invocation(:bind, %{project: 9}), opts)

    assert {:error, :invalid_invocation, _} =
             Context.resolve(invocation(:bind, %{}, %{host: "work"}), opts)

    managed = Path.join(repo.common_dir, "taskman")
    refute File.exists?(managed)

    assert {:ok, context} =
             Context.resolve(invocation(:bind, %{project: 9}, %{host: "work"}), opts)

    assert {context.host, context.project_id, context.common_git_dir} ==
             {"work", 9, repo.common_dir}

    refute File.exists?(managed)

    bind!(repo)

    assert {:ok, context} =
             Context.resolve(invocation(:bind, %{project: 9}, %{host: "work"}), opts)

    assert {context.host, context.project_id, context.common_git_dir} ==
             {"work", 9, repo.common_dir}

    assert {:ok, %{host: "personal", project_id: 7}} = RepositoryBinding.read(repo.common_dir)

    File.mkdir_p!(managed)
    File.write!(Path.join(managed, "binding.json"), "private malformed old state")
    assert {:error, :invalid_configuration, message} = Context.resolve(bind, opts)
    assert message =~ "binding"
    refute message =~ "private"

    assert {:ok, context} =
             Context.resolve(invocation(:project_required, %{project: 9}, %{host: "work"}), opts)

    assert context.project_id == 9
  end

  test "host-only roles select the repository host but never substitute its Project", %{
    opts: opts,
    repo: repo
  } do
    bind!(repo)
    assert {:ok, context} = Context.resolve(invocation(:host_only, %{project: 99}), opts)
    assert {context.host, context.project_id, context.sources.project} == {"personal", nil, :none}
  end

  test "effective inspection reports safe sources without requiring authentication", %{
    opts: opts,
    repo: repo,
    root: root
  } do
    bind!(repo)
    assert {:ok, context} = Context.resolve(invocation(:effective_inspection), opts)

    assert {context.host, context.project_id, context.config_format, context.default_host} ==
             {"personal", 7, :v2, "work"}

    assert context.sources == %{host: :repository, project: :repository, key: :stored}

    for host <- ["personal", "alias"] do
      assert {:ok, context} =
               Context.resolve(invocation(:effective_inspection, %{}, %{host: host}), opts)

      assert {context.host, context.project_id} == {host, nil}
      assert context.sources.host == :flag
      assert context.sources.project == :none
    end

    assert :ok = RepositoryBinding.unbind(repo.common_dir)
    Fixtures.write_hosts!(root, %{format: :v2, hosts: %{}, default_host: nil})

    assert {:ok, context} =
             Context.resolve(
               invocation(:effective_inspection),
               with_env(opts, %{
                 "TASKMAN_API_KEY" => @injected_key,
                 "TASKMAN_API_KEY_HOST" => "unselected"
               })
             )

    assert {context.host, context.api_url, context.project_id, context.api_key} ==
             {nil, nil, nil, nil}

    assert context.sources == %{host: :none, project: :none, key: :none}

    assert {:error, :invalid_configuration, _} =
             Context.resolve(
               invocation(:effective_inspection),
               with_env(opts, %{"TASKMAN_API_KEY_HOST" => "unselected"})
             )

    assert {:error, :invalid_invocation, _} = Context.resolve(invocation(:host_only), opts)

    bind!(repo)

    assert {:error, :invalid_configuration, _} =
             Context.resolve(invocation(:effective_inspection), opts)

    assert {:error, :invalid_configuration, _} =
             Context.resolve(invocation(:project_required), opts)
  end

  test "local roles bypass stores and transport variables while resolving roles reject retired URLs",
       %{opts: opts} do
    poison =
      opts
      |> with_env(%{
        "TASKMAN_API_URL" => "private retired url",
        "TASKMAN_API_KEY" => "private key"
      })
      |> Keyword.put(:git_runner, fn _, _, _ -> flunk("local commands must bypass Git") end)

    assert {:ok, context} = Context.resolve(invocation(:local), poison)
    assert context.host == nil
    assert context.sources == %{host: :none, project: :none, key: :none}

    for role <- [:project_required, :project_filter, :host_only, :bind, :effective_inspection] do
      assert {:error, :invalid_configuration, message} =
               Context.resolve(invocation(role, %{project: 9}, %{host: "work"}), poison)

      refute message =~ "private"
    end

    assert {:ok, _} =
             Context.resolve(
               invocation(:host_only, %{}, %{host: "work"}),
               with_env(opts, %{"TASKMAN_API_URL" => ""})
             )
  end

  test "explicit syntax and conflicting scope are refused before store discovery", %{opts: opts} do
    failure =
      Keyword.put(opts, :git_runner, fn _, _, _ ->
        flunk("invalid syntax must bypass discovery")
      end)

    for globals <- [%{host: ""}, %{host: "INVALID secret"}] do
      assert {:error, :invalid_invocation, message} =
               Context.resolve(invocation(:host_only, %{}, globals), failure)

      refute message =~ "secret"
    end

    for project <- [0, -1, "7", nil] do
      assert {:error, :invalid_invocation, _} =
               Context.resolve(
                 invocation(:project_required, %{project: project}, %{host: "work"}),
                 failure
               )
    end

    assert {:error, :invalid_invocation, _} =
             Context.resolve(
               invocation(:project_filter, %{all_projects: true, project: 9}, %{host: "work"}),
               failure
             )
  end

  test "complete selectors still validate the entire host document", %{opts: opts, root: root} do
    Fixtures.legacy_config!(root, %{"api_url" => "private invalid url"})

    assert {:error, :invalid_configuration, message} =
             Context.resolve(invocation(:project_required, %{project: 9}, %{host: "work"}), opts)

    refute message =~ "private"
  end

  test "effective inspection preflights repository state even with an explicit host", %{
    opts: opts,
    repo: repo
  } do
    managed = Path.join(repo.common_dir, "taskman")
    File.mkdir_p!(managed)
    File.write!(Path.join(managed, "binding.json"), "private malformed binding")

    assert {:error, :invalid_configuration, _} =
             Context.resolve(invocation(:effective_inspection, %{}, %{host: "work"}), opts)

    failure = Keyword.put(opts, :git_runner, fn _, _, _ -> {:error, :unavailable} end)

    assert {:error, :invalid_configuration, _} =
             Context.resolve(invocation(:effective_inspection, %{}, %{host: "work"}), failure)

    assert {:error, :invalid_configuration, _} =
             Context.resolve(invocation(:bind, %{project: 9}, %{host: "work"}), failure)
  end

  test "inspection preserves absent and legacy formats without writing configuration", %{
    opts: opts,
    root: root
  } do
    path = Path.join([root, "taskman", "config.json"])
    File.rm!(path)
    assert {:ok, context} = Context.resolve(invocation(:effective_inspection), Map.new(opts))

    assert {context.config_format, context.host, context.api_url, context.api_key} ==
             {:absent, nil, nil, nil}

    assert context.sources == %{host: :none, project: :none, key: :none}
    refute File.exists?(path)

    Fixtures.legacy_config!(root, %{"api_key" => @personal_key})
    original = File.read!(path)
    assert {:ok, context} = Context.resolve(invocation(:host_only), opts)

    assert {context.config_format, context.host, context.api_url, context.api_key,
            context.default_host} ==
             {:legacy, "default", "http://localhost:4000", @personal_key, "default"}

    assert context.sources == %{host: :default, project: :none, key: :stored}
    assert File.read!(path) == original

    Fixtures.legacy_config!(root, %{"api_url" => "https://legacy.example.invalid"})
    assert {:ok, context} = Context.resolve(invocation(:effective_inspection), opts)
    assert {context.api_key, context.sources.key} == {nil, :none}
  end

  defp invocation(role, options \\ %{}, globals \\ %{}) do
    command = struct!(Command, context_role: role)
    %Invocation{command: command, options: options, globals: globals}
  end

  defp bind!(repo) do
    assert :ok =
             RepositoryBinding.put(repo.common_dir, %{version: 1, host: "personal", project_id: 7})
  end

  defp with_env(opts, env), do: Keyword.put(opts, :env, env)
end
