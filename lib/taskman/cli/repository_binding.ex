defmodule Taskman.CLI.RepositoryBinding do
  @moduledoc "Git discovery and cooperative storage of a clone's shared host/Project binding."

  alias Taskman.CLI.Config.Document

  @type binding :: %{version: 1, host: String.t(), project_id: pos_integer()}
  @type local_error :: {:error, :invalid_configuration, String.t()}

  @doc "Discover the working tree's shared Git directory without creating files."
  @spec discover(keyword() | map()) :: {:ok, binary() | :outside_work_tree} | local_error()
  def discover(options \\ []) do
    cwd = option(options, :cwd, File.cwd!())
    env = options |> option(:env, System.get_env()) |> Map.new() |> Map.put("LC_ALL", "C")
    runner = option(options, :git_runner, &run_git/3)

    case runner.(["rev-parse", "--is-inside-work-tree"], cwd, env) do
      {output, 0} when is_binary(output) ->
        case strip_newline(output) do
          "true" -> discover_common_dir(runner, cwd, env)
          "false" -> {:ok, :outside_work_tree}
          _other -> discovery_error()
        end

      {output, 128} when is_binary(output) ->
        diagnostic = output |> String.split("\n") |> Enum.find(&String.starts_with?(&1, "fatal:"))

        if is_binary(diagnostic) and
             String.starts_with?(diagnostic, "fatal: not a git repository (or any "),
           do: {:ok, :outside_work_tree},
           else: discovery_error()

      _other ->
        discovery_error()
    end
  end

  @doc "Read a complete binding, or report an absent binding without writing."
  @spec read(binary(), keyword() | map()) :: {:ok, binding() | :unbound} | local_error()
  def read(common_dir, _options \\ []) do
    case File.read(binding_path(common_dir)) do
      {:ok, contents} -> decode(contents)
      {:error, :enoent} -> {:ok, :unbound}
      {:error, _reason} -> error("Could not read the Taskman repository binding")
    end
  end

  @doc "Validate and replace a binding after re-reading its current state under the lock."
  @spec put(binary(), binding(), keyword() | map()) :: :ok | local_error()
  def put(common_dir, binding, options \\ []) do
    path = binding_path(common_dir)

    with :ok <- validate(binding),
         :ok <- ensure_directory(Path.dirname(path)) do
      with_lock(path, options, fn ->
        with {:ok, _current} <- read(common_dir) do
          replace(path, binding, options)
        end
      end)
    end
  end

  @doc "Remove a binding offline, including malformed JSON; retain an existing store directory."
  @spec unbind(binary(), keyword() | map()) :: :ok | local_error()
  def unbind(common_dir, options \\ []) do
    path = binding_path(common_dir)
    directory = Path.dirname(path)

    case File.stat(directory) do
      {:error, :enoent} ->
        :ok

      {:ok, %{type: :directory}} ->
        with_lock(path, options, fn ->
          case File.rm(path) do
            result when result in [:ok, {:error, :enoent}] ->
              sync_after_change(directory, options)

            {:error, _reason} ->
              error("Could not remove the Taskman repository binding")
          end
        end)

      _other ->
        error("Could not access the Taskman repository binding directory")
    end
  end

  defp discover_common_dir(runner, cwd, env) do
    case runner.(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd, env) do
      {output, 0} when is_binary(output) ->
        path = strip_newline(output)

        with true <-
               path != "" and Path.type(path) == :absolute and
                 not String.contains?(path, ["\n", "\r"]),
             {:ok, _entries} <- File.ls(path) do
          {:ok, path}
        else
          _other -> discovery_error()
        end

      _other ->
        discovery_error()
    end
  end

  defp run_git(argv, cwd, env) do
    # System.cmd inherits unspecified variables. Explicitly unset ambient Git state so
    # an injected environment remains authoritative for discovery in isolated invocations.
    unset = for {key, _} <- System.get_env(), String.starts_with?(key, "GIT_"), do: {key, nil}
    System.cmd("git", argv, cd: cwd, env: unset ++ Map.to_list(env), stderr_to_stdout: true)
  rescue
    _error -> {:error, :git_unavailable}
  end

  defp decode(contents) do
    with {:ok, %{"version" => version, "host" => host, "project_id" => project_id} = json} <-
           Jason.decode(contents),
         true <- map_size(json) == 3,
         binding = %{version: version, host: host, project_id: project_id},
         :ok <- validate(binding) do
      {:ok, binding}
    else
      _other -> invalid_binding()
    end
  end

  defp validate(%{version: 1, host: host, project_id: project_id} = binding)
       when map_size(binding) == 3 and is_integer(project_id) and project_id > 0 do
    case Document.validate_host_name(host) do
      :ok -> :ok
      _other -> invalid_binding()
    end
  end

  defp validate(_binding), do: invalid_binding()

  defp ensure_directory(directory) do
    case File.mkdir(directory) do
      :ok ->
        case File.chmod(directory, 0o700) do
          :ok -> :ok
          {:error, _reason} -> error("Could not set Taskman repository directory permissions")
        end

      {:error, :eexist} ->
        case File.stat(directory) do
          {:ok, %{type: :directory}} -> :ok
          _other -> error("Could not access the Taskman repository binding directory")
        end

      {:error, _reason} ->
        error("Could not create the Taskman repository binding directory")
    end
  end

  defp with_lock(path, options, operation) do
    lock = path <> ".lock"

    case acquire_lock(lock, options, 100) do
      :ok ->
        try do
          operation.()
        after
          # Only successful mkdir acquisition reaches this release. Never steal or
          # recursively remove a lock; interrupted writers require manual cleanup.
          _ = File.rmdir(lock)
        end

      error ->
        error
    end
  end

  defp acquire_lock(lock, options, retries) do
    case File.mkdir(lock) do
      :ok ->
        :ok

      {:error, :eexist} when retries > 0 ->
        wait = option(options, :wait, &Process.sleep/1)
        wait.(100)
        acquire_lock(lock, options, retries - 1)

      {:error, :eexist} ->
        error(
          "The Taskman repository binding lock is busy; after verifying no writer is active, remove the stale lock directory"
        )

      {:error, _reason} ->
        error("Could not acquire the Taskman repository binding lock")
    end
  end

  defp replace(path, binding, options) do
    stage =
      Path.join(
        Path.dirname(path),
        ".binding.json.stage-#{System.unique_integer([:positive, :monotonic])}"
      )

    contents = Jason.encode!(binding) <> "\n"
    rename = option(options, :rename, &File.rename/2)

    staged =
      File.open(stage, [:write, :exclusive, :binary], fn device ->
        with :ok <- File.chmod(stage, 0o600),
             :ok <- IO.binwrite(device, contents),
             :ok <- :file.sync(device),
             do: :ok
      end)

    case staged do
      {:ok, result} ->
        try do
          with :ok <- result, :ok <- rename.(stage, path) do
            sync_after_change(Path.dirname(path), options)
          else
            _other -> error("Could not atomically replace the Taskman repository binding")
          end
        after
          _ = File.rm(stage)
        end

      {:error, :eexist} ->
        replace(path, binding, options)

      {:error, _reason} ->
        error("Could not stage the Taskman repository binding")
    end
  end

  defp sync_after_change(directory, options) do
    sync = option(options, :sync_directory, &sync_directory/1)

    case sync.(directory) do
      :ok -> :ok
      _other -> changed_error()
    end
  rescue
    _error -> changed_error()
  end

  defp sync_directory(directory) do
    with {:ok, device} <- :file.open(String.to_charlist(directory), [:read, :raw, :directory]) do
      try do
        :file.sync(device)
      after
        :file.close(device)
      end
    end
  end

  defp strip_newline(output), do: String.replace_suffix(output, "\n", "")
  defp binding_path(common_dir), do: Path.join([common_dir, "taskman", "binding.json"])
  defp option(options, key, default) when is_list(options), do: Keyword.get(options, key, default)
  defp option(options, key, default) when is_map(options), do: Map.get(options, key, default)

  defp discovery_error,
    do:
      error(
        "Could not discover the Git working tree; repair Git state or supply explicit context"
      )

  defp invalid_binding,
    do: error("The Taskman repository binding is invalid; use repo unbind to repair it")

  defp changed_error,
    do:
      error(
        "The Taskman repository binding may already have changed because directory sync failed; inspect with repo show"
      )

  defp error(message), do: {:error, :invalid_configuration, message}
end
