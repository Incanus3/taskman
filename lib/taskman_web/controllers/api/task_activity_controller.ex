defmodule TaskmanWeb.API.TaskActivityController do
  use TaskmanWeb, :controller

  action_fallback TaskmanWeb.API.FallbackController

  alias Taskman.Projects
  alias Taskman.Tasks
  alias TaskmanWeb.API.Params
  alias TaskmanWeb.API.Representation

  def index(conn, %{"project_id" => project_id, "task_id" => task_id}) do
    conn = fetch_query_params(conn)

    with {:ok, include_comments?} <- comments_option(conn.query_params),
         {:ok, project} <- fetch_project(project_id),
         {:ok, task} <- fetch_task(project, task_id),
         {:ok, entries} <- Tasks.list_activity(project, task, include_comments: include_comments?) do
      json(conn, %{data: Enum.map(entries, &Representation.activity/1)})
    end
  end

  def index(_conn, _params), do: {:error, :invalid_request}

  defp comments_option(%{} = query) when map_size(query) == 0, do: {:ok, false}

  defp comments_option(%{"include_comments" => value} = query)
       when map_size(query) == 1 and value in ["true", "false"],
       do: {:ok, value == "true"}

  defp comments_option(_query), do: {:error, :invalid_request}

  defp fetch_project(project_id) do
    with {:ok, id} <- Params.positive_id(project_id) do
      case Projects.get_project(id) do
        nil -> {:error, :not_found}
        project -> {:ok, project}
      end
    end
  end

  defp fetch_task(project, task_id) do
    with {:ok, id} <- Params.positive_id(task_id) do
      case Tasks.get_task_for_project(project, id) do
        nil -> {:error, :not_found}
        task -> {:ok, task}
      end
    end
  end
end
