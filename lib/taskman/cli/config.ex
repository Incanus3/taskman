defmodule Taskman.CLI.Config do
  @moduledoc "Secure local configuration for the Taskman CLI."

  import Bitwise

  @default_api_url "http://localhost:4000"
  @config_directory "taskman"
  @config_filename "config.json"
  @allowed_keys ["api_url", "api_key"]
  @api_key_pattern ~r/\Atm_[A-Za-z0-9_]+_[A-Za-z0-9]+\z/
  @lock_marker "taskman_config_lock_v1"
  @lock_retry_limit 200
  @lock_retry_delay_ms 10

  @typedoc "The API connection settings after source precedence has been applied."
  @type resolved_config :: %{api_url: String.t(), api_key: String.t() | nil}

  @doc "Return the configuration file path, using an injected root or environment when supplied."
  @spec path(keyword() | map()) :: Path.t()
  def path(options \\ []) do
    options = normalize_options(options)
    root = option(options, :config_root) || config_root(option(options, :env, System.get_env()))
    Path.join([root, @config_directory, @config_filename])
  end

  @doc "Resolve the API URL and key from flags, environment, and the protected config file."
  @spec resolve(map(), keyword() | map()) ::
          {:ok, resolved_config()} | {:error, :invalid_configuration, String.t()}
  def resolve(explicit \\ %{}, options \\ [])

  def resolve(explicit, options) when is_map(explicit) do
    options = normalize_options(options)
    env = option(options, :env, System.get_env())
    api_url_override = explicit_value(explicit, :api_url) || env_value(env, "TASKMAN_API_URL")
    api_key_override = env_value(env, "TASKMAN_API_KEY")

    with {:ok, stored} <-
           read_if_needed(api_url_override, api_key_override, path(options), options),
         api_url <- api_url_override || stored["api_url"] || @default_api_url,
         api_key <- api_key_override || stored["api_key"],
         :ok <- validate_url(api_url),
         :ok <- validate_optional_api_key(api_key) do
      {:ok,
       %{
         api_url: api_url,
         api_key: api_key
       }}
    end
  end

  def resolve(_explicit, _options), do: error("Configuration overrides must be a map")

  @doc "Store a validated API URL without exposing any configured credential."
  @spec set_url(String.t(), keyword() | map()) ::
          :ok | {:error, :invalid_configuration, String.t()}
  def set_url(api_url, options \\ [])

  def set_url(api_url, options) when is_binary(api_url) do
    options = normalize_options(options)

    with :ok <- validate_url(api_url),
         :ok <- ensure_directory(Path.dirname(path(options))) do
      with_config_lock(path(options), options, fn ->
        with {:ok, stored} <- read(path(options), options),
             :ok <- before_write(path(options), options) do
          write(Map.put(stored, "api_url", api_url), path(options), options)
        end
      end)
    end
  end

  def set_url(_api_url, _options),
    do: error("The Taskman API URL must be a valid HTTP or HTTPS base URL")

  @doc "Store a structurally valid prompted API key without displaying it."
  @spec set_key(String.t(), keyword() | map()) ::
          :ok | {:error, :invalid_configuration, String.t()}
  def set_key(api_key, options \\ [])

  def set_key(api_key, options) when is_binary(api_key) do
    options = normalize_options(options)

    with :ok <- validate_api_key(api_key),
         :ok <- ensure_directory(Path.dirname(path(options))) do
      with_config_lock(path(options), options, fn ->
        with {:ok, stored} <- read(path(options), options),
             :ok <- before_write(path(options), options) do
          write(Map.put(stored, "api_key", api_key), path(options), options)
        end
      end)
    end
  end

  def set_key(_api_key, _options), do: invalid_api_key()

  @doc "Return a display-safe configuration summary."
  @spec display(resolved_config()) :: %{api_url: String.t(), api_key_configured: boolean()}
  def display(%{api_url: api_url, api_key: api_key}) when is_binary(api_url) do
    %{api_url: api_url, api_key_configured: is_binary(api_key) and api_key != ""}
  end

  defp read_if_needed(nil, _api_key_override, config_path, options),
    do: read(config_path, options)

  defp read_if_needed(_api_url_override, nil, config_path, options),
    do: read(config_path, options)

  defp read_if_needed(_api_url_override, _api_key_override, _config_path, _options),
    do: {:ok, %{}}

  defp read(config_path, options) do
    case lstat(config_path) do
      {:error, :enoent} ->
        {:ok, %{}}

      {:ok, lstat} ->
        with :ok <- validate_directory(Path.dirname(config_path)),
             :ok <- validate_config_file(lstat),
             :ok <- before_open(config_path, options),
             {:ok, device} <- open_readonly(config_path) do
          try do
            with {:ok, contents} <- read_opened_file(device, options),
                 {:ok, opened_stat} <- file_info(device),
                 :ok <- validate_opened_file(lstat, opened_stat),
                 {:ok, decoded} <- decode(contents),
                 :ok <- validate_shape(decoded) do
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

  defp decode(contents) do
    case Jason.decode(contents) do
      {:ok, decoded} -> {:ok, decoded}
      {:error, _reason} -> error("The Taskman configuration file must contain valid JSON")
    end
  end

  defp validate_shape(decoded) when is_map(decoded) do
    cond do
      not Enum.all?(Map.keys(decoded), &(&1 in @allowed_keys)) ->
        error("The Taskman configuration file may contain only api_url and api_key")

      not Enum.all?(decoded, fn {_key, value} -> is_binary(value) end) ->
        error("The Taskman configuration values must be strings")

      true ->
        :ok
    end
  end

  defp validate_shape(_decoded),
    do: error("The Taskman configuration file must contain an object")

  defp validate_url(url) when is_binary(url) do
    case URI.new(url) do
      {:ok,
       %URI{
         scheme: scheme,
         host: host,
         port: port,
         path: path,
         query: nil,
         fragment: nil,
         userinfo: nil
       }}
      when scheme in ["http", "https"] and is_binary(host) and host != "" and
             (is_nil(port) or (is_integer(port) and port in 1..65_535)) and
             path in [nil, "", "/"] ->
        if String.trim(url) == url, do: :ok, else: invalid_url()

      _other ->
        invalid_url()
    end
  end

  defp validate_url(_url), do: invalid_url()

  defp invalid_url, do: error("The Taskman API URL must be a valid HTTP or HTTPS base URL")

  defp validate_optional_api_key(nil), do: :ok
  defp validate_optional_api_key(api_key), do: validate_api_key(api_key)

  defp validate_api_key(api_key) when is_binary(api_key) do
    if Regex.match?(@api_key_pattern, api_key), do: :ok, else: invalid_api_key()
  end

  defp validate_api_key(_api_key), do: invalid_api_key()

  defp invalid_api_key,
    do: error("The Taskman API key must be a valid server-issued credential")

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
        sync_directory(Path.dirname(config_path), options)
      else
        {:error, _reason} -> error("Could not atomically replace the Taskman configuration file")
      end

    case result do
      :ok ->
        :ok

      _other ->
        _ = File.rm(stage)
        error("Could not atomically replace the Taskman configuration file")
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

  defp explicit_value(explicit, key) do
    Map.get(explicit, key) || Map.get(explicit, Atom.to_string(key))
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
        "TASKMAN_API_URL" -> Map.get(env, :TASKMAN_API_URL)
        "TASKMAN_API_KEY" -> Map.get(env, :TASKMAN_API_KEY)
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
