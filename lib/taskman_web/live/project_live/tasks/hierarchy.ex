defmodule TaskmanWeb.ProjectLive.Tasks.Hierarchy do
  alias Taskman.Tasks.{Hierarchy, HierarchyNode}

  @enforce_keys [:hierarchy, :root_id, :expanded_node_ids, :required_node_ids]
  defstruct hierarchy: nil,
            root_id: nil,
            expanded_node_ids: MapSet.new(),
            required_node_ids: MapSet.new()

  @type t :: %__MODULE__{
          hierarchy: Hierarchy.t() | nil,
          root_id: pos_integer() | nil,
          expanded_node_ids: MapSet.t(pos_integer()),
          required_node_ids: MapSet.t(pos_integer())
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

    %__MODULE__{
      hierarchy: hierarchy,
      root_id: root_id,
      expanded_node_ids: expanded_node_ids,
      required_node_ids: required_node_ids
    }
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
end
