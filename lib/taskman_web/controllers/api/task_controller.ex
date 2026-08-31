defmodule TaskmanWeb.API.TaskController do
  use TaskmanWeb, :controller

  action_fallback TaskmanWeb.API.FallbackController

  alias Taskman.Lists
  alias Taskman.Projects
  alias Taskman.Tasks
  alias Taskman.Tasks.Hierarchy
  alias Taskman.Tasks.HierarchyNode
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation
  alias TaskmanWeb.API.Params
  alias TaskmanWeb.API.Representation

  @editable_fields ~w(title description status priority due_at parent_task_id)
  @task_sort_fields %{
    "id" => :id,
    "title" => :title,
    "location" => :location,
    "status" => :status,
    "priority" => :priority
  }
  @sort_directions %{"asc" => :asc, "desc" => :desc}

  def index(conn, %{"project_id" => project_id} = params) do
    with {:ok, project} <- fetch_project(project_id),
         {:ok, location} <- resolve_location(project, Map.get(params, "list_id")),
         {:ok, task_options} <- task_query_options(params),
         {:ok, tasks} <-
           Tasks.list_tasks_for_location(project, location, task_options) do
      json(conn, %{data: Enum.map(tasks, &task_data(project, &1))})
    end
  end

  def index(_conn, _params), do: {:error, :invalid_request}

  def create(conn, %{"project_id" => project_id, "task" => task_params})
      when is_map(task_params) do
    {list_id, task_attrs} = Map.pop(task_params, "list_id")
    {parent_task_id, task_attrs, _parent_present?} = pop_parent_task_id(task_attrs)

    with {:ok, project} <- fetch_project(project_id),
         {:ok, location} <- resolve_location(project, list_id),
         {:ok, parent} <- resolve_parent(project, parent_task_id),
         {:ok, task} <- Tasks.create_task(project, location, task_attrs, parent: parent) do
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

  def hierarchy(conn, %{"project_id" => project_id, "task_id" => task_id}) do
    with {:ok, project} <- fetch_project(project_id),
         {:ok, task} <- fetch_task(project, task_id),
         {:ok, hierarchy} <- Tasks.get_task_hierarchy(project, task) do
      json(conn, %{data: hierarchy_data(hierarchy)})
    end
  end

  def hierarchy(_conn, _params), do: {:error, :invalid_request}

  def update(
        conn,
        %{"project_id" => project_id, "task_id" => task_id, "task" => task_attrs}
      )
      when is_map(task_attrs) do
    {parent_task_id, task_attrs_without_parent, parent_present?} =
      pop_parent_task_id(task_attrs)

    with :ok <- validate_update_attrs(task_attrs),
         {:ok, project} <- fetch_project(project_id),
         {:ok, task} <- fetch_task(project, task_id),
         {:ok, parent_opts} <-
           resolve_update_parent(project, parent_task_id, parent_present?),
         {:ok, updated} <-
           Tasks.update_task(project, task, task_attrs_without_parent, parent_opts) do
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

  defp resolve_parent(_project, nil), do: {:ok, nil}

  defp resolve_parent(project, parent_task_id) do
    with {:ok, id} <- Params.positive_id(parent_task_id) do
      case Tasks.get_task_for_project(project, id) do
        nil -> {:error, :not_found}
        parent -> {:ok, parent}
      end
    end
  end

  defp resolve_update_parent(_project, _parent_task_id, false), do: {:ok, []}

  defp resolve_update_parent(project, parent_task_id, true) do
    with {:ok, parent} <- resolve_parent(project, parent_task_id) do
      {:ok, [parent: parent]}
    end
  end

  defp include_descendants?(%{"include_descendants" => "true"}), do: true
  defp include_descendants?(_params), do: false

  defp task_query_options(params) do
    include_descendants? = include_descendants?(params)

    with {:ok, statuses} <- task_statuses(params),
         {:ok, sort} <- task_sort(params, include_descendants?) do
      options = [include_descendants: include_descendants?]
      options = maybe_put_task_option(options, :statuses, statuses)
      {:ok, maybe_put_task_option(options, :sort, sort)}
    end
  end

  defp task_statuses(params) do
    case Map.fetch(params, "statuses") do
      :error ->
        {:ok, nil}

      {:ok, statuses} when is_list(statuses) and statuses != [] ->
        Enum.reduce_while(statuses, {:ok, []}, fn status, {:ok, parsed} ->
          case Enum.find(Task.statuses(), &(Atom.to_string(&1) == status)) do
            nil -> {:halt, {:error, :invalid_request}}
            parsed_status -> {:cont, {:ok, [parsed_status | parsed]}}
          end
        end)
        |> case do
          {:ok, parsed} -> {:ok, Enum.reverse(parsed)}
          error -> error
        end

      {:ok, status} when is_binary(status) and status != "" ->
        task_statuses(%{"statuses" => [status]})

      {:ok, _invalid} ->
        {:error, :invalid_request}
    end
  end

  defp task_sort(params, include_descendants?) do
    case {Map.fetch(params, "sort"), Map.fetch(params, "direction")} do
      {:error, :error} ->
        {:ok, nil}

      {{:ok, field}, {:ok, direction}} when is_binary(field) and is_binary(direction) ->
        with {:ok, parsed_field} <- Map.fetch(@task_sort_fields, field),
             {:ok, parsed_direction} <- Map.fetch(@sort_directions, direction),
             true <- parsed_field != :location or include_descendants? do
          {:ok, {parsed_field, parsed_direction}}
        else
          _invalid -> {:error, :invalid_request}
        end

      _incomplete_or_invalid ->
        {:error, :invalid_request}
    end
  end

  defp maybe_put_task_option(options, _key, nil), do: options
  defp maybe_put_task_option(options, key, value), do: Keyword.put(options, key, value)

  defp destination_list_id(destination) do
    if Map.has_key?(destination, "list_id") do
      {:ok, Map.get(destination, "list_id")}
    else
      {:error, :invalid_request}
    end
  end

  defp validate_update_attrs(attrs) do
    if Enum.any?(@editable_fields, &Map.has_key?(attrs, &1)) or
         Map.has_key?(attrs, :parent_task_id) do
      :ok
    else
      {:error, :invalid_request}
    end
  end

  defp pop_parent_task_id(attrs) do
    cond do
      Map.has_key?(attrs, "parent_task_id") ->
        {parent_task_id, attrs} = Map.pop(attrs, "parent_task_id")
        {parent_task_id, attrs, true}

      Map.has_key?(attrs, :parent_task_id) ->
        {parent_task_id, attrs} = Map.pop(attrs, :parent_task_id)
        {parent_task_id, attrs, true}

      true ->
        {nil, attrs, false}
    end
  end

  defp hierarchy_data(%Hierarchy{selected_task_id: selected_task_id, root: root}) do
    %{
      selected_task_id: selected_task_id,
      root: hierarchy_node_data(root)
    }
  end

  defp hierarchy_node_data(%HierarchyNode{
         task: task,
         location_path: location_path,
         children: children
       }) do
    %{
      task:
        Representation.task_with_location(%TaskWithLocation{
          task: task,
          location_path: location_path
        }),
      children: Enum.map(children, &hierarchy_node_data/1)
    }
  end

  defp task_data(_project, %TaskWithLocation{} = task_with_location),
    do: Representation.task_with_location(task_with_location)

  defp task_data(project, task) do
    project_lists = Lists.list_lists_for_project(project)
    Representation.task(task, project_lists)
  end
end
