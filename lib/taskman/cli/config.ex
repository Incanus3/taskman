defmodule Taskman.CLI.Config do
  @moduledoc "Secure local configuration for the Taskman CLI."

  import Bitwise

  @default_api_url "http://localhost:4000"
  @config_directory "taskman"
  @config_filename "config.json"
  @allowed_keys ["api_url", "api_key"]

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

    with {:ok, stored} <- read(path(options), options),
         api_url <-
           explicit_value(explicit, :api_url) || env_value(env, "TASKMAN_API_URL") ||
             stored["api_url"] || @default_api_url,
         :ok <- validate_url(api_url) do
      {:ok,
       %{
         api_url: api_url,
         api_key: env_value(env, "TASKMAN_API_KEY") || stored["api_key"]
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
         {:ok, stored} <- read(path(options), options) do
      write(Map.put(stored, "api_url", api_url), path(options), options)
    end
  end

  def set_url(_api_url, _options),
    do: error("The Taskman API URL must be a valid HTTP or HTTPS base URL")

  @doc "Store a prompted API key without validating or displaying its server-issued contents."
  @spec set_key(String.t(), keyword() | map()) ::
          :ok | {:error, :invalid_configuration, String.t()}
  def set_key(api_key, options \\ [])

  def set_key(api_key, options) when is_binary(api_key) and byte_size(api_key) > 0 do
    options = normalize_options(options)

    with {:ok, stored} <- read(path(options), options) do
      write(Map.put(stored, "api_key", api_key), path(options), options)
    end
  end

  def set_key(_api_key, _options), do: error("The Taskman API key cannot be empty")

  @doc "Return a display-safe configuration summary."
  @spec display(resolved_config()) :: %{api_url: String.t(), api_key_configured: boolean()}
  def display(%{api_url: api_url, api_key: api_key}) when is_binary(api_url) do
    %{api_url: api_url, api_key_configured: is_binary(api_key) and api_key != ""}
  end

  defp read(config_path, options) do
    with {:ok, %File.Stat{type: :regular, mode: mode}} <- File.stat(config_path),
         :ok <- validate_directory(Path.dirname(config_path)),
         :ok <- validate_file_mode(mode),
         {:ok, contents} <- read_file(config_path, options),
         {:ok, decoded} <- decode(contents),
         :ok <- validate_shape(decoded),
         :ok <- validate_stored_url(decoded) do
      {:ok, decoded}
    else
      {:ok, %File.Stat{}} -> error("The Taskman configuration path must be a regular file")
      {:error, :enoent} -> {:ok, %{}}
      {:error, _reason} -> error("The Taskman configuration file could not be read")
      {:error, :invalid_configuration, _message} = result -> result
    end
  end

  defp read_file(config_path, options) do
    case option(options, :read_file) do
      read_file when is_function(read_file, 1) -> read_file.(config_path)
      _other -> File.read(config_path)
    end
  rescue
    _error -> {:error, :read_failed}
  end

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

  defp validate_stored_url(%{"api_url" => api_url}), do: validate_url(api_url)
  defp validate_stored_url(_decoded), do: :ok

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
    case File.stat(directory) do
      {:ok, %File.Stat{type: :directory, mode: mode}} when (mode &&& 0o022) == 0 ->
        :ok

      {:ok, %File.Stat{type: :directory}} ->
        error("Unsafe Taskman configuration directory permissions")

      {:ok, _stat} ->
        error("The Taskman configuration directory is not a directory")

      {:error, _reason} ->
        error("The Taskman configuration directory could not be read")
    end
  end

  defp validate_file_mode(mode) when (mode &&& 0o077) == 0, do: :ok

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
        :ok
      else
        {:error, _reason} -> error("Could not atomically replace the Taskman configuration file")
      end

    case result do
      :ok ->
        :ok

      {:error, :invalid_configuration, _message} = error ->
        _ = File.rm(stage)
        error
    end
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
