defmodule Taskman.Tasks do
  import Ecto.Query

  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation

  def list_tasks_for_project(%Project{id: project_id}) do
    Task
    |> where([task], task.project_id == ^project_id)
    |> order_by([task], asc: task.inserted_at, asc: task.id)
    |> Repo.all()
  end

  def get_task_for_project(%Project{id: project_id}, id) when is_integer(id) and id > 0 do
    Repo.get_by(Task, id: id, project_id: project_id)
  end

  def get_task_for_project(%Project{} = project, id) when is_binary(id) do
    case Integer.parse(id) do
      {parsed, ""} -> get_task_for_project(project, parsed)
      _invalid -> nil
    end
  end

  def get_task_for_project(%Project{}, _id), do: nil

  def create_task(%Project{id: project_id}, attrs \\ %{}) do
    create_task(%Project{id: project_id}, nil, attrs)
  end

  def create_task(%Project{id: project_id}, nil, attrs) do
    %Task{project_id: project_id}
    |> Task.changeset(attrs)
    |> Repo.insert()
  end

  def create_task(
        %Project{id: project_id},
        %TaskList{project_id: project_id, id: list_id},
        attrs
      ) do
    %Task{project_id: project_id, list_id: list_id}
    |> Task.changeset(attrs)
    |> Repo.insert()
  end

  def create_task(%Project{}, %TaskList{}, _attrs), do: {:error, :not_found}
  def create_task(%Project{}, _location, _attrs), do: {:error, :not_found}

  def list_tasks_for_location(project, location, opts \\ [])

  def list_tasks_for_location(%Project{id: project_id} = project, nil, opts) do
    if Keyword.get(opts, :include_descendants, false) do
      task_lists = Lists.list_lists_for_project(project)
      root_ids = Enum.flat_map(task_lists, &if(is_nil(&1.parent_list_id), do: [&1.id], else: []))

      project_id
      |> list_tasks_for_project_descendants(root_ids)
      |> listed_tasks(task_lists)
      |> then(&{:ok, &1})
    else
      project_id
      |> list_tasks_for_direct_project()
      |> listed_tasks([])
      |> then(&{:ok, &1})
    end
  end

  def list_tasks_for_location(
        %Project{id: project_id} = project,
        %TaskList{project_id: project_id, id: list_id},
        opts
      ) do
    task_lists = Lists.list_lists_for_project(project)

    case Enum.find(task_lists, &(&1.id == list_id)) do
      nil ->
        {:error, :not_found}

      _task_list ->
        tasks =
          if Keyword.get(opts, :include_descendants, false) do
            list_tasks_for_list_descendants(project_id, list_id)
          else
            list_tasks_for_direct_list(project_id, list_id)
          end

        {:ok, listed_tasks(tasks, task_lists)}
    end
  end

  def list_tasks_for_location(%Project{}, %TaskList{}, _opts), do: {:error, :not_found}
  def list_tasks_for_location(%Project{}, _location, _opts), do: {:error, :not_found}

  def move_task(project, task, destination)

  def move_task(
        %Project{id: project_id} = project,
        %Task{project_id: project_id} = task,
        nil
      ) do
    persist_location_change(project, task, nil)
  end

  def move_task(
        %Project{id: project_id} = project,
        %Task{project_id: project_id} = task,
        %TaskList{project_id: project_id, id: destination_id}
      ) do
    case Lists.get_list_for_project(project, destination_id) do
      nil -> {:error, :not_found}
      _destination -> persist_location_change(project, task, destination_id)
    end
  end

  def move_task(%Project{}, %Task{}, _destination), do: {:error, :not_found}

  defp persist_location_change(%Project{} = project, %Task{id: task_id}, destination_id) do
    case get_task_for_project(project, task_id) do
      nil ->
        {:error, :not_found}

      %Task{list_id: ^destination_id} ->
        {:error, :unchanged_location}

      %Task{} = persisted_task ->
        persisted_task
        |> Ecto.Changeset.change(list_id: destination_id)
        |> Ecto.Changeset.foreign_key_constraint(:list_id,
          name: :tasks_list_id_project_id_fkey
        )
        |> Repo.update()
    end
  end

  defp list_tasks_for_direct_project(project_id) do
    Task
    |> where([task], task.project_id == ^project_id and is_nil(task.list_id))
    |> order_by([task], asc: task.inserted_at, asc: task.id)
    |> Repo.all()
  end

  defp list_tasks_for_direct_list(project_id, list_id) do
    Task
    |> where([task], task.project_id == ^project_id and task.list_id == ^list_id)
    |> order_by([task], asc: task.inserted_at, asc: task.id)
    |> Repo.all()
  end

  defp list_tasks_for_project_descendants(project_id, root_ids) do
    descendant_lists = descendant_lists_query(project_id, root_ids)
    descendant_ids = from item in "descendant_lists", select: item.id

    Task
    |> recursive_ctes(true)
    |> with_cte("descendant_lists", as: ^descendant_lists)
    |> where([task], task.project_id == ^project_id)
    |> where([task], is_nil(task.list_id) or task.list_id in subquery(descendant_ids))
    |> order_by([task], asc: task.inserted_at, asc: task.id)
    |> Repo.all()
  end

  defp list_tasks_for_list_descendants(project_id, root_id) do
    descendant_lists = descendant_lists_query(project_id, [root_id])
    descendant_ids = from item in "descendant_lists", select: item.id

    Task
    |> recursive_ctes(true)
    |> with_cte("descendant_lists", as: ^descendant_lists)
    |> where([task], task.project_id == ^project_id)
    |> where([task], task.list_id in subquery(descendant_ids))
    |> order_by([task], asc: task.inserted_at, asc: task.id)
    |> Repo.all()
  end

  defp descendant_lists_query(project_id, root_ids) do
    seed =
      from task_list in TaskList,
        where:
          task_list.project_id == ^project_id and
            task_list.id in ^root_ids,
        select: %{id: task_list.id}

    recursive_member =
      from task_list in TaskList,
        join: parent in "descendant_lists",
        on: task_list.parent_list_id == parent.id,
        where: task_list.project_id == ^project_id,
        select: %{id: task_list.id}

    union_all(seed, ^recursive_member)
  end

  defp listed_tasks(tasks, task_lists) do
    paths_by_id =
      Map.new(task_lists, fn task_list ->
        {task_list.id, Lists.path_for(task_lists, task_list)}
      end)

    Enum.map(tasks, fn task ->
      %TaskWithLocation{
        task: task,
        location_path: Map.get(paths_by_id, task.list_id, [])
      }
    end)
  end

  def change_task(owner, attrs \\ %{})

  def change_task(%Project{id: project_id}, attrs) do
    %Task{project_id: project_id}
    |> Task.changeset(attrs)
  end

  def change_task(%Task{} = task, attrs) do
    Task.changeset(task, attrs)
  end

  def update_task(
        %Project{id: project_id},
        %Task{project_id: project_id} = task,
        attrs
      ) do
    task
    |> Task.changeset(attrs)
    |> Repo.update()
  end

  def update_task(%Project{}, %Task{}, _attrs), do: {:error, :not_found}
end
