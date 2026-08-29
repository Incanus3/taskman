defmodule Taskman.CLI.Commands.Projects do
  @moduledoc "HTTP-backed handlers for Project CLI commands."

  alias Taskman.CLI.{Client, Invocation, Output, Result}

  @doc "Execute a Project command against the configured Taskman API."
  @spec execute(atom(), Invocation.t(), keyword() | map()) :: Result.t()
  def execute(action, %Invocation{} = invocation, runtime_options \\ []) do
    json? = Map.get(invocation.globals, :json, false)
    runtime_options = with_api_url(runtime_options, Map.get(invocation.globals, :api_url))

    case action do
      :list ->
        request(
          invocation,
          :get,
          "/api/v1/projects",
          [],
          runtime_options,
          json?,
          {:collection, :project}
        )

      :show ->
        project_id = Map.fetch!(invocation.arguments, :project_id)

        request(
          invocation,
          :get,
          "/api/v1/projects/#{project_id}",
          [],
          runtime_options,
          json?,
          {:member, :project}
        )

      :create ->
        body = %{
          "project" => %{
            "name" => Map.fetch!(invocation.options, :name),
            "primary_directory" => Map.fetch!(invocation.options, :directory)
          }
        }

        request(
          invocation,
          :post,
          "/api/v1/projects",
          [json: body],
          runtime_options,
          json?,
          {:member, :project}
        )

      _other ->
        internal_error(invocation, json?)
    end
  end

  defp request(
         invocation,
         method,
         path,
         request_options,
         runtime_options,
         json?,
         success_shape
       ) do
    case Client.request(method, path, request_options, runtime_options, success_shape) do
      {:ok, data} -> %Result{stdout: Output.success(invocation.command, data, json?)}
      {:error, status, envelope} -> %Result{status: status, stderr: Output.error(envelope, json?)}
    end
  end

  defp internal_error(_invocation, json?) do
    message = "Unsupported Project command"
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
