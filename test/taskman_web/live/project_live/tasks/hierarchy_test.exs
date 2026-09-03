defmodule TaskmanWeb.ProjectLive.Tasks.HierarchyTest do
  use ExUnit.Case, async: true

  alias Taskman.Tasks.{Hierarchy, HierarchyNode, Task}
  alias TaskmanWeb.ProjectLive.Tasks.Hierarchy, as: TaskHierarchy

  test "loading a hierarchy expands the selected Task and its ancestors" do
    hierarchy = hierarchy(3)

    state = TaskHierarchy.load(TaskHierarchy.empty(), hierarchy)

    assert TaskHierarchy.expanded?(state, 1)
    assert TaskHierarchy.expanded?(state, 2)
    assert TaskHierarchy.expanded?(state, 3)
    refute TaskHierarchy.expanded?(state, 4)
  end

  test "same-root navigation retains expanded branches and adds the new selected path" do
    first_state = TaskHierarchy.load(TaskHierarchy.empty(), hierarchy(3))
    expanded_state = TaskHierarchy.toggle(first_state, 4)

    state = TaskHierarchy.load(expanded_state, hierarchy(5))

    assert TaskHierarchy.expanded?(state, 1)
    assert TaskHierarchy.expanded?(state, 2)
    assert TaskHierarchy.expanded?(state, 3)
    assert TaskHierarchy.expanded?(state, 4)
    assert TaskHierarchy.expanded?(state, 5)
  end

  test "a disclosure toggles only a branch outside the selected path" do
    state = TaskHierarchy.load(TaskHierarchy.empty(), hierarchy(2))

    assert TaskHierarchy.expanded?(state, 1)
    assert TaskHierarchy.expanded?(state, 2)
    assert TaskHierarchy.expanded?(TaskHierarchy.toggle(state, 1), 1)
    assert TaskHierarchy.expanded?(TaskHierarchy.toggle(state, 2), 2)

    refute TaskHierarchy.expanded?(state, 4)
    assert TaskHierarchy.expanded?(TaskHierarchy.toggle(state, 4), 4)

    refute state
           |> TaskHierarchy.toggle(4)
           |> TaskHierarchy.toggle(4)
           |> TaskHierarchy.expanded?(4)
  end

  test "loading a different connected tree resets prior branch expansion" do
    state =
      TaskHierarchy.empty()
      |> TaskHierarchy.load(hierarchy(3))
      |> TaskHierarchy.toggle(4)
      |> TaskHierarchy.load(other_hierarchy())

    refute TaskHierarchy.expanded?(state, 1)
    refute TaskHierarchy.expanded?(state, 4)
    assert TaskHierarchy.expanded?(state, 10)
    assert TaskHierarchy.expanded?(state, 11)
  end

  test "clearing hierarchy state removes the current tree and expansion choices" do
    state = TaskHierarchy.load(TaskHierarchy.empty(), hierarchy(3))

    assert TaskHierarchy.clear(state) == TaskHierarchy.empty()
  end

  defp hierarchy(selected_task_id) do
    %Hierarchy{
      selected_task_id: selected_task_id,
      root:
        hierarchy_node(1, [
          hierarchy_node(2, [hierarchy_node(3)]),
          hierarchy_node(4, [hierarchy_node(5)])
        ])
    }
  end

  defp other_hierarchy do
    %Hierarchy{selected_task_id: 11, root: hierarchy_node(10, [hierarchy_node(11)])}
  end

  defp hierarchy_node(id, children \\ []) do
    %HierarchyNode{
      task: %Task{id: id, title: "Task #{id}"},
      location_path: [],
      children: children
    }
  end
end
