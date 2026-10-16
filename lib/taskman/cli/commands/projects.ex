defmodule Taskman.CLI.Commands.Projects do
  @moduledoc "HTTP-backed handlers for Project CLI commands."

  alias Taskman.CLI.Client
  alias Taskman.CLI.Execution.{Invocation, Result}

  @doc "Execute a Project command against the configured Taskman API."
  @spec execute(atom(), Invocation.t(), keyword() | map()) :: Result.t()
  def execute(action, %Invocation{} = invocation, runtime_options \\ []) do
    json? = Map.get(invocation.globals, :json, false)

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
        body = %{"project" => project_attrs(invocation.options)}

        request(
          invocation,
          :post,
          "/api/v1/projects",
          [json: body],
          runtime_options,
          json?,
          {:member, :project}
        )

      :update ->
        project_id = Map.fetch!(invocation.arguments, :project_id)
        body = %{"project" => project_attrs(invocation.options)}

        request(
          invocation,
          :patch,
          "/api/v1/projects/#{project_id}",
          [json: body],
          runtime_options,
          json?,
          {:member, :project}
        )

      _other ->
        internal_error(invocation, json?)
    end
  end

  defp project_attrs(options) do
    options
    |> Map.take([:name, :description, :icon, :color])
    |> Map.new(fn {key, value} -> {Atom.to_string(key), value} end)
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
    Client.request(method, path, request_options, runtime_options, success_shape)
    |> Result.from_response(invocation.command, json?)
  end

  defp internal_error(_invocation, json?) do
    message = "Unsupported Project command"
    envelope = %{"error" => %{"code" => "internal_error", "message" => message}}

    Result.error(5, envelope, json?)
  end
end
