defmodule TaskmanWeb.ProjectLive.Tasks.Hierarchy do
  alias Taskman.Tasks.{Hierarchy, HierarchyNode, Task}

  @enforce_keys [:hierarchy, :root_id, :expanded_node_ids, :required_node_ids]
  defstruct hierarchy: nil,
            root_id: nil,
            expanded_node_ids: MapSet.new(),
            required_node_ids: MapSet.new(),
            visible_statuses: Task.statuses(),
            filter_open?: false,
            sort: {:status, :desc},
            sort_open?: false

  @type t :: %__MODULE__{
          hierarchy: Hierarchy.t() | nil,
          root_id: pos_integer() | nil,
          expanded_node_ids: MapSet.t(pos_integer()),
          required_node_ids: MapSet.t(pos_integer()),
          visible_statuses: [Task.status()],
          filter_open?: boolean(),
          sort: {atom(), :asc | :desc},
          sort_open?: boolean()
        }

  @spec empty() :: t()
  def empty,
    do: %__MODULE__{
      hierarchy: nil,
      root_id: nil,
      expanded_node_ids: MapSet.new(),
      required_node_ids: MapSet.new()
    }

  @spec load(t(), Hierarchy.t()) :: t()
  def load(%__MODULE__{} = state, %Hierarchy{} = hierarchy) do
    root_id = hierarchy.root.task.id
    required_node_ids = hierarchy |> selected_path() |> MapSet.new()

    expanded_node_ids =
      if state.root_id == root_id do
        MapSet.union(state.expanded_node_ids, required_node_ids)
      else
        required_node_ids
      end

    %{
      state
      | hierarchy: hierarchy,
        root_id: root_id,
        expanded_node_ids: expanded_node_ids,
        required_node_ids: required_node_ids
    }
  end

  @spec filter_statuses(t(), [term()]) :: t()
  def filter_statuses(%__MODULE__{} = state, statuses) when is_list(statuses) do
    visible_statuses =
      Enum.filter(Task.statuses(), &(&1 in statuses || Atom.to_string(&1) in statuses))

    %{state | visible_statuses: visible_statuses}
  end

  @spec sort_by(t(), atom()) :: t()
  def sort_by(%__MODULE__{} = state, field) when field in [:id, :title, :status, :priority] do
    direction =
      case state.sort do
        {^field, :asc} -> :desc
        {^field, :desc} -> :asc
        _ when field in [:status, :priority] -> :desc
        _ -> :asc
      end

    %{state | sort: {field, direction}, sort_open?: false}
  end

  def sort_by(%__MODULE__{} = state, _field), do: state

  @spec visible_root(t()) :: HierarchyNode.t() | nil
  def visible_root(%__MODULE__{hierarchy: nil}), do: nil

  def visible_root(%__MODULE__{} = state) do
    project_node(state.hierarchy.root, state)
  end

  defp project_node(%HierarchyNode{} = node, state) do
    children =
      Enum.flat_map(node.children, fn child ->
        case project_node(child, state) do
          nil -> []
          visible -> [visible]
        end
      end)

    if node.task.status in state.visible_statuses || children != [] ||
         MapSet.member?(state.required_node_ids, node.task.id) do
      {field, direction} = state.sort
      children = Enum.sort_by(children, &{sort_value(&1.task, field), &1.task.id}, direction)
      %{node | children: children}
    end
  end

  defp sort_value(task, :id), do: task.id
  defp sort_value(task, :title), do: String.downcase(task.title)

  defp sort_value(task, :status) do
    Map.fetch!(
      %{will_not_do: 0, icebox: 1, pending: 2, in_progress: 3, in_review: 4, done: 5},
      task.status
    )
  end

  defp sort_value(task, :priority) do
    Map.fetch!(%{none: 0, low: 1, medium: 2, high: 3, urgent: 4}, task.priority)
  end

  @spec toggle(t(), pos_integer()) :: t()
  def toggle(%__MODULE__{} = state, task_id) when is_integer(task_id) and task_id > 0 do
    if MapSet.member?(state.required_node_ids, task_id) do
      state
    else
      expanded_node_ids =
        if MapSet.member?(state.expanded_node_ids, task_id) do
          MapSet.delete(state.expanded_node_ids, task_id)
        else
          MapSet.put(state.expanded_node_ids, task_id)
        end

      %{state | expanded_node_ids: expanded_node_ids}
    end
  end

  def toggle(%__MODULE__{} = state, _task_id), do: state

  @spec expanded?(t(), pos_integer()) :: boolean()
  def expanded?(%__MODULE__{} = state, task_id),
    do: MapSet.member?(state.expanded_node_ids, task_id)

  @spec collapsible?(t(), pos_integer()) :: boolean()
  def collapsible?(%__MODULE__{} = state, task_id),
    do: !MapSet.member?(state.required_node_ids, task_id)

  @spec selected_location_path(t()) :: [Taskman.Lists.TaskList.t()]
  def selected_location_path(%__MODULE__{
        hierarchy: %Hierarchy{root: root, selected_task_id: selected_task_id}
      }) do
    selected_location_path(root, selected_task_id) || []
  end

  def selected_location_path(%__MODULE__{}), do: []

  @spec clear(t()) :: t()
  def clear(_state), do: empty()

  defp selected_path(%Hierarchy{root: root, selected_task_id: selected_task_id}) do
    selected_path(root, selected_task_id)
  end

  defp selected_path(%HierarchyNode{task: %{id: task_id}}, task_id), do: [task_id]

  defp selected_path(%HierarchyNode{task: task, children: children}, selected_task_id) do
    case Enum.find_value(children, fn child ->
           case selected_path(child, selected_task_id) do
             [] -> nil
             path -> path
           end
         end) do
      nil -> []
      path -> [task.id | path]
    end
  end

  defp selected_location_path(
         %HierarchyNode{task: %{id: selected_task_id}, location_path: location_path},
         selected_task_id
       ),
       do: location_path

  defp selected_location_path(%HierarchyNode{children: children}, selected_task_id) do
    Enum.find_value(children, &selected_location_path(&1, selected_task_id))
  end
end
