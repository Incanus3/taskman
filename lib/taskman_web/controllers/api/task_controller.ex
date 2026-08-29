defmodule TaskmanWeb.API.TaskController do
  use TaskmanWeb, :controller

  action_fallback TaskmanWeb.API.FallbackController

  alias Taskman.Lists
  alias Taskman.Projects
  alias Taskman.Tasks
  alias Taskman.Tasks.TaskWithLocation
  alias TaskmanWeb.API.Params
  alias TaskmanWeb.API.Representation

  @editable_fields ~w(title description status priority due_at)

  def index(conn, %{"project_id" => project_id} = params) do
    with {:ok, project} <- fetch_project(project_id),
         {:ok, location} <- resolve_location(project, Map.get(params, "list_id")),
         {:ok, tasks} <-
           Tasks.list_tasks_for_location(project, location,
             include_descendants: include_descendants?(params)
           ) do
      json(conn, %{data: Enum.map(tasks, &task_data(project, &1))})
    end
  end

  def index(_conn, _params), do: {:error, :invalid_request}

  def create(conn, %{"project_id" => project_id, "task" => task_params})
      when is_map(task_params) do
    {list_id, task_attrs} = Map.pop(task_params, "list_id")

    with {:ok, project} <- fetch_project(project_id),
         {:ok, location} <- resolve_location(project, list_id),
         {:ok, task} <- Tasks.create_task(project, location, task_attrs) do
      conn
      |> put_status(:created)
      |> json(%{data: task_data(project, task)})
    end
  end

  def create(_conn, _params), do: {:error, :invalid_request}

  def show(conn, %{"project_id" => project_id, "task_id" => task_id}) do
    with {:ok, project} <- fetch_project(project_id),
         {:ok, task} <- fetch_task(project, task_id) do
      json(conn, %{data: task_data(project, task)})
    end
  end

  def show(_conn, _params), do: {:error, :invalid_request}

  def update(
        conn,
        %{"project_id" => project_id, "task_id" => task_id, "task" => task_attrs}
      )
      when is_map(task_attrs) do
    with :ok <- validate_update_attrs(task_attrs),
         {:ok, project} <- fetch_project(project_id),
         {:ok, task} <- fetch_task(project, task_id),
         {:ok, updated} <- Tasks.update_task(project, task, task_attrs) do
      json(conn, %{data: task_data(project, updated)})
    end
  end

  def update(_conn, _params), do: {:error, :invalid_request}

  def move(
        conn,
        %{
          "project_id" => project_id,
          "task_id" => task_id,
          "destination" => destination
        }
      )
      when is_map(destination) do
    with {:ok, list_id} <- destination_list_id(destination),
         {:ok, project} <- fetch_project(project_id),
         {:ok, task} <- fetch_task(project, task_id),
         {:ok, location} <- resolve_location(project, list_id),
         {:ok, moved} <- Tasks.move_task(project, task, location) do
      json(conn, %{data: task_data(project, moved)})
    end
  end

  def move(_conn, _params), do: {:error, :invalid_request}

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

  defp resolve_location(_project, nil), do: {:ok, nil}

  defp resolve_location(project, list_id) do
    with {:ok, id} <- Params.positive_id(list_id) do
      case Lists.get_list_for_project(project, id) do
        nil -> {:error, :not_found}
        task_list -> {:ok, task_list}
      end
    end
  end

  defp include_descendants?(%{"include_descendants" => "true"}), do: true
  defp include_descendants?(_params), do: false

  defp destination_list_id(destination) do
    if Map.has_key?(destination, "list_id") do
      {:ok, Map.get(destination, "list_id")}
    else
      {:error, :invalid_request}
    end
  end

  defp validate_update_attrs(attrs) do
    if Enum.any?(@editable_fields, &Map.has_key?(attrs, &1)) do
      :ok
    else
      {:error, :invalid_request}
    end
  end

  defp task_data(_project, %TaskWithLocation{} = task_with_location),
    do: Representation.task_with_location(task_with_location)

  defp task_data(project, task) do
    project_lists = Lists.list_lists_for_project(project)
    Representation.task(task, project_lists)
  end
end
