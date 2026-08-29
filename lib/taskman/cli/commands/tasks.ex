defmodule Taskman.CLI.Commands.Tasks do
  @moduledoc "HTTP-backed handlers for Task CLI commands."

  alias Taskman.CLI.{Client, Invocation, Output, Result}

  @task_fields ~w(title description status priority due_at)a

  @doc "Execute a Task command against the configured Taskman API."
  @spec execute(atom(), Invocation.t(), keyword() | map()) :: Result.t()
  def execute(action, %Invocation{} = invocation, runtime_options \\ []) do
    json? = Map.get(invocation.globals, :json, false)
    runtime_options = with_api_url(runtime_options, Map.get(invocation.globals, :api_url))
    project_id = Map.fetch!(invocation.options, :project)

    case action do
      :list ->
        query = task_list_query(invocation.options)
        request_options = if query == %{}, do: [], else: [params: query]

        request(
          invocation,
          :get,
          "/api/v1/projects/#{project_id}/tasks",
          request_options,
          runtime_options,
          json?,
          {:collection, :task}
        )

      :show ->
        task_id = Map.fetch!(invocation.arguments, :task_id)

        request(
          invocation,
          :get,
          "/api/v1/projects/#{project_id}/tasks/#{task_id}",
          [],
          runtime_options,
          json?
        )

      :create ->
        task = task_fields(invocation.options)
        task = maybe_put_list(task, invocation.options)
        body = %{"task" => task}

        request(
          invocation,
          :post,
          "/api/v1/projects/#{project_id}/tasks",
          [json: body],
          runtime_options,
          json?
        )

      :update ->
        task_id = Map.fetch!(invocation.arguments, :task_id)
        task = task_fields(invocation.options)
        task = maybe_clear_due_at(task, invocation.options)
        body = %{"task" => task}

        request(
          invocation,
          :patch,
          "/api/v1/projects/#{project_id}/tasks/#{task_id}",
          [json: body],
          runtime_options,
          json?
        )

      :move ->
        task_id = Map.fetch!(invocation.arguments, :task_id)
        destination = %{"list_id" => destination_list_id(invocation.options)}
        body = %{"destination" => destination}

        request(
          invocation,
          :post,
          "/api/v1/projects/#{project_id}/tasks/#{task_id}/move",
          [json: body],
          runtime_options,
          json?
        )

      _other ->
        internal_error(json?)
    end
  end

  defp task_list_query(options) do
    %{}
    |> maybe_put_query(:list, "list_id", options)
    |> maybe_put_query(:include_descendants, "include_descendants", options)
  end

  defp maybe_put_query(query, option, key, options) do
    if Map.has_key?(options, option) do
      Map.put(query, key, Map.fetch!(options, option))
    else
      query
    end
  end

  defp task_fields(options) do
    Enum.reduce(@task_fields, %{}, fn field, task ->
      if Map.has_key?(options, field) do
        Map.put(task, Atom.to_string(field), Map.fetch!(options, field))
      else
        task
      end
    end)
  end

  defp maybe_put_list(task, options) do
    if Map.has_key?(options, :list) do
      Map.put(task, "list_id", Map.fetch!(options, :list))
    else
      task
    end
  end

  defp maybe_clear_due_at(task, options) do
    if Map.get(options, :clear_due_at, false) do
      Map.put(task, "due_at", nil)
    else
      task
    end
  end

  defp destination_list_id(options) do
    if Map.get(options, :to_project_root, false) do
      nil
    else
      Map.fetch!(options, :to_list)
    end
  end

  defp request(
         invocation,
         method,
         path,
         request_options,
         runtime_options,
         json?,
         success_shape \\ {:member, :task}
       ) do
    case Client.request(method, path, request_options, runtime_options, success_shape) do
      {:ok, data} -> %Result{stdout: Output.success(invocation.command, data, json?)}
      {:error, status, envelope} -> %Result{status: status, stderr: Output.error(envelope, json?)}
    end
  end

  defp internal_error(json?) do
    message = "Unsupported Task command"
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
