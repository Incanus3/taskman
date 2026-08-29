defmodule TaskmanWeb.API.ListController do
  use TaskmanWeb, :controller

  action_fallback TaskmanWeb.API.FallbackController

  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects
  alias TaskmanWeb.API.Params
  alias TaskmanWeb.API.Representation

  def index(conn, %{"project_id" => project_id}) do
    with {:ok, project} <- fetch_project(project_id) do
      project_lists = Lists.list_lists_for_project(project)
      tree_ordered_lists = Lists.tree_order(project_lists)

      json(conn, %{
        data: Enum.map(tree_ordered_lists, &Representation.task_list(&1, project_lists))
      })
    end
  end

  def index(_conn, _params), do: {:error, :invalid_request}

  def create(conn, %{"project_id" => project_id, "list" => attrs}) when is_map(attrs) do
    with {:ok, project} <- fetch_project(project_id),
         {:ok, parent} <- resolve_parent(project, Map.get(attrs, "parent_list_id")),
         {:ok, task_list} <- Lists.create_list(project, parent, attrs) do
      project_lists = Lists.list_lists_for_project(project)

      conn
      |> put_status(:created)
      |> json(%{data: Representation.task_list(task_list, project_lists)})
    end
  end

  def create(_conn, _params), do: {:error, :invalid_request}

  def show(conn, %{"project_id" => project_id, "list_id" => list_id}) do
    with {:ok, project} <- fetch_project(project_id),
         {:ok, task_list} <- fetch_list(project, list_id) do
      project_lists = Lists.list_lists_for_project(project)

      json(conn, %{data: Representation.task_list(task_list, project_lists)})
    end
  end

  def show(_conn, _params), do: {:error, :invalid_request}

  def update(
        conn,
        %{"project_id" => project_id, "list_id" => list_id, "list" => attrs}
      )
      when is_map(attrs) do
    with {:ok, project} <- fetch_project(project_id),
         {:ok, task_list} <- fetch_list(project, list_id),
         {:ok, renamed} <- Lists.rename_list(project, task_list, attrs) do
      project_lists = Lists.list_lists_for_project(project)

      json(conn, %{data: Representation.task_list(renamed, project_lists)})
    end
  end

  def update(_conn, _params), do: {:error, :invalid_request}

  defp fetch_project(project_id) do
    with {:ok, id} <- Params.positive_id(project_id) do
      case Projects.get_project(id) do
        nil -> {:error, :not_found}
        project -> {:ok, project}
      end
    end
  end

  defp fetch_list(project, list_id) do
    with {:ok, id} <- Params.positive_id(list_id) do
      case Lists.get_list_for_project(project, id) do
        nil -> {:error, :not_found}
        %TaskList{} = task_list -> {:ok, task_list}
      end
    end
  end

  defp resolve_parent(_project, nil), do: {:ok, nil}

  defp resolve_parent(project, parent_id) do
    with {:ok, id} <- Params.positive_id(parent_id),
         %TaskList{} = parent <- Lists.get_list_for_project(project, id) do
      {:ok, parent}
    else
      nil -> {:error, :not_found}
      {:error, _reason} = error -> error
    end
  end
end
