defmodule Taskman.Tasks do
  import Ecto.Query

  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.Hierarchy
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

  @spec search_parent_candidates(Project.t(), Task.t() | nil, String.t(), keyword()) ::
          [TaskWithLocation.t()]
  def search_parent_candidates(project, current_task, query, opts \\ []) do
    Hierarchy.search_parent_candidates(project, current_task, query, opts)
  end

  @spec get_task_hierarchy(Project.t(), Task.t()) ::
          {:ok, Hierarchy.t()} | {:error, :not_found}
  def get_task_hierarchy(project, task) do
    Hierarchy.get_task_hierarchy(project, task)
  end

  def create_task(%Project{id: project_id}, attrs \\ %{}) do
    create_task(%Project{id: project_id}, nil, attrs)
  end

  def create_task(%Project{} = project, location, attrs) do
    create_task(project, location, attrs, [])
  end

  def create_task(
        %Project{id: project_id} = project,
        nil,
        attrs,
        opts
      ) do
    create_task_with_parent(project, %Task{project_id: project_id}, attrs, opts)
  end

  def create_task(
        %Project{id: project_id} = project,
        %TaskList{project_id: project_id, id: list_id},
        attrs,
        opts
      ) do
    create_task_with_parent(project, %Task{project_id: project_id, list_id: list_id}, attrs, opts)
  end

  def create_task(%Project{}, %TaskList{}, _attrs, _opts), do: {:error, :not_found}
  def create_task(%Project{}, _location, _attrs, _opts), do: {:error, :not_found}

  def list_tasks_for_location(project, location, opts \\ [])

  def list_tasks_for_location(%Project{id: project_id} = project, nil, opts) do
    if Keyword.get(opts, :include_descendants, false) do
      task_lists = Lists.list_lists_for_project(project)
      root_ids = Enum.flat_map(task_lists, &if(is_nil(&1.parent_list_id), do: [&1.id], else: []))

      project_id
      |> list_tasks_for_project_descendants(root_ids, opts)
      |> listed_tasks(task_lists)
      |> apply_task_options(opts)
      |> then(&{:ok, &1})
    else
      project_id
      |> list_tasks_for_direct_project(opts)
      |> listed_tasks([])
      |> apply_task_options(opts)
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
            list_tasks_for_list_descendants(project_id, list_id, opts)
          else
            list_tasks_for_direct_list(project_id, list_id, opts)
          end

        {:ok, tasks |> listed_tasks(task_lists) |> apply_task_options(opts)}
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

  defp list_tasks_for_direct_project(project_id, opts) do
    Task
    |> where([task], task.project_id == ^project_id and is_nil(task.list_id))
    |> apply_task_query_options(opts)
    |> Repo.all()
  end

  defp list_tasks_for_direct_list(project_id, list_id, opts) do
    Task
    |> where([task], task.project_id == ^project_id and task.list_id == ^list_id)
    |> apply_task_query_options(opts)
    |> Repo.all()
  end

  defp list_tasks_for_project_descendants(project_id, root_ids, opts) do
    descendant_lists = descendant_lists_query(project_id, root_ids)
    descendant_ids = from item in "descendant_lists", select: item.id

    Task
    |> recursive_ctes(true)
    |> with_cte("descendant_lists", as: ^descendant_lists)
    |> where([task], task.project_id == ^project_id)
    |> where([task], is_nil(task.list_id) or task.list_id in subquery(descendant_ids))
    |> apply_task_query_options(opts)
    |> Repo.all()
  end

  defp list_tasks_for_list_descendants(project_id, root_id, opts) do
    descendant_lists = descendant_lists_query(project_id, [root_id])
    descendant_ids = from item in "descendant_lists", select: item.id

    Task
    |> recursive_ctes(true)
    |> with_cte("descendant_lists", as: ^descendant_lists)
    |> where([task], task.project_id == ^project_id)
    |> where([task], task.list_id in subquery(descendant_ids))
    |> apply_task_query_options(opts)
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

  defp apply_task_query_options(query, opts) do
    statuses = opts |> Keyword.get(:statuses, Task.statuses()) |> normalize_statuses()

    query
    |> apply_status_filter(statuses)
    |> apply_task_order(Keyword.get(opts, :sort))
  end

  defp normalize_statuses(statuses) when is_list(statuses) do
    Enum.filter(statuses, &(is_atom(&1) and &1 in Task.statuses()))
  end

  defp normalize_statuses(statuses), do: statuses

  defp apply_status_filter(query, []), do: where(query, false)

  defp apply_status_filter(query, statuses) do
    where(query, [task], task.status in ^statuses)
  end

  defp apply_task_order(query, nil), do: default_task_order(query)
  defp apply_task_order(query, {:location, _direction}), do: default_task_order(query)

  defp apply_task_order(query, {field, direction})
       when field in [:id, :title, :location, :status, :priority] and
              direction in [:asc, :desc] do
    case field do
      :id ->
        order_by(query, [task], [{^direction, task.id}])

      :title ->
        order_by(query, [task], [
          {^direction, fragment("lower(?)", task.title)},
          {^direction, task.id}
        ])

      :status ->
        order_by(query, [task], [
          {^direction,
           fragment(
             "CASE ? WHEN 'will_not_do' THEN 0 WHEN 'icebox' THEN 1 WHEN 'pending' THEN 2 WHEN 'in_progress' THEN 3 WHEN 'in_review' THEN 4 WHEN 'done' THEN 5 END",
             task.status
           )},
          {^direction, task.id}
        ])

      :priority ->
        order_by(query, [task], [
          {^direction,
           fragment(
             "CASE ? WHEN 'none' THEN 0 WHEN 'low' THEN 1 WHEN 'medium' THEN 2 WHEN 'high' THEN 3 WHEN 'urgent' THEN 4 END",
             task.priority
           )},
          {^direction, task.id}
        ])
    end
  end

  defp default_task_order(query) do
    order_by(query, [task], asc: task.inserted_at, asc: task.id)
  end

  defp apply_task_options(tasks, opts) do
    sort_listed_tasks(tasks, Keyword.get(opts, :sort))
  end

  defp sort_listed_tasks(tasks, nil), do: tasks

  defp sort_listed_tasks(tasks, {:location, direction}) when direction in [:asc, :desc] do
    Enum.sort_by(tasks, &{task_sort_value(&1, :location), &1.task.id}, direction)
  end

  defp sort_listed_tasks(tasks, {field, direction})
       when field in [:id, :title, :status, :priority] and direction in [:asc, :desc],
       do: tasks

  defp task_sort_value(%TaskWithLocation{location_path: location_path}, :location) do
    Enum.map_join(location_path, " / ", &String.downcase(&1.name))
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
        %Project{id: project_id} = project,
        %Task{project_id: project_id} = task,
        attrs
      ) do
    update_task(project, task, attrs, [])
  end

  def update_task(%Project{}, %Task{}, _attrs), do: {:error, :not_found}

  def update_task(
        %Project{id: project_id} = project,
        %Task{project_id: project_id} = task,
        attrs,
        opts
      ) do
    if Keyword.has_key?(opts, :parent) do
      update_task_with_parent(project, task, attrs, Keyword.get(opts, :parent))
    else
      task
      |> Task.changeset(attrs)
      |> Repo.update()
    end
  end

  def update_task(%Project{}, %Task{}, _attrs, _opts), do: {:error, :not_found}

  defp create_task_with_parent(project, task, attrs, opts) do
    if Keyword.has_key?(opts, :parent) do
      persist_parent_mutation(project, task, attrs, Keyword.get(opts, :parent), :create)
    else
      task
      |> Task.changeset(attrs)
      |> Repo.insert()
    end
  end

  defp update_task_with_parent(project, task, attrs, parent) do
    persist_parent_mutation(project, task, attrs, parent, :update)
  end

  defp persist_parent_mutation(project, task, attrs, parent, operation) do
    Repo.transaction(fn ->
      with %Project{} = locked_project <- lock_project(project),
           {:ok, persisted_task} <- reload_task(locked_project, task, operation),
           {:ok, persisted_parent} <- reload_parent(locked_project, parent),
           :ok <- Hierarchy.validate_parent(persisted_task, persisted_parent, locked_project) do
        changeset = Task.changeset(persisted_task, attrs)

        changeset
        |> Ecto.Changeset.put_change(:parent_task_id, persisted_parent && persisted_parent.id)
        |> persist_parent_mutation(operation)
      else
        nil ->
          Repo.rollback(:not_found)

        {:error, :not_found} ->
          Repo.rollback(:not_found)

        {:error, :cycle} ->
          changeset = Task.changeset(task, attrs)

          changeset
          |> Ecto.Changeset.add_error(:parent_task_id, "would create a cycle")
          |> Repo.rollback()
      end
    end)
  end

  defp lock_project(%Project{id: project_id}) do
    Project
    |> where([project], project.id == ^project_id)
    |> lock("FOR UPDATE")
    |> Repo.one()
  end

  defp reload_task(_project, %Task{} = task, :create), do: {:ok, task}

  defp reload_task(%Project{} = project, %Task{id: task_id}, :update) do
    case get_task_for_project(project, task_id) do
      nil -> {:error, :not_found}
      task -> {:ok, task}
    end
  end

  defp reload_parent(_project, nil), do: {:ok, nil}

  defp reload_parent(%Project{} = project, %Task{id: parent_id}) when is_integer(parent_id) do
    case get_task_for_project(project, parent_id) do
      nil -> {:error, :not_found}
      parent -> {:ok, parent}
    end
  end

  defp reload_parent(%Project{}, _parent), do: {:error, :not_found}

  defp persist_parent_mutation(changeset, :create) do
    case Repo.insert(changeset) do
      {:ok, task} -> task
      {:error, changeset} -> Repo.rollback(changeset)
    end
  end

  defp persist_parent_mutation(changeset, :update) do
    case Repo.update(changeset) do
      {:ok, task} -> task
      {:error, changeset} -> Repo.rollback(changeset)
    end
  end
end
