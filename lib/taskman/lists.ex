defmodule Taskman.Lists do
  import Ecto.Query

  alias Taskman.ChangeNotifications
  alias Taskman.Lists.NavigationNode
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Repo

  @type selected_location :: nil | {:project, pos_integer()} | {:list, pos_integer()}

  def list_lists_for_project(%Project{id: project_id}) do
    TaskList
    |> where([task_list], task_list.project_id == ^project_id)
    |> order_by([task_list], asc: task_list.inserted_at, asc: task_list.id)
    |> Repo.all()
  end

  @doc "Flatten Lists in parent-before-descendant order while preserving sibling order."
  @spec tree_order([TaskList.t()]) :: [TaskList.t()]
  def tree_order(task_lists) when is_list(task_lists) do
    children_by_parent = Enum.group_by(task_lists, & &1.parent_list_id)

    {visited, reversed} =
      flatten_task_lists(
        Map.get(children_by_parent, nil, []),
        children_by_parent,
        MapSet.new(),
        []
      )

    remaining = Enum.reject(task_lists, &MapSet.member?(visited, &1.id))
    {_visited, reversed} = flatten_task_lists(remaining, children_by_parent, visited, reversed)

    Enum.reverse(reversed)
  end

  def get_list_for_project(%Project{id: project_id}, id) when is_integer(id) and id > 0 do
    Repo.get_by(TaskList, id: id, project_id: project_id)
  end

  def get_list_for_project(%Project{} = project, id) when is_binary(id) do
    case Integer.parse(id) do
      {parsed, ""} -> get_list_for_project(project, parsed)
      _invalid -> nil
    end
  end

  def get_list_for_project(%Project{}, _id), do: nil

  def create_list(%Project{id: project_id}, nil, attrs) do
    changeset = TaskList.changeset(%TaskList{project_id: project_id}, attrs)
    changed_fields = Map.keys(changeset.changes)

    case Repo.insert(changeset) do
      {:ok, task_list} = result ->
        fields = changed_fields ++ [:project_id, :parent_list_id]
        _ = ChangeNotifications.publish_list(task_list, :created, fields)
        result

      error ->
        error
    end
  end

  def create_list(
        %Project{id: project_id},
        %TaskList{project_id: project_id, id: parent_id},
        attrs
      ) do
    changeset =
      TaskList.changeset(
        %TaskList{project_id: project_id, parent_list_id: parent_id},
        attrs
      )

    changed_fields = Map.keys(changeset.changes)

    case Repo.insert(changeset) do
      {:ok, task_list} = result ->
        fields = changed_fields ++ [:project_id, :parent_list_id]
        _ = ChangeNotifications.publish_list(task_list, :created, fields)
        result

      error ->
        error
    end
  end

  def create_list(%Project{}, %TaskList{}, _attrs), do: {:error, :not_found}

  def change_list(%TaskList{} = task_list, attrs \\ %{}) do
    TaskList.changeset(task_list, attrs)
  end

  def rename_list(
        %Project{id: project_id},
        %TaskList{project_id: project_id} = task_list,
        attrs
      ) do
    changeset = TaskList.changeset(task_list, attrs)
    changed_fields = Map.keys(changeset.changes)

    case Repo.update(changeset) do
      {:ok, renamed} = result ->
        if changed_fields != [] do
          _ = ChangeNotifications.publish_list(renamed, :updated, changed_fields)
        end

        result

      error ->
        error
    end
  end

  def rename_list(%Project{}, %TaskList{}, _attrs), do: {:error, :not_found}

  def path_for(lists, nil) when is_list(lists), do: []

  def path_for(lists, %TaskList{} = task_list) when is_list(lists) do
    by_id = Map.new(lists, &{&1.id, &1})
    do_path_for(by_id, task_list, MapSet.new())
  end

  defp do_path_for(by_id, %TaskList{id: id}, visited) when is_integer(id) do
    if MapSet.member?(visited, id) do
      []
    else
      visited = MapSet.put(visited, id)

      case Map.get(by_id, id) do
        nil ->
          []

        %TaskList{parent_list_id: nil} = current ->
          [current]

        %TaskList{parent_list_id: parent_id} = current ->
          do_path_for(by_id, Map.get(by_id, parent_id, %TaskList{id: parent_id}), visited)
          |> Kernel.++([current])
      end
    end
  end

  defp do_path_for(_by_id, _task_list, _visited), do: []

  defp flatten_task_lists([], _children_by_parent, visited, reversed),
    do: {visited, reversed}

  defp flatten_task_lists([task_list | rest], children_by_parent, visited, reversed) do
    if MapSet.member?(visited, task_list.id) do
      flatten_task_lists(rest, children_by_parent, visited, reversed)
    else
      visited = MapSet.put(visited, task_list.id)

      {visited, reversed} =
        flatten_task_lists(
          Map.get(children_by_parent, task_list.id, []),
          children_by_parent,
          visited,
          [task_list | reversed]
        )

      flatten_task_lists(rest, children_by_parent, visited, reversed)
    end
  end

  def navigation_nodes(
        projects,
        lists_by_project,
        selected_location,
        expanded_node_ids
      )
      when is_list(projects) and is_map(lists_by_project) do
    Enum.flat_map(projects, fn project ->
      task_lists = Map.get(lists_by_project, project.id, [])
      children_by_parent = Enum.group_by(task_lists, & &1.parent_list_id)
      forced_open_ids = forced_open_list_ids(task_lists, selected_location)

      selected_list_in_project? =
        match?({:list, _}, selected_location) and
          Enum.any?(task_lists, &(&1.id == elem(selected_location, 1)))

      project_selected? = selected_location == {:project, project.id}
      project_expandable? = task_lists != []

      project_node = %NavigationNode{
        dom_id: "project-#{project.id}",
        kind: :project,
        depth: 1,
        project: project,
        task_list: nil,
        expanded?:
          project_expandable? &&
            (MapSet.member?(expanded_node_ids, {:project, project.id}) or
               selected_list_in_project? or
               forced_open_ids != MapSet.new()),
        expandable?: project_expandable?,
        selected?: project_selected?
      }

      if project_node.expanded? do
        [
          project_node
          | flatten_children(
              children_by_parent,
              project,
              nil,
              2,
              selected_location,
              expanded_node_ids,
              forced_open_ids
            )
        ]
      else
        [project_node]
      end
    end)
  end

  defp forced_open_list_ids(task_lists, {:list, selected_id}) do
    by_id = Map.new(task_lists, &{&1.id, &1})
    collect_ancestor_ids(by_id, selected_id, MapSet.new())
  end

  defp forced_open_list_ids(_task_lists, _selected_location), do: MapSet.new()

  defp collect_ancestor_ids(by_id, id, seen) do
    if MapSet.member?(seen, id) do
      MapSet.new()
    else
      case Map.get(by_id, id) do
        nil ->
          MapSet.new()

        %TaskList{parent_list_id: nil} ->
          MapSet.new()

        %TaskList{parent_list_id: parent_id} ->
          seen = MapSet.put(seen, id)
          MapSet.put(collect_ancestor_ids(by_id, parent_id, seen), parent_id)
      end
    end
  end

  defp flatten_children(
         children_by_parent,
         project,
         parent_id,
         depth,
         selected_location,
         expanded_node_ids,
         forced_open_ids
       ) do
    children_by_parent
    |> Map.get(parent_id, [])
    |> Enum.flat_map(fn task_list ->
      child_lists = Map.get(children_by_parent, task_list.id, [])
      expandable? = child_lists != []
      selected? = selected_location == {:list, task_list.id}
      forced_open? = MapSet.member?(forced_open_ids, task_list.id)

      expanded? =
        expandable? &&
          (forced_open? or MapSet.member?(expanded_node_ids, {:list, task_list.id}))

      node = %NavigationNode{
        dom_id: "list-#{task_list.id}",
        kind: :list,
        depth: depth,
        project: project,
        task_list: task_list,
        expanded?: expanded?,
        expandable?: expandable?,
        selected?: selected?
      }

      if expanded? do
        [
          node
          | flatten_children(
              children_by_parent,
              project,
              task_list.id,
              depth + 1,
              selected_location,
              expanded_node_ids,
              forced_open_ids
            )
        ]
      else
        [node]
      end
    end)
  end
end
