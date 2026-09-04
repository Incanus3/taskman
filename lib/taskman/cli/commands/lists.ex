defmodule Taskman.CLI.Commands.Lists do
  @moduledoc "HTTP-backed handlers for List CLI commands."

  alias Taskman.CLI.Client
  alias Taskman.CLI.Execution.{Invocation, Result}
  alias Taskman.CLI.Presentation.Output

  @doc "Execute a List command against the configured Taskman API."
  @spec execute(atom(), Invocation.t(), keyword() | map()) :: Result.t()
  def execute(action, %Invocation{} = invocation, runtime_options \\ []) do
    json? = Map.get(invocation.globals, :json, false)
    runtime_options = with_api_url(runtime_options, Map.get(invocation.globals, :api_url))
    project_id = Map.fetch!(invocation.options, :project)

    case action do
      :list ->
        request(
          invocation,
          :get,
          "/api/v1/projects/#{project_id}/lists",
          [],
          runtime_options,
          json?,
          {:collection, :list}
        )

      :show ->
        list_id = Map.fetch!(invocation.arguments, :list_id)

        request(
          invocation,
          :get,
          "/api/v1/projects/#{project_id}/lists/#{list_id}",
          [],
          runtime_options,
          json?
        )

      :create ->
        list = %{"name" => Map.fetch!(invocation.options, :name)}
        list = maybe_put_parent(list, invocation.options)
        body = %{"list" => list}

        request(
          invocation,
          :post,
          "/api/v1/projects/#{project_id}/lists",
          [json: body],
          runtime_options,
          json?
        )

      :rename ->
        list_id = Map.fetch!(invocation.arguments, :list_id)
        body = %{"list" => %{"name" => Map.fetch!(invocation.options, :name)}}

        request(
          invocation,
          :patch,
          "/api/v1/projects/#{project_id}/lists/#{list_id}",
          [json: body],
          runtime_options,
          json?
        )

      _other ->
        internal_error(json?)
    end
  end

  defp maybe_put_parent(list, options) do
    if Map.has_key?(options, :parent) do
      Map.put(list, "parent_list_id", Map.fetch!(options, :parent))
    else
      list
    end
  end

  defp request(
         invocation,
         method,
         path,
         request_options,
         runtime_options,
         json?,
         success_shape \\ {:member, :list}
       ) do
    case Client.request(method, path, request_options, runtime_options, success_shape) do
      {:ok, data} -> %Result{stdout: Output.success(invocation.command, data, json?)}
      {:error, status, envelope} -> %Result{status: status, stderr: Output.error(envelope, json?)}
    end
  end

  defp internal_error(json?) do
    message = "Unsupported List command"
    envelope = %{"error" => %{"code" => "internal_error", "message" => message}}

    %Result{status: 5, stderr: Output.error(envelope, json?)}
  end

  defp with_api_url(runtime_options, nil), do: runtime_options

  defp with_api_url(runtime_options, api_url) when is_list(runtime_options),
    do: Keyword.put(runtime_options, :api_url, api_url)

  defp with_api_url(runtime_options, api_url) when is_map(runtime_options),
    do: Map.put(runtime_options, :api_url, api_url)

  defp with_api_url(_runtime_options, api_url), do: [api_url: api_url]
end
