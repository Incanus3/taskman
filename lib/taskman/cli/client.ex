defmodule Taskman.CLI.Client do
  @moduledoc "Req-backed transport for the versioned Taskman JSON API."

  alias Taskman.CLI.Registry

  @default_api_url "http://localhost:4000"
  @connection_guidance "Start the backend from the Taskman repository with `mix phx.server`, or run `taskman agent onboarding` for setup guidance."
  @error_contract %{
    400 => {"invalid_request", 3},
    404 => {"not_found", 3},
    409 => {["unchanged_location", "concurrent_update"], 3},
    422 => {"validation_failed", 3},
    500 => {"internal_error", 5}
  }

  @type request_options :: keyword() | map()
  @type runtime_options :: keyword() | map()
  @type error_envelope :: map()
  @type success_shape ::
          :any | :hierarchy | {:collection | :member, :project | :list | :task}

  @doc "Make one API request and classify transport and response-contract failures."
  @spec request(atom(), String.t(), request_options(), runtime_options(), success_shape()) ::
          {:ok, term()} | {:error, non_neg_integer(), error_envelope()}
  def request(method, path, request_options \\ [], runtime_options \\ [], success_shape \\ :any) do
    request_options = normalize_options(request_options)
    runtime_options = normalize_options(runtime_options)

    api_url =
      option(runtime_options, :api_url) || option(request_options, :api_url) || @default_api_url

    api_key = option(runtime_options, :api_key) || option(request_options, :api_key)
    req_options = runtime_options |> option(:req_options, []) |> normalize_options()

    request_options =
      request_options
      |> Keyword.delete(:api_url)
      |> Keyword.delete(:api_key)

    try do
      with :ok <- ensure_req_started(),
           {:ok, response} <-
             perform_request(method, path, request_options, api_url, req_options, api_key) do
        classify_response(response, api_url, success_shape)
      else
        {:error, reason} -> classify_transport_error(reason, api_url)
        other -> classify_transport_error(other, api_url)
      end
    rescue
      exception -> classify_exception(exception, api_url)
    end
  end

  defp perform_request(method, path, request_options, api_url, req_options, api_key) do
    req =
      Req.new([base_url: api_url, receive_timeout: 15_000, retry: false] ++ req_options)

    options =
      [method: method, url: path]
      |> maybe_put(request_options, :json, [:json, :json_body])
      |> maybe_put(request_options, :params, [:params, :query])
      |> maybe_put_bearer_header(api_key)

    Req.request(req, options)
  end

  defp maybe_put_bearer_header(options, api_key) when is_binary(api_key) and api_key != "" do
    headers = Keyword.get(options, :headers, [])
    Keyword.put(options, :headers, [{"authorization", "Bearer " <> api_key} | headers])
  end

  defp maybe_put_bearer_header(options, _api_key), do: options

  defp maybe_put(options, request_options, key, aliases) do
    case Enum.find(aliases, &Keyword.has_key?(request_options, &1)) do
      nil -> options
      option -> Keyword.put(options, key, Keyword.fetch!(request_options, option))
    end
  end

  defp classify_response(%Req.Response{status: status, body: body}, api_url, success_shape)
       when status in 200..299 do
    case valid_success(body, success_shape) do
      {:ok, data} -> {:ok, data}
      :error -> invalid_response(api_url)
    end
  end

  defp classify_response(%Req.Response{status: status, body: body}, api_url, _success_shape)
       when status >= 400 do
    case Map.fetch(@error_contract, status) do
      {:ok, {expected_code, exit_status}} ->
        case valid_error(body, expected_code) do
          {:ok, envelope} -> {:error, exit_status, envelope}
          :error -> invalid_response(api_url)
        end

      :error ->
        invalid_response(api_url)
    end
  end

  defp classify_response(_response, api_url, _success_shape), do: invalid_response(api_url)

  defp valid_success(body, success_shape) when is_map(body) do
    if Map.keys(body) == ["data"] do
      case Map.fetch(body, "data") do
        {:ok, data} when is_map(data) or is_list(data) ->
          if valid_success_data?(data, success_shape), do: {:ok, data}, else: :error

        _ ->
          :error
      end
    else
      :error
    end
  end

  defp valid_success(_body, _success_shape), do: :error

  defp valid_success_data?(data, :any), do: is_map(data) or is_list(data)

  defp valid_success_data?(data, {:collection, resource}) when is_list(data),
    do: Enum.all?(data, &valid_resource?(&1, resource))

  defp valid_success_data?(data, {:member, resource}) when is_map(data),
    do: valid_resource?(data, resource)

  defp valid_success_data?(data, :hierarchy) when is_map(data), do: valid_hierarchy?(data)

  defp valid_success_data?(_data, _success_shape), do: false

  defp valid_resource?(project, :project) when is_map(project) do
    required_keys?(project, ~w(id name primary_directory)) and
      positive_integer?(project["id"]) and
      is_binary(project["name"]) and
      is_binary(project["primary_directory"])
  end

  defp valid_resource?(task_list, :list) when is_map(task_list) do
    required_keys?(task_list, ~w(id project_id parent_list_id name path)) and
      positive_integer?(task_list["id"]) and
      positive_integer?(task_list["project_id"]) and
      optional_positive_integer?(task_list["parent_list_id"]) and
      is_binary(task_list["name"]) and
      string_list?(task_list["path"])
  end

  defp valid_resource?(task, :task) when is_map(task) do
    required_keys?(
      task,
      ~w(id project_id list_id parent_task_id title description status priority due_at location)
    ) and
      positive_integer?(task["id"]) and
      positive_integer?(task["project_id"]) and
      optional_positive_integer?(task["list_id"]) and
      optional_positive_integer?(task["parent_task_id"]) and
      is_binary(task["title"]) and
      is_binary(task["description"]) and
      task["status"] in Registry.statuses() and
      task["priority"] in Registry.priorities() and
      valid_due_at?(task["due_at"]) and
      valid_task_location?(task["location"], task["list_id"])
  end

  defp valid_resource?(_resource, _type), do: false

  defp valid_hierarchy?(hierarchy) do
    required_keys?(hierarchy, ~w(selected_task_id root)) and
      positive_integer?(hierarchy["selected_task_id"]) and
      valid_hierarchy_node?(hierarchy["root"])
  end

  defp valid_hierarchy_node?(%{"task" => task, "children" => children}) when is_list(children) do
    valid_resource?(task, :task) and Enum.all?(children, &valid_hierarchy_node?/1)
  end

  defp valid_hierarchy_node?(_node), do: false

  defp required_keys?(map, keys), do: Enum.all?(keys, &Map.has_key?(map, &1))
  defp positive_integer?(value), do: is_integer(value) and value > 0
  defp optional_positive_integer?(nil), do: true
  defp optional_positive_integer?(value), do: positive_integer?(value)
  defp string_list?(value), do: is_list(value) and Enum.all?(value, &is_binary/1)

  defp valid_due_at?(nil), do: true

  defp valid_due_at?(value) when is_binary(value),
    do: match?({:ok, _due_at}, NaiveDateTime.from_iso8601(value))

  defp valid_due_at?(_value), do: false

  defp valid_task_location?(location, nil) when is_map(location) do
    required_keys?(location, ~w(kind list_id path)) and
      location["kind"] == "project" and
      is_nil(location["list_id"]) and
      location["path"] == []
  end

  defp valid_task_location?(location, list_id) when is_map(location) do
    required_keys?(location, ~w(kind list_id path)) and
      location["kind"] == "list" and
      location["list_id"] == list_id and
      positive_integer?(list_id) and
      string_list?(location["path"]) and
      location["path"] != []
  end

  defp valid_task_location?(_location, _list_id), do: false

  defp valid_error(body, expected_code) when is_map(body) do
    with true <- Map.keys(body) == ["error"],
         {:ok, error} <- Map.fetch(body, "error"),
         true <- is_map(error),
         true <- valid_error_keys?(error),
         {:ok, code} <- Map.fetch(error, "code"),
         {:ok, message} <- Map.fetch(error, "message"),
         true <- is_binary(code) and code != "",
         true <- valid_error_code?(code, expected_code),
         true <- is_binary(message) and message != "",
         true <- valid_fields?(error, code) do
      {:ok, body}
    else
      _ -> :error
    end
  end

  defp valid_error(_body, _expected_code), do: :error

  defp valid_error_keys?(error) do
    Enum.all?(Map.keys(error), &(&1 in ["code", "message", "fields"]))
  end

  defp valid_error_code?(code, expected_code) when is_binary(expected_code),
    do: code == expected_code

  defp valid_error_code?(code, expected_codes) when is_list(expected_codes),
    do: code in expected_codes

  defp valid_fields?(error, "concurrent_update") do
    case Map.fetch(error, "fields") do
      {:ok, fields} when is_map(fields) and map_size(fields) > 0 ->
        Enum.all?(fields, fn {field, messages} ->
          is_binary(field) and field != "" and
            is_list(messages) and messages != [] and
            Enum.all?(messages, &(is_binary(&1) and &1 != ""))
        end)

      _ ->
        false
    end
  end

  defp valid_fields?(error, _code) do
    case Map.fetch(error, "fields") do
      :error ->
        true

      {:ok, fields} when is_map(fields) ->
        Enum.all?(fields, fn {_field, messages} ->
          is_list(messages) and Enum.all?(messages, &is_binary/1)
        end)

      _ ->
        false
    end
  end

  defp classify_transport_error(%Req.TransportError{} = exception, api_url) do
    {:error, 4, error("connection_failed", connection_message(exception, api_url))}
  end

  defp classify_transport_error(%Mint.TransportError{} = exception, api_url) do
    {:error, 4, error("connection_failed", connection_message(exception, api_url))}
  end

  defp classify_transport_error(reason, api_url)
       when reason in [
              :closed,
              :econnrefused,
              :enetdown,
              :enetunreach,
              :ehostdown,
              :ehostunreach,
              :nxdomain,
              :timeout
            ] do
    {:error, 4,
     error(
       "connection_failed",
       connection_message(reason, api_url)
     )}
  end

  defp classify_transport_error({:failed_connect, _details} = reason, api_url) do
    {:error, 4,
     error(
       "connection_failed",
       connection_message(reason, api_url)
     )}
  end

  defp classify_transport_error({:error, reason}, api_url),
    do: classify_transport_error(reason, api_url)

  defp classify_transport_error(reason, api_url) do
    {:error, 5, error("invalid_response", contract_message(reason, api_url))}
  end

  defp classify_exception(%Req.TransportError{} = exception, api_url),
    do: classify_transport_error(exception, api_url)

  defp classify_exception(%Mint.TransportError{} = exception, api_url),
    do: classify_transport_error(exception, api_url)

  defp classify_exception(exception, api_url) do
    {:error, 5, error("invalid_response", contract_message(exception, api_url))}
  end

  defp invalid_response(api_url) do
    {:error, 5, error("invalid_response", "Taskman API response from #{api_url} was invalid")}
  end

  defp connection_message(%{__exception__: true} = exception, api_url),
    do: connection_message(Exception.message(exception), api_url)

  defp connection_message(detail, api_url) when is_binary(detail),
    do: "Could not connect to Taskman API at #{api_url}: #{detail}. #{@connection_guidance}"

  defp connection_message(reason, api_url),
    do: connection_message(inspect(reason), api_url)

  defp contract_message(%{__exception__: true} = exception, api_url) do
    "Taskman API response from #{api_url} was invalid: #{Exception.message(exception)}"
  end

  defp contract_message(reason, api_url) do
    "Taskman API response from #{api_url} was invalid: #{inspect(reason)}"
  end

  defp error(code, message), do: %{"error" => %{"code" => code, "message" => message}}

  defp ensure_req_started do
    case Application.ensure_all_started(:req) do
      {:ok, _started} -> :ok
      {:error, reason} -> {:error, reason}
    end
  end

  defp option(options, key, default \\ nil) when is_list(options),
    do: Keyword.get(options, key, default)

  defp normalize_options(options) when is_list(options) do
    if Keyword.keyword?(options), do: options, else: []
  end

  defp normalize_options(options) when is_map(options) do
    Enum.reduce(options, [], fn
      {key, value}, acc when is_atom(key) -> Keyword.put(acc, key, value)
      {"api_url", value}, acc -> Keyword.put(acc, :api_url, value)
      {"req_options", value}, acc -> Keyword.put(acc, :req_options, value)
      {"api_key", value}, acc -> Keyword.put(acc, :api_key, value)
      {"json", value}, acc -> Keyword.put(acc, :json, value)
      {"json_body", value}, acc -> Keyword.put(acc, :json_body, value)
      {"params", value}, acc -> Keyword.put(acc, :params, value)
      {"query", value}, acc -> Keyword.put(acc, :query, value)
      _entry, acc -> acc
    end)
  end

  defp normalize_options(_options), do: []
end
