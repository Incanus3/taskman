defmodule Taskman.CLI.Config.Document do
  @moduledoc "Pure validation and projection of the CLI's stored host configuration."

  @legacy_keys ["api_url", "api_key"]
  @document_keys ["version", "default_host", "hosts"]
  @host_keys ["api_url", "api_key"]
  @host_name_pattern ~r/\A[a-z][a-z0-9_-]{0,63}\z/
  @api_key_pattern ~r/\Atm_[A-Za-z0-9_]+_[A-Za-z0-9]+\z/
  @legacy_api_url "http://localhost:4000"

  @type host :: %{api_url: String.t(), api_key: String.t() | nil}
  @type t :: %{
          format: :absent | :legacy | :v2,
          hosts: %{String.t() => host()},
          default_host: String.t() | nil
        }
  @type local_error :: {:error, :invalid_configuration, String.t()}

  @doc "Decode and strictly validate stored JSON, projecting legacy settings without IO."
  @spec decode(binary()) :: {:ok, t()} | local_error()
  def decode(contents) when is_binary(contents) do
    case Jason.decode(contents) do
      {:ok, decoded} when is_map(decoded) -> decode_object(decoded)
      {:ok, _other} -> error("The Taskman configuration file must contain an object")
      {:error, _reason} -> error("The Taskman configuration file must contain valid JSON")
    end
  end

  def decode(_contents), do: error("The Taskman configuration file must contain valid JSON")

  @doc "Encode a validated host document in the version 2 persisted shape."
  @spec encode(t()) :: map()
  def encode(%{hosts: hosts, default_host: default_host}) do
    hosts =
      Map.new(hosts, fn {name, host} ->
        {name, optional_field(%{"api_url" => host.api_url}, "api_key", host.api_key)}
      end)

    optional_field(%{"version" => 2, "hosts" => hosts}, "default_host", default_host)
  end

  @doc "Validate an exact lowercase ASCII host name without normalization."
  @spec validate_host_name(term()) :: :ok | local_error()
  def validate_host_name(name) when is_binary(name) do
    if Regex.match?(@host_name_pattern, name),
      do: :ok,
      else: invalid_host_name()
  end

  def validate_host_name(_name), do: invalid_host_name()

  @doc "Validate a constrained HTTP or HTTPS base URL without displaying its contents."
  @spec validate_url(term()) :: :ok | local_error()
  def validate_url(url) when is_binary(url) do
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

  def validate_url(_url), do: invalid_url()

  @doc "Validate a complete server-token shape without displaying the credential."
  @spec validate_api_key(term()) :: :ok | local_error()
  def validate_api_key(api_key) when is_binary(api_key) do
    if Regex.match?(@api_key_pattern, api_key), do: :ok, else: invalid_api_key()
  end

  def validate_api_key(_api_key), do: invalid_api_key()

  defp decode_object(%{"version" => 2, "hosts" => hosts} = decoded) when is_map(hosts) do
    with :ok <- allowed_fields(decoded, @document_keys),
         {:ok, hosts} <- decode_hosts(hosts),
         :ok <- validate_default(Map.get(decoded, "default_host"), hosts) do
      {:ok, %{format: :v2, hosts: hosts, default_host: Map.get(decoded, "default_host")}}
    end
  end

  defp decode_object(%{"version" => _version}),
    do: error("The Taskman configuration file requires version 2 and a hosts object")

  defp decode_object(decoded) do
    with :ok <- allowed_fields(decoded, @legacy_keys),
         :ok <- validate_optional_url(Map.get(decoded, "api_url")),
         :ok <- validate_optional_key(Map.get(decoded, "api_key")) do
      case {Map.get(decoded, "api_url"), Map.get(decoded, "api_key")} do
        {nil, nil} ->
          {:ok, %{format: :legacy, hosts: %{}, default_host: nil}}

        {api_url, api_key} ->
          {:ok,
           %{
             format: :legacy,
             hosts: %{"default" => %{api_url: api_url || @legacy_api_url, api_key: api_key}},
             default_host: "default"
           }}
      end
    end
  end

  defp decode_hosts(hosts) do
    Enum.reduce_while(hosts, {:ok, %{}}, fn {name, entry}, {:ok, decoded} ->
      with :ok <- validate_host_name(name),
           {:ok, host} <- decode_host(entry) do
        {:cont, {:ok, Map.put(decoded, name, host)}}
      else
        error -> {:halt, error}
      end
    end)
  end

  defp decode_host(entry) when is_map(entry) do
    with :ok <- allowed_fields(entry, @host_keys),
         :ok <- validate_url(Map.get(entry, "api_url")),
         :ok <- validate_optional_key(Map.get(entry, "api_key")) do
      {:ok, %{api_url: entry["api_url"], api_key: Map.get(entry, "api_key")}}
    end
  end

  defp decode_host(_entry), do: error("Each Taskman host must contain an object")

  defp validate_default(nil, _hosts), do: :ok

  defp validate_default(name, hosts) do
    with :ok <- validate_host_name(name) do
      if Map.has_key?(hosts, name),
        do: :ok,
        else: error("The configured default host must name a registered host")
    end
  end

  defp allowed_fields(object, fields) do
    if Enum.all?(Map.keys(object), &(&1 in fields)),
      do: :ok,
      else: error("The Taskman configuration file contains unsupported fields")
  end

  defp validate_optional_url(nil), do: :ok
  defp validate_optional_url(url), do: validate_url(url)
  defp validate_optional_key(nil), do: :ok
  defp validate_optional_key(key), do: validate_api_key(key)

  defp optional_field(map, _field, nil), do: map
  defp optional_field(map, field, value), do: Map.put(map, field, value)

  defp invalid_host_name,
    do:
      error(
        "Host names must use 1 to 64 lowercase ASCII letters, digits, underscores or hyphens, starting with a letter"
      )

  defp invalid_url, do: error("The Taskman API URL must be a valid HTTP or HTTPS base URL")

  defp invalid_api_key,
    do: error("The Taskman API key must be a valid server-issued credential")

  defp error(message), do: {:error, :invalid_configuration, message}
end
