defmodule Taskman.CLI.Config do
  @moduledoc "Secure local configuration for the Taskman CLI."

  import Bitwise

  alias Taskman.CLI.Config.Document

  @config_directory "taskman"
  @config_filename "config.json"
  @lock_marker "taskman_config_lock_v1"
  @lock_retry_limit 100
  @lock_retry_delay_ms 100

  @doc "Return the configuration file path, using an injected root or environment when supplied."
  @spec path(keyword() | map()) :: Path.t()
  def path(options \\ []) do
    options = normalize_options(options)
    root = option(options, :config_root) || config_root(option(options, :env, System.get_env()))
    Path.join([root, @config_directory, @config_filename])
  end

  @type local_error :: {:error, :invalid_configuration | :invalid_invocation, String.t()}

  @doc "Read protected host configuration without creating or upgrading any files."
  @spec load(keyword() | map()) :: {:ok, Document.t()} | local_error()
  def load(options \\ []) do
    options = normalize_options(options)

    read(path(options), options)
  end

  @doc "Register a host without a credential or an implicit default selection."
  @spec add_host(String.t(), String.t(), keyword() | map()) :: :ok | local_error()
  def add_host(name, api_url, options \\ []) do
    with :ok <- Document.validate_host_name(name),
         :ok <- Document.validate_url(api_url) do
      mutate_hosts(options, fn document ->
        if Map.has_key?(document.hosts, name) do
          invocation_error("The named Taskman host is already registered")
        else
          {:ok,
           %{document | hosts: Map.put(document.hosts, name, %{api_url: api_url, api_key: nil})}}
        end
      end)
    end
  end

  @doc "Change the address of the same host, retaining its key and default selection."
  @spec set_host_url(String.t(), String.t(), keyword() | map()) :: :ok | local_error()
  def set_host_url(name, api_url, options \\ []) do
    with :ok <- Document.validate_host_name(name),
         :ok <- Document.validate_url(api_url) do
      mutate_hosts(options, &update_host(&1, name, fn host -> %{host | api_url: api_url} end))
    end
  end

  @doc "Replace a registered host's credential without displaying it."
  @spec set_host_key(String.t(), String.t(), keyword() | map()) :: :ok | local_error()
  def set_host_key(name, api_key, options \\ []) do
    with :ok <- Document.validate_host_name(name),
         :ok <- Document.validate_api_key(api_key) do
      mutate_hosts(options, &update_host(&1, name, fn host -> %{host | api_key: api_key} end))
    end
  end

  @doc "Remove a host and clear its default selection without choosing a replacement."
  @spec remove_host(String.t(), keyword() | map()) :: :ok | local_error()
  def remove_host(name, options \\ []) do
    with :ok <- Document.validate_host_name(name) do
      mutate_hosts(options, fn document ->
        with {:ok, _host} <- fetch_host(document, name) do
          default_host = if document.default_host == name, do: nil, else: document.default_host
          {:ok, %{document | hosts: Map.delete(document.hosts, name), default_host: default_host}}
        end
      end)
    end
  end

  @doc "Select an existing host as the configured default."
  @spec set_default_host(String.t(), keyword() | map()) :: :ok | local_error()
  def set_default_host(name, options \\ []) do
    with :ok <- Document.validate_host_name(name) do
      mutate_hosts(options, fn document ->
        with {:ok, _host} <- fetch_host(document, name) do
          {:ok, %{document | default_host: name}}
        end
      end)
    end
  end

  defp mutate_hosts(options, mutation) do
    options = normalize_options(options)
    config_path = path(options)

    with {:ok, document} <- load(options),
         {:ok, _validated} <- mutation.(document),
         :ok <- ensure_directory(Path.dirname(config_path)) do
      with_config_lock(config_path, options, fn ->
        with {:ok, current} <- load(options),
             {:ok, updated} <- mutation.(current),
             :ok <- before_write(config_path, options) do
          write(Document.encode(updated), config_path, options)
        else
          {:error, code, _message} = result
          when code in [:invalid_configuration, :invalid_invocation] ->
            result

          _other ->
            error("The Taskman configuration file could not be prepared for writing")
        end
      end)
    end
  end

  defp update_host(document, name, update) do
    with {:ok, host} <- fetch_host(document, name) do
      {:ok, %{document | hosts: Map.put(document.hosts, name, update.(host))}}
    end
  end

  defp fetch_host(document, name) do
    case Map.fetch(document.hosts, name) do
      {:ok, host} -> {:ok, host}
      :error -> invocation_error("The named Taskman host is not registered")
    end
  end

  defp invocation_error(message), do: {:error, :invalid_invocation, message}

  defp read(config_path, options) do
    case lstat(config_path) do
      {:error, :enoent} ->
        {:ok, %{format: :absent, hosts: %{}, default_host: nil}}

      {:ok, lstat} ->
        with :ok <- validate_directory(Path.dirname(config_path)),
             :ok <- validate_config_file(lstat),
             :ok <- before_open(config_path, options),
             {:ok, device} <- open_readonly(config_path) do
          try do
            with {:ok, contents} <- read_opened_file(device, options),
                 {:ok, opened_stat} <- file_info(device),
                 :ok <- validate_opened_file(lstat, opened_stat),
                 {:ok, decoded} <- Document.decode(contents) do
              {:ok, decoded}
            else
              {:error, :invalid_configuration, _message} = result -> result
              _other -> error("The Taskman configuration file could not be read")
            end
          after
            close_file(device)
          end
        end

      {:error, _reason} ->
        error("The Taskman configuration file could not be read")
    end
  end

  defp lstat(path), do: File.lstat(path)

  defp before_open(config_path, options) do
    case option(options, :before_open) do
      hook when is_function(hook, 1) ->
        hook.(config_path)
        :ok

      _other ->
        :ok
    end
  rescue
    _error -> {:error, :before_open_failed}
  end

  defp open_readonly(config_path),
    do: :file.open(String.to_charlist(config_path), [:read, :binary, :raw])

  defp read_opened_file(device, options) do
    case option(options, :read_file) do
      read_file when is_function(read_file, 1) -> read_file.(device)
      _other -> {:ok, IO.binread(device, :eof)}
    end
  rescue
    _error -> {:error, :read_failed}
  end

  defp file_info(device) do
    with {:ok, info} <- :file.read_file_info(device) do
      {:ok, File.Stat.from_record(info)}
    end
  end

  defp close_file(nil), do: :ok
  defp close_file(device), do: :file.close(device)

  defp write(config, config_path, options) do
    with :ok <- ensure_directory(Path.dirname(config_path)),
         :ok <- stage_and_replace(config, config_path, options) do
      :ok
    end
  end

  defp ensure_directory(directory) do
    root = Path.dirname(directory)

    with :ok <- mkdir_p(root),
         :ok <- ensure_config_directory(directory),
         :ok <- validate_directory(directory) do
      :ok
    else
      {:error, :invalid_configuration, _message} = result -> result
      {:error, _reason} -> error("The Taskman configuration directory could not be created")
    end
  end

  defp mkdir_p(directory) do
    case File.mkdir_p(directory) do
      :ok -> :ok
      {:error, reason} -> {:error, reason}
    end
  end

  defp ensure_config_directory(directory) do
    case File.mkdir(directory) do
      :ok -> File.chmod(directory, 0o700)
      {:error, :eexist} -> :ok
      {:error, reason} -> {:error, reason}
    end
  end

  defp validate_directory(directory) do
    case File.lstat(directory) do
      {:ok, %File.Stat{type: :directory, mode: mode}} when (mode &&& 0o7777) == 0o700 ->
        :ok

      {:ok, %File.Stat{type: :directory}} ->
        error("Unsafe Taskman configuration directory permissions")

      {:ok, _stat} ->
        error("The Taskman configuration directory is not a directory")

      {:error, _reason} ->
        error("The Taskman configuration directory could not be read")
    end
  end

  defp validate_config_file(%File.Stat{type: :regular, links: 1, mode: mode}) do
    validate_file_mode(mode)
  end

  defp validate_config_file(%File.Stat{type: :regular, links: links}) when links > 1 do
    error("The Taskman configuration file must not have hard links")
  end

  defp validate_config_file(%File.Stat{}) do
    error("The Taskman configuration path must be a regular file")
  end

  defp validate_opened_file(expected, opened) do
    with :ok <- validate_config_file(opened),
         true <- same_file?(expected, opened) do
      :ok
    else
      false -> error("The Taskman configuration file changed while it was being read")
      {:error, :invalid_configuration, _message} = result -> result
    end
  end

  defp same_file?(expected, opened) do
    expected.inode == opened.inode and expected.major_device == opened.major_device and
      expected.minor_device == opened.minor_device and expected.size == opened.size
  end

  defp validate_file_mode(mode) when (mode &&& 0o7777) == 0o600, do: :ok

  defp validate_file_mode(_mode) do
    error("Unsafe Taskman configuration file permissions; run chmod 600 on config.json")
  end

  defp stage_and_replace(config, config_path, options) do
    stage = unique_stage(config_path)
    contents = Jason.encode!(config) <> "\n"

    result =
      with {:ok, :ok} <-
             File.open(stage, [:write, :exclusive, :binary], fn device ->
               with :ok <- File.chmod(stage, 0o600),
                    :ok <- IO.binwrite(device, contents),
                    :ok <- :file.sync(device) do
                 :ok
               end
             end),
           :ok <- rename(stage, config_path, options) do
        case sync_directory(Path.dirname(config_path), options) do
          :ok ->
            :ok

          _other ->
            error(
              "The Taskman configuration may already have changed because directory sync failed; inspect with config hosts list"
            )
        end
      else
        {:error, _reason} -> error("Could not atomically replace the Taskman configuration file")
      end

    case result do
      :ok ->
        :ok

      {:error, :invalid_configuration, _message} = result ->
        _ = File.rm(stage)
        result
    end
  end

  defp sync_directory(directory, options) do
    case option(options, :sync_directory) do
      sync_directory when is_function(sync_directory, 1) -> sync_directory.(directory)
      _other -> sync_directory(directory)
    end
  rescue
    _error -> {:error, :sync_failed}
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

  defp with_config_lock(config_path, options, fun) when is_function(fun, 0) do
    lock_path = config_path <> ".lock"

    case acquire_lock(lock_path, options, 0) do
      {:ok, token} ->
        try do
          fun.()
        after
          _ = before_release(lock_path, options)
          _ = release_lock(lock_path, token, options)
        end

      {:error, message} ->
        error(message)
    end
  end

  defp acquire_lock(lock_path, options, attempt) do
    case File.mkdir(lock_path) do
      :ok ->
        token = lock_token()

        with :ok <- File.chmod(lock_path, 0o700),
             :ok <- File.write(owner_path(lock_path, token), owner_marker(token), [:exclusive]) do
          {:ok, token}
        else
          {:error, _reason} ->
            _ = File.rmdir(lock_path)
            {:error, "The Taskman configuration file could not be locked"}
        end

      {:error, :eexist} ->
        with :ok <- wait_for_lock(options, attempt) do
          acquire_lock(lock_path, options, attempt + 1)
        end

      {:error, _reason} ->
        {:error, "The Taskman configuration file could not be locked"}
    end
  end

  defp wait_for_lock(options, attempt) do
    if attempt < option(options, :lock_retry_limit, @lock_retry_limit) do
      receive do
      after
        option(options, :lock_retry_delay_ms, @lock_retry_delay_ms) -> :ok
      end
    else
      {:error, "Taskman configuration is busy; try again"}
    end
  end

  defp lock_token do
    :crypto.strong_rand_bytes(24)
    |> Base.url_encode64(padding: false)
  end

  defp owner_marker(token), do: @lock_marker <> ":" <> token

  defp owner_path(lock_path, token), do: Path.join(lock_path, "owner-" <> token)

  defp validate_owned_lock(lock_path, token) do
    owner_path = owner_path(lock_path, token)

    with {:ok, %File.Stat{type: :directory} = lock_stat} <- File.lstat(lock_path),
         {:ok, %File.Stat{type: :regular}} <- File.lstat(owner_path),
         {:ok, marker} <- File.read(owner_path),
         true <- marker == owner_marker(token),
         {:ok, entries} <- File.ls(lock_path),
         true <- entries == [Path.basename(owner_path)] do
      {:ok, lock_stat}
    else
      _other -> {:error, :unowned_lock}
    end
  end

  defp release_lock(lock_path, token, options) do
    with {:ok, lock_stat} <- validate_owned_lock(lock_path, token),
         :ok <- after_lock_validation(lock_path, options),
         :ok <- validate_lock_identity(lock_path, lock_stat),
         :ok <- File.rm(owner_path(lock_path, token)),
         :ok <- validate_lock_identity(lock_path, lock_stat),
         :ok <- File.rmdir(lock_path) do
      :ok
    else
      _other -> :ok
    end
  end

  defp validate_lock_identity(lock_path, expected) do
    with {:ok, %File.Stat{type: :directory} = current} <- File.lstat(lock_path),
         true <- same_location?(expected, current) do
      :ok
    else
      _other -> {:error, :unowned_lock}
    end
  end

  defp same_location?(left, right) do
    left.inode == right.inode and left.major_device == right.major_device and
      left.minor_device == right.minor_device
  end

  defp after_lock_validation(lock_path, options) do
    case option(options, :after_lock_validation) do
      hook when is_function(hook, 1) -> hook.(lock_path)
      _other -> :ok
    end
  rescue
    _error -> :ok
  end

  defp before_release(lock_path, options) do
    case option(options, :before_release) do
      hook when is_function(hook, 1) -> hook.(lock_path)
      _other -> :ok
    end
  rescue
    _error -> :ok
  end

  defp before_write(config_path, options) do
    case option(options, :before_write) do
      hook when is_function(hook, 1) -> hook.(config_path)
      _other -> :ok
    end
  rescue
    _error -> {:error, :before_write_failed}
  end

  defp rename(from, to, options) do
    case option(options, :rename) do
      rename when is_function(rename, 2) -> rename.(from, to)
      _other -> File.rename(from, to)
    end
  rescue
    _error -> {:error, :rename_failed}
  end

  defp unique_stage(config_path) do
    stage =
      Path.join(
        Path.dirname(config_path),
        ".#{Path.basename(config_path)}.stage-#{System.unique_integer([:positive, :monotonic])}"
      )

    if File.exists?(stage), do: unique_stage(config_path), else: stage
  end

  defp config_root(env) do
    case env_value(env, "XDG_CONFIG_HOME") do
      value when is_binary(value) and value != "" -> value
      _other -> Path.join(home_directory(env), ".config")
    end
  end

  defp home_directory(env) do
    case env_value(env, "HOME") do
      value when is_binary(value) and value != "" -> value
      _other -> System.user_home!()
    end
  end

  defp env_value(env, key) when is_map(env) do
    Map.get(env, key) ||
      case key do
        "XDG_CONFIG_HOME" -> Map.get(env, :XDG_CONFIG_HOME)
        "HOME" -> Map.get(env, :HOME)
      end
  end

  defp env_value(env, key) when is_list(env) do
    Enum.find_value(env, fn
      {^key, value} -> value
      {atom_key, value} when is_atom(atom_key) -> if Atom.to_string(atom_key) == key, do: value
      _entry -> nil
    end)
  end

  defp env_value(_env, key), do: System.get_env(key)

  defp normalize_options(options) when is_list(options) do
    if Keyword.keyword?(options), do: options, else: []
  end

  defp normalize_options(options) when is_map(options), do: Map.to_list(options)
  defp normalize_options(_options), do: []

  defp option(options, key, default \\ nil), do: Keyword.get(options, key, default)

  defp error(message), do: {:error, :invalid_configuration, message}
end
