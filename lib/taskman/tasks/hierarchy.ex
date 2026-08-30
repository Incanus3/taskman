defmodule Taskman.Tasks.Hierarchy do
  import Ecto.Query

  alias Taskman.Lists
  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.HierarchyNode
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation

  @enforce_keys [:selected_task_id, :root]
  defstruct [:selected_task_id, :root]

  @type t :: %__MODULE__{
          selected_task_id: pos_integer(),
          root: HierarchyNode.t()
        }

  @spec validate_parent(Task.t(), Task.t() | nil, Project.t()) :: :ok | {:error, :cycle}
  def validate_parent(%Task{}, nil, %Project{}), do: :ok

  def validate_parent(%Task{id: nil}, %Task{}, %Project{}), do: :ok

  def validate_parent(
        %Task{id: task_id},
        %Task{id: parent_id},
        %Project{id: project_id}
      )
      when is_integer(task_id) and is_integer(parent_id) and is_integer(project_id) do
    if task_id == parent_id or ancestor_chain_contains_task?(parent_id, task_id, project_id) do
      {:error, :cycle}
    else
      :ok
    end
  end

  @spec search_parent_candidates(
          Project.t(),
          Task.t() | nil,
          String.t(),
          keyword()
        ) :: [TaskWithLocation.t()]
  def search_parent_candidates(
        %Project{id: project_id} = project,
        current_task,
        query,
        opts \\ []
      )
      when is_binary(query) do
    query = String.trim(query)
    limit = normalize_limit(Keyword.get(opts, :limit, 20))

    eligible_query =
      Task
      |> where([task], task.project_id == ^project_id)
      |> exclude_current_task(project_id, current_task)

    {exact_task, exact_id} =
      if limit > 0 do
        exact_candidate(eligible_query, query)
      else
        {nil, nil}
      end

    title_limit = max(limit - if(is_nil(exact_task), do: 0, else: 1), 0)

    title_tasks =
      if title_limit == 0 do
        []
      else
        eligible_query
        |> where([task], ilike(task.title, ^"%#{query}%"))
        |> maybe_exclude_exact(exact_id)
        |> order_by([task], asc: task.inserted_at, asc: task.id)
        |> limit(^title_limit)
        |> Repo.all()
      end

    tasks = if exact_task, do: [exact_task | title_tasks], else: title_tasks
    with_location_paths(project, tasks)
  end

  @spec get_task_hierarchy(Project.t(), Task.t()) ::
          {:ok, t()} | {:error, :not_found}
  def get_task_hierarchy(%Project{id: project_id} = project, %Task{id: task_id})
      when is_integer(task_id) do
    {:ok, result} =
      Repo.transaction(fn ->
        case lock_project_for_hierarchy(project_id) do
          nil -> {:error, :not_found}
          %Project{} -> project_hierarchy(project, task_id)
        end
      end)

    result
  end

  def get_task_hierarchy(%Project{}, _task), do: {:error, :not_found}

  defp project_hierarchy(project, task_id) do
    case Repo.get_by(Task, id: task_id, project_id: project.id) do
      nil ->
        {:error, :not_found}

      %Task{} = selected_task ->
        root_id = topmost_root_id(project.id, selected_task.id) || selected_task.id
        tasks = connected_tasks(project.id, root_id)
        lists = Lists.list_lists_for_project(project)
        paths_by_id = list_paths(lists)
        children_by_parent = Enum.group_by(tasks, & &1.parent_task_id)
        root_task = Enum.find(tasks, &(&1.id == root_id)) || selected_task

        root = build_node(root_task, children_by_parent, paths_by_id)
        {:ok, %__MODULE__{selected_task_id: selected_task.id, root: root}}
    end
  end

  defp lock_project_for_hierarchy(project_id) do
    Project
    |> where([project], project.id == ^project_id)
    |> lock("FOR SHARE")
    |> Repo.one()
  end

  defp exact_candidate(eligible_query, query) do
    case positive_integer(query) do
      nil ->
        {nil, nil}

      exact_id ->
        exact_task =
          eligible_query
          |> where([task], task.id == ^exact_id)
          |> limit(1)
          |> Repo.one()

        {exact_task, exact_id}
    end
  end

  defp positive_integer(query) do
    case Integer.parse(query) do
      {id, ""} when id > 0 -> id
      _ -> nil
    end
  end

  defp normalize_limit(limit) when is_integer(limit) and limit >= 0, do: limit
  defp normalize_limit(_limit), do: 20

  defp exclude_current_task(query, project_id, %Task{id: current_id})
       when is_integer(current_id) do
    descendants = descendant_ids_query(project_id, current_id)

    query
    |> recursive_ctes(true)
    |> with_cte("task_parent_descendants", as: ^descendants)
    |> where(
      [task],
      task.id not in subquery(from descendant in "task_parent_descendants", select: descendant.id)
    )
  end

  defp exclude_current_task(query, _project_id, _current_task), do: query

  defp descendant_ids_query(project_id, current_id) do
    seed =
      from task in Task,
        where: task.id == ^current_id and task.project_id == ^project_id,
        select: %{id: task.id}

    recursive_member =
      from task in Task,
        join: descendant in "task_parent_descendants",
        on: task.parent_task_id == field(descendant, :id),
        where: task.project_id == ^project_id,
        select: %{id: task.id}

    union_all(seed, ^recursive_member)
  end

  defp maybe_exclude_exact(query, nil), do: query

  defp maybe_exclude_exact(query, exact_id) do
    where(query, [task], task.id != ^exact_id)
  end

  defp with_location_paths(project, tasks) do
    paths_by_id = project |> Lists.list_lists_for_project() |> list_paths()

    Enum.map(tasks, fn task ->
      %TaskWithLocation{
        task: task,
        location_path: Map.get(paths_by_id, task.list_id, [])
      }
    end)
  end

  defp topmost_root_id(project_id, selected_id) do
    ancestors = ancestor_chain_query(project_id, selected_id)

    "task_parent_ancestors"
    |> recursive_ctes(true)
    |> with_cte("task_parent_ancestors", as: ^ancestors)
    |> where([ancestor], is_nil(field(ancestor, :parent_task_id)))
    |> select([ancestor], field(ancestor, :id))
    |> limit(1)
    |> Repo.one()
  end

  defp ancestor_chain_query(project_id, selected_id) do
    seed =
      from task in Task,
        where: task.id == ^selected_id and task.project_id == ^project_id,
        select: %{id: task.id, parent_task_id: task.parent_task_id}

    recursive_member =
      from task in Task,
        join: ancestor in "task_parent_ancestors",
        on: task.id == field(ancestor, :parent_task_id),
        where: task.project_id == ^project_id,
        select: %{id: task.id, parent_task_id: task.parent_task_id}

    union_all(seed, ^recursive_member)
  end

  defp connected_tasks(project_id, root_id) do
    descendants = descendant_tree_query(project_id, root_id)
    descendant_ids = from descendant in "task_parent_descendants", select: descendant.id

    Task
    |> recursive_ctes(true)
    |> with_cte("task_parent_descendants", as: ^descendants)
    |> where([task], task.project_id == ^project_id)
    |> where([task], task.id in subquery(descendant_ids))
    |> order_by([task], asc: task.inserted_at, asc: task.id)
    |> Repo.all()
  end

  defp descendant_tree_query(project_id, root_id) do
    seed =
      from task in Task,
        where: task.id == ^root_id and task.project_id == ^project_id,
        select: %{id: task.id}

    recursive_member =
      from task in Task,
        join: descendant in "task_parent_descendants",
        on: task.parent_task_id == field(descendant, :id),
        where: task.project_id == ^project_id,
        select: %{id: task.id}

    union_all(seed, ^recursive_member)
  end

  defp list_paths(lists) do
    Map.new(lists, fn task_list ->
      {task_list.id, Lists.path_for(lists, task_list)}
    end)
  end

  defp build_node(task, children_by_parent, paths_by_id) do
    children =
      children_by_parent
      |> Map.get(task.id, [])
      |> Enum.map(&build_node(&1, children_by_parent, paths_by_id))

    %HierarchyNode{
      task: task,
      location_path: Map.get(paths_by_id, task.list_id, []),
      children: children
    }
  end

  defp ancestor_chain_contains_task?(parent_id, task_id, project_id) do
    seed =
      from task in Task,
        where: task.id == ^parent_id and task.project_id == ^project_id,
        select: %{id: task.id, parent_task_id: task.parent_task_id}

    recursive_member =
      from task in Task,
        join: ancestor in "task_parent_ancestors",
        on: task.id == field(ancestor, :parent_task_id),
        where: task.project_id == ^project_id and field(ancestor, :id) != ^task_id,
        select: %{id: task.id, parent_task_id: task.parent_task_id}

    ancestor_chain = union_all(seed, ^recursive_member)

    "task_parent_ancestors"
    |> recursive_ctes(true)
    |> with_cte("task_parent_ancestors", as: ^ancestor_chain)
    |> where([ancestor], field(ancestor, :id) == ^task_id)
    |> select([_ancestor], 1)
    |> limit(1)
    |> Repo.exists?()
  end
end
