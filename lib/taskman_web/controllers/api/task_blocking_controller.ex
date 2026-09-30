defmodule TaskmanWeb.API.TaskBlockingController do
  use TaskmanWeb, :controller

  action_fallback TaskmanWeb.API.FallbackController

  alias Taskman.Projects
  alias Taskman.Tasks
  alias TaskmanWeb.API.Params
  alias TaskmanWeb.API.Representation

  def index(conn, %{"project_id" => project_id, "task_id" => task_id}) do
    conn = fetch_query_params(conn)

    with :ok <- no_query(conn),
         {:ok, project} <- fetch_project(project_id),
         {:ok, task} <- fetch_scoped_task(project, task_id),
         {:ok, %{blocks: blocks, blocked_by: blocked_by}} <- Tasks.list_blocking(project, task) do
      json(conn, %{
        data: %{
          blocks: Enum.map(blocks, &Representation.linked_task/1),
          blocked_by: Enum.map(blocked_by, &Representation.linked_task/1)
        }
      })
    end
  end

  def create(conn, params), do: mutate(conn, params, :create)
  def delete(conn, params), do: mutate(conn, params, :delete)

  defp mutate(
         conn,
         %{"project_id" => project_id, "task_id" => task_id, "target_task_id" => target_id},
         operation
       ) do
    conn = fetch_query_params(conn)

    with :ok <- no_query(conn),
         :ok <- empty_body(conn),
         {:ok, project} <- fetch_project(project_id),
         {:ok, blocker} <- fetch_scoped_task(project, task_id),
         {:ok, target} <- fetch_target(target_id),
         {:ok, edge} <- apply_mutation(operation, project, blocker, target) do
      conn
      |> put_status(if(operation == :create, do: :created, else: :ok))
      |> json(%{data: Representation.blocking_edge(edge)})
    end
  end

  defp mutate(_conn, _params, _operation), do: {:error, :invalid_request}

  defp apply_mutation(:create, project, blocker, target),
    do: Tasks.add_block(project, blocker, target)

  defp apply_mutation(:delete, project, blocker, target),
    do: Tasks.remove_block(project, blocker, target)

  defp no_query(%Plug.Conn{query_params: query}) when map_size(query) == 0, do: :ok
  defp no_query(_conn), do: {:error, :invalid_request}

  defp empty_body(%Plug.Conn{body_params: body}) when map_size(body) == 0, do: :ok
  defp empty_body(_conn), do: {:error, :invalid_request}

  defp fetch_project(raw_id) do
    with {:ok, id} <- Params.positive_id(raw_id) do
      case Projects.get_project(id) do
        nil -> {:error, :not_found}
        project -> {:ok, project}
      end
    end
  end

  defp fetch_scoped_task(project, raw_id) do
    with {:ok, id} <- Params.positive_id(raw_id) do
      case Tasks.get_task_for_project(project, id) do
        nil -> {:error, :not_found}
        task -> {:ok, task}
      end
    end
  end

  defp fetch_target(raw_id) do
    with {:ok, id} <- Params.positive_id(raw_id) do
      case Tasks.get_task(id) do
        nil -> {:error, :not_found}
        task -> {:ok, task}
      end
    end
  end
end
