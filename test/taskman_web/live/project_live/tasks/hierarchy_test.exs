defmodule TaskmanWeb.ProjectLive.Tasks.HierarchyTest do
  use ExUnit.Case, async: true

  alias Taskman.Tasks.{Hierarchy, HierarchyNode, Task}
  alias TaskmanWeb.ProjectLive.Tasks.Hierarchy, as: TaskHierarchy

  test "returns the selected Task's fresh location path" do
    location_path = [%{id: 21, name: "Planning"}, %{id: 22, name: "Launch"}]

    hierarchy = %Hierarchy{
      selected_task_id: 3,
      root:
        hierarchy_node(1, [
          hierarchy_node(2, [hierarchy_node(3, [], location_path)])
        ])
    }

    state = TaskHierarchy.load(TaskHierarchy.empty(), hierarchy)

    assert TaskHierarchy.selected_location_path(state) == location_path
  end

  test "finds a selected Task location after an unrelated sibling" do
    location_path = [%{id: 22, name: "Launch"}]

    hierarchy = %Hierarchy{
      selected_task_id: 3,
      root:
        hierarchy_node(1, [
          hierarchy_node(2),
          hierarchy_node(3, [], location_path)
        ])
    }

    state = TaskHierarchy.load(TaskHierarchy.empty(), hierarchy)

    assert TaskHierarchy.selected_location_path(state) == location_path
  end

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

  test "status projection searches collapsed descendants and preserves ancestry and sibling order" do
    source = %Hierarchy{
      selected_task_id: 3,
      root:
        status_node(1, :done, [
          status_node(2, :done, [status_node(3, :done)]),
          status_node(4, :done, [status_node(5, :done, [status_node(6, :pending)])]),
          status_node(7, :done),
          status_node(8, :pending)
        ])
    }

    state = TaskHierarchy.load(TaskHierarchy.empty(), source)
    filtered = TaskHierarchy.filter_statuses(state, ["pending", "invalid", "pending"])
    projected = TaskHierarchy.visible_root(filtered)

    assert Enum.map(projected.children, & &1.task.id) == [2, 4, 8]

    assert [%{task: %{id: 5}, children: [%{task: %{id: 6}}]}] =
             Enum.at(projected.children, 1).children

    refute TaskHierarchy.expanded?(filtered, 4)
    assert filtered.hierarchy == source
    assert state.hierarchy == source
    assert filtered.visible_statuses == [:pending]
  end

  test "no checked statuses retains only the current path and navigation recomputes it" do
    state = TaskHierarchy.load(TaskHierarchy.empty(), hierarchy(3))
    assert TaskHierarchy.visible_root(state) == state.hierarchy.root

    filtered = state |> TaskHierarchy.toggle(4) |> TaskHierarchy.filter_statuses([])
    assert tree_ids(TaskHierarchy.visible_root(filtered)) == [1, 2, 3]

    navigated = TaskHierarchy.load(filtered, hierarchy(5))
    assert tree_ids(TaskHierarchy.visible_root(navigated)) == [1, 4, 5]
    assert TaskHierarchy.expanded?(navigated, 4)
    assert navigated.visible_statuses == []
    assert TaskHierarchy.load(navigated, other_hierarchy()).visible_statuses == []
    assert TaskHierarchy.clear(navigated).visible_statuses == Task.statuses()
  end

  test "restoring checked statuses restores branches and their manual expansion" do
    state = TaskHierarchy.empty() |> TaskHierarchy.load(hierarchy(3)) |> TaskHierarchy.toggle(4)
    filtered = TaskHierarchy.filter_statuses(state, [])
    restored = TaskHierarchy.filter_statuses(filtered, Task.statuses())
    assert TaskHierarchy.visible_root(restored) == state.hierarchy.root
    assert TaskHierarchy.expanded?(restored, 4)
  end

  defp status_node(id, status, children \\ []) do
    node = hierarchy_node(id, children)
    %{node | task: %{node.task | status: status}}
  end

  defp tree_ids(node), do: [node.task.id | Enum.flat_map(node.children, &tree_ids/1)]

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

  defp hierarchy_node(id, children \\ [], location_path \\ []) do
    %HierarchyNode{
      task: %Task{id: id, title: "Task #{id}"},
      location_path: location_path,
      children: children
    }
  end
end
