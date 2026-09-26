defmodule Taskman.Lists.NavigationTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Lists
  alias Taskman.Tasks

  test "direct Task presence is scoped to one Project and ignores status" do
    project = project_fixture(%{})
    other = project_fixture(%{})
    empty = list_fixture(project)
    filled = list_fixture(project)
    foreign = list_fixture(other)
    _hidden = task_fixture(project, filled, %{status: :done})
    _also_filled = task_fixture(project, filled, %{})
    _foreign = task_fixture(other, foreign, %{})
    _root_task = task_fixture(project, %{})

    assert Tasks.list_ids_with_direct_tasks(project) == MapSet.new([filled.id])
    assert Tasks.list_ids_with_direct_tasks(other) == MapSet.new([foreign.id])
    refute MapSet.member?(Tasks.list_ids_with_direct_tasks(project), empty.id)
  end

  test "a selected descendant opens ancestors in a Lists-only depth-first tree" do
    project = project_fixture(%{})
    other = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    leaf = list_fixture(project, child, %{name: "Copy"})
    _other = list_fixture(other)

    nodes =
      Lists.navigation_nodes(
        project,
        Lists.list_lists_for_project(project),
        MapSet.new(),
        {:list, leaf.id},
        MapSet.new()
      )

    assert Enum.map(nodes, &{&1.kind, &1.depth, &1.task_list.id}) ==
             [{:list, 1, root.id}, {:list, 2, child.id}, {:list, 3, leaf.id}]

    assert Enum.map(nodes, & &1.expanded?) == [true, true, false]
    assert List.last(nodes).selected?
  end

  test "leaf, child-only, and mixed icons use direct Task ownership" do
    project = project_fixture(%{})
    empty = list_fixture(project)
    filled = list_fixture(project)
    child_only = list_fixture(project)
    mixed = list_fixture(project)
    descendant = list_fixture(project, child_only)
    _mixed_child = list_fixture(project, mixed)
    _filled_task = task_fixture(project, filled, %{status: :done})
    _descendant_task = task_fixture(project, descendant, %{})
    _mixed_task = task_fixture(project, mixed, %{})

    lists = Lists.list_lists_for_project(project)
    direct_ids = Tasks.list_ids_with_direct_tasks(project)
    closed = Lists.navigation_nodes(project, lists, direct_ids, nil, MapSet.new())

    opened =
      Lists.navigation_nodes(
        project,
        lists,
        direct_ids,
        nil,
        MapSet.new([{:list, child_only.id}, {:list, mixed.id}])
      )

    by_id = Map.new(closed, &{&1.task_list.id, &1})
    open_by_id = Map.new(opened, &{&1.task_list.id, &1})

    assert %{
             kind: :list,
             depth: 1,
             list_kind: :leaf,
             icon: "hero-list-bullet",
             expandable?: false
           } =
             by_id[empty.id]

    assert %{list_kind: :leaf, icon: "hero-list-bullet", expandable?: false} = by_id[filled.id]

    assert %{list_kind: :child_only, icon: "hero-folder", expanded?: false, expandable?: true} =
             by_id[child_only.id]

    assert %{icon: "hero-folder-open", expanded?: true} = open_by_id[child_only.id]
    assert %{icon: "hero-list-bullet", depth: 2} = open_by_id[descendant.id]
    assert %{list_kind: :mixed, icon: "hero-queue-list", expanded?: false} = by_id[mixed.id]
    assert %{list_kind: :mixed, icon: "hero-queue-list", expanded?: true} = open_by_id[mixed.id]
  end

  test "moving the only direct Task changes both source and destination icons" do
    project = project_fixture(%{})
    source = list_fixture(project)
    destination = list_fixture(project)
    _source_child = list_fixture(project, source)
    _destination_child = list_fixture(project, destination)
    task = task_fixture(project, source, %{})
    lists = Lists.list_lists_for_project(project)

    icons = fn ->
      project
      |> Tasks.list_ids_with_direct_tasks()
      |> then(&Lists.navigation_nodes(project, lists, &1, nil, MapSet.new()))
      |> Map.new(&{&1.task_list.id, &1.icon})
    end

    assert icons.()[source.id] == "hero-queue-list"
    assert icons.()[destination.id] == "hero-folder"
    assert {:ok, _moved} = Tasks.move_task(project, task, destination)
    assert icons.()[source.id] == "hero-folder"
    assert icons.()[destination.id] == "hero-queue-list"
  end

  test "adding and removing the last child changes pure navigation state" do
    project = project_fixture(%{})
    root = list_fixture(project)

    child = %Taskman.Lists.TaskList{
      id: -1,
      project_id: project.id,
      parent_list_id: root.id,
      name: "Child"
    }

    lists = [root]

    build = fn lists ->
      Lists.navigation_nodes(project, lists, MapSet.new(), nil, MapSet.new())
    end

    assert [%{icon: "hero-list-bullet", expandable?: false}] = build.(lists)
    assert [%{icon: "hero-folder", expandable?: true}] = build.([root, child])
    assert [%{icon: "hero-list-bullet", expandable?: false}] = build.(lists)
  end
end
