defmodule Taskman.CLI.RepositoryBindingTest do
  # The staging collision case reserves the next VM-wide unique integer.
  use ExUnit.Case, async: false

  import Bitwise
  alias Taskman.CLI.{Fixtures, RepositoryBinding}

  @old %{version: 1, host: "personal", project_id: 7}
  @new %{version: 1, host: "work", project_id: 9}

  setup do
    root =
      Path.join(System.tmp_dir!(), "binding-#{System.unique_integer([:positive, :monotonic])}")

    File.mkdir_p!(root)
    on_exit(fn -> File.rm_rf!(root) end)
    %{root: root, repo: Fixtures.git_repo!(root, "clone with spaces")}
  end

  test "linked worktrees share binding while independent clones do not", %{root: root, repo: repo} do
    linked = Fixtures.linked_worktree!(repo, Path.join(root, "linked worktree"))
    Fixtures.git!(root, ["clone", "--quiet", repo.cwd, Path.join(root, "independent")])
    independent = Path.join(root, "independent/.git")
    subdir = Path.join(repo.cwd, "sub/directory")
    File.mkdir_p!(subdir)
    Fixtures.git!(repo.cwd, ["checkout", "--quiet", "--detach"])

    for cwd <- [repo.cwd, subdir, linked.cwd] do
      assert {:ok, common_dir} = RepositoryBinding.discover(cwd: cwd, env: %{})
      assert common_dir == repo.common_dir
    end

    assert :ok = RepositoryBinding.put(repo.common_dir, @old)
    assert {:ok, @old} = RepositoryBinding.read(linked.common_dir)

    assert {:ok, ^independent} =
             RepositoryBinding.discover(cwd: Path.dirname(independent), env: %{})

    assert {:ok, :unbound} = RepositoryBinding.read(independent)
  end

  test "Git relocation and nearer repositories choose their own common directory", %{
    root: root,
    repo: repo
  } do
    nested = Fixtures.git_repo!(repo.cwd, "nested")
    assert {:ok, nested_dir} = RepositoryBinding.discover(cwd: nested.cwd, env: %{})
    assert nested_dir == nested.common_dir
    assert {:ok, :unbound} = RepositoryBinding.read(nested_dir)

    source = Fixtures.git_repo!(root, "submodule source")

    Fixtures.git!(repo.cwd, [
      "-c",
      "protocol.file.allow=always",
      "submodule",
      "add",
      "--quiet",
      source.cwd,
      "module"
    ])

    assert {:ok, module_dir} =
             RepositoryBinding.discover(cwd: Path.join(repo.cwd, "module"), env: %{})

    assert module_dir == Path.join(repo.common_dir, "modules/module")

    relocation = %{
      "GIT_DIR" => source.common_dir,
      "GIT_WORK_TREE" => repo.cwd,
      "GIT_COMMON_DIR" => source.common_dir
    }

    assert {:ok, relocated} = RepositoryBinding.discover(cwd: repo.cwd, env: relocation)
    assert relocated == source.common_dir

    separate = Path.join(root, "separate metadata")
    cwd = Path.join(root, "relocated checkout")
    Fixtures.git!(root, ["init", "--quiet", "--separate-git-dir", separate, cwd])
    assert {:ok, ^separate} = RepositoryBinding.discover(cwd: cwd, env: %{})
    Fixtures.git!(root, ["init", "--quiet", "--bare", Path.join(root, "bare")])

    assert {:ok, :outside_work_tree} =
             RepositoryBinding.discover(cwd: Path.join(root, "bare"), env: %{})

    assert {:ok, :outside_work_tree} = RepositoryBinding.discover(cwd: root, env: %{})
    broken = Path.join(root, "broken")
    File.mkdir!(broken)
    File.write!(Path.join(broken, ".git"), "gitdir: /missing/disposable-git-dir\n")
    assert {:error, :invalid_configuration, _} = RepositoryBinding.discover(cwd: broken, env: %{})
  end

  test "discovery uses exact argv and refuses uncertain failures without displaying diagnostics",
       %{repo: repo} do
    owner = self()

    runner = fn argv, cwd, env ->
      send(owner, {argv, cwd, env})

      case argv do
        ["rev-parse", "--is-inside-work-tree"] ->
          {"true\n", 0}

        ["rev-parse", "--path-format=absolute", "--git-common-dir"] ->
          {repo.common_dir <> "\n", 0}
      end
    end

    assert {:ok, common} =
             RepositoryBinding.discover(
               cwd: repo.cwd,
               env: %{"GIT_DIR" => "selected"},
               git_runner: runner
             )

    assert common == repo.common_dir

    assert_received {["rev-parse", "--is-inside-work-tree"], _,
                     %{"GIT_DIR" => "selected", "LC_ALL" => "C"}}

    assert_received {["rev-parse", "--path-format=absolute", "--git-common-dir"], _, _}

    for result <- [{"fatal: private diagnostic\n", 128}, {"unexpected", 0}, {:error, :enoent}] do
      assert {:error, :invalid_configuration, message} =
               RepositoryBinding.discover(
                 cwd: repo.cwd,
                 env: %{},
                 git_runner: fn _, _, _ -> result end
               )

      refute message =~ "private diagnostic"
    end

    assert {:ok, :outside_work_tree} =
             RepositoryBinding.discover(
               git_runner: fn _, _, _ ->
                 {"fatal: not a git repository (or any of the parent directories): .git\n", 128}
               end
             )

    for path <- ["relative\n", "\n", "/one\n/two\n", "/missing/disposable-common-dir\n"] do
      runner = fn
        ["rev-parse", "--is-inside-work-tree"], _, _ -> {"true\n", 0}
        _, _, _ -> {path, 0}
      end

      assert {:error, :invalid_configuration, _} = RepositoryBinding.discover(git_runner: runner)
    end
  end

  test "strict bindings reject malformed prior state and invalid replacements", %{repo: repo} do
    assert {:ok, :unbound} = RepositoryBinding.read(repo.common_dir)
    assert :ok = RepositoryBinding.put(repo.common_dir, @old)
    path = binding_path(repo)
    bytes = File.read!(path)

    for invalid <- [
          %{@old | version: 2},
          %{@old | host: "Personal"},
          %{@old | project_id: 0},
          %{@old | project_id: 7.0},
          Map.put(@old, :api_key, "synthetic"),
          Map.delete(@old, :host)
        ] do
      assert {:error, :invalid_configuration, _} = RepositoryBinding.put(repo.common_dir, invalid)
      assert File.read!(path) == bytes
    end

    for malformed <- [
          "{",
          "[]",
          "null",
          ~s({"version":1,"host":"personal","project_id":null}),
          ~s({"version":1.0,"host":"personal","project_id":7}),
          ~s({"version":1,"host":"personal","project_id":7,"extra":true})
        ] do
      File.write!(path, malformed)
      assert {:error, :invalid_configuration, _} = RepositoryBinding.read(repo.common_dir)
      assert {:error, :invalid_configuration, _} = RepositoryBinding.put(repo.common_dir, @new)
      assert File.read!(path) == malformed
    end
  end

  test "failed lock acquisition leaves the existing lock and readable binding", %{repo: repo} do
    assert :ok = RepositoryBinding.put(repo.common_dir, @old)
    lock = binding_path(repo) <> ".lock"
    File.mkdir!(lock)
    owner = self()
    wait = fn ms -> send(owner, {:waited, ms}) end

    assert {:error, :invalid_configuration, message} =
             RepositoryBinding.put(repo.common_dir, @new, wait: wait)

    assert message =~ "lock"
    assert File.dir?(lock)
    assert {:ok, @old} = RepositoryBinding.read(repo.common_dir)
    for _ <- 1..100, do: assert_received({:waited, 100})
    refute_received {:waited, _}

    assert {:error, :invalid_configuration, _} =
             RepositoryBinding.unbind(repo.common_dir, wait: wait)

    assert File.dir?(lock)
    assert {:ok, @old} = RepositoryBinding.read(repo.common_dir)
  end

  test "replacement errors describe the persisted commit boundary", %{repo: repo} do
    assert :ok = RepositoryBinding.put(repo.common_dir, @old)
    path = binding_path(repo)
    old_bytes = File.read!(path)
    owner = self()

    rename = fn stage, destination ->
      send(owner, {:staged, Jason.decode!(File.read!(stage)), File.stat!(stage).mode &&& 0o777})
      assert destination == path
      assert File.read!(path) == old_bytes
      {:error, :eacces}
    end

    assert {:error, :invalid_configuration, before_message} =
             RepositoryBinding.put(repo.common_dir, @new, rename: rename)

    refute before_message =~ "already"
    assert File.read!(path) == old_bytes
    assert_received {:staged, %{"version" => 1, "host" => "work", "project_id" => 9}, 0o600}
    assert File.ls!(Path.dirname(path)) == ["binding.json"]

    assert {:error, :invalid_configuration, after_message} =
             RepositoryBinding.put(repo.common_dir, @new,
               sync_directory: fn _ -> {:error, :eio} end
             )

    assert after_message =~ "may already have changed"
    assert after_message =~ "repo show"
    assert {:ok, @new} = RepositoryBinding.read(repo.common_dir)
    assert (File.stat!(path).mode &&& 0o777) == 0o600
    assert (File.stat!(Path.dirname(path)).mode &&& 0o777) == 0o700
  end

  test "concurrent writers serialize and re-read state after acquiring the lock", %{repo: repo} do
    assert :ok = RepositoryBinding.put(repo.common_dir, @old)
    owner = self()

    rename = fn stage, path ->
      send(owner, {:first_ready, self()})

      receive do
        :replace -> File.rename(stage, path)
      end
    end

    supervisor = start_supervised!(Task.Supervisor)

    first =
      Task.Supervisor.async_nolink(supervisor, fn ->
        RepositoryBinding.put(repo.common_dir, @new, rename: rename)
      end)

    assert_receive {:first_ready, first_pid}

    wait = fn 100 ->
      send(owner, {:second_waiting, self()})

      receive do
        :retry -> :ok
      end
    end

    second_binding = %{version: 1, host: "last", project_id: 11}

    second =
      Task.Supervisor.async_nolink(supervisor, fn ->
        RepositoryBinding.put(repo.common_dir, second_binding, wait: wait)
      end)

    assert_receive {:second_waiting, second_pid}
    assert {:ok, @old} = RepositoryBinding.read(repo.common_dir)
    send(first_pid, :replace)
    assert :ok = Task.await(first)
    assert {:ok, @new} = RepositoryBinding.read(repo.common_dir)
    send(second_pid, :retry)
    assert :ok = Task.await(second)
    assert {:ok, ^second_binding} = RepositoryBinding.read(repo.common_dir)

    lock = binding_path(repo) <> ".lock"
    File.mkdir!(lock)

    repair_wait = fn 100 ->
      File.write!(binding_path(repo), "malformed while waiting")
      File.rmdir!(lock)
    end

    assert {:error, :invalid_configuration, _} =
             RepositoryBinding.put(repo.common_dir, @old, wait: repair_wait)

    assert File.read!(binding_path(repo)) == "malformed while waiting"
    refute File.exists?(lock)
  end

  test "unbind repairs malformed JSON and does not create an absent directory", %{repo: repo} do
    directory = Path.join(repo.common_dir, "taskman")
    assert :ok = RepositoryBinding.unbind(repo.common_dir)
    refute File.exists?(directory)
    assert :ok = RepositoryBinding.put(repo.common_dir, @old)
    File.write!(binding_path(repo), "malformed")
    assert {:error, :invalid_configuration, _} = RepositoryBinding.read(repo.common_dir)
    assert :ok = RepositoryBinding.unbind(repo.common_dir)
    assert {:ok, :unbound} = RepositoryBinding.read(repo.common_dir)
    assert :ok = RepositoryBinding.unbind(repo.common_dir)
    assert File.dir?(directory)
    assert :ok = RepositoryBinding.put(repo.common_dir, @old)

    assert {:error, :invalid_configuration, message} =
             RepositoryBinding.unbind(repo.common_dir,
               sync_directory: fn _ -> {:error, :eio} end
             )

    assert message =~ "may already have changed"
    assert message =~ "repo show"
    assert {:ok, :unbound} = RepositoryBinding.read(repo.common_dir)
  end

  test "existing staging artifacts are retained when a new write chooses a unique sibling", %{
    repo: repo
  } do
    assert :ok = RepositoryBinding.put(repo.common_dir, @old)
    suffix = System.unique_integer([:positive, :monotonic]) + 1
    orphan = Path.join(Path.dirname(binding_path(repo)), ".binding.json.stage-#{suffix}")
    File.write!(orphan, "interrupted writer artifact")
    assert :ok = RepositoryBinding.put(repo.common_dir, @new)
    assert File.read!(orphan) == "interrupted writer artifact"
    assert {:ok, @new} = RepositoryBinding.read(repo.common_dir)
  end

  test "ordinary IO errors refuse without changing Git directory permissions", %{
    root: root,
    repo: repo
  } do
    git_mode = File.stat!(repo.common_dir).mode
    assert :ok = RepositoryBinding.put(repo.common_dir, @old)
    assert File.stat!(repo.common_dir).mode == git_mode
    File.rm!(binding_path(repo))
    File.mkdir!(binding_path(repo))
    assert {:error, :invalid_configuration, _} = RepositoryBinding.read(repo.common_dir)
    assert {:error, :invalid_configuration, _} = RepositoryBinding.put(repo.common_dir, @new)
    assert {:error, :invalid_configuration, _} = RepositoryBinding.unbind(repo.common_dir)
    blocked = Path.join(root, "blocked")
    File.mkdir!(blocked)
    File.write!(Path.join(blocked, "taskman"), "ordinary file")
    assert {:error, :invalid_configuration, _} = RepositoryBinding.put(blocked, @new)
    assert {:error, :invalid_configuration, _} = RepositoryBinding.unbind(blocked)
  end

  defp binding_path(repo), do: Path.join([repo.common_dir, "taskman", "binding.json"])
end
