defmodule Taskman.ListsTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures

  alias Taskman.Lists
  alias Taskman.Lists.NavigationNode

  test "creates arbitrarily nested Lists and returns stable root-to-node paths" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    leaf = list_fixture(project, child, %{name: "Copy"})

    assert Lists.list_lists_for_project(project) == [root, child, leaf]
    assert Lists.path_for(Lists.list_lists_for_project(project), leaf) == [root, child, leaf]
  end

  test "rejects duplicate sibling names without regard to case" do
    project = project_fixture(%{})
    _existing = list_fixture(project, nil, %{name: "Planning"})

    assert {:error, changeset} = Lists.create_list(project, nil, %{name: "planning"})
    assert %{name: [_]} = errors_on(changeset)
  end

  test "allows the same name under different parents" do
    project = project_fixture(%{})
    first_parent = list_fixture(project, nil, %{name: "First"})
    second_parent = list_fixture(project, nil, %{name: "Second"})

    assert {:ok, first_child} = Lists.create_list(project, first_parent, %{name: "Shared"})
    assert {:ok, second_child} = Lists.create_list(project, second_parent, %{name: "shared"})
    assert first_child.parent_list_id == first_parent.id
    assert second_child.parent_list_id == second_parent.id
  end

  test "rejects a parent from another Project" do
    project = project_fixture(%{})
    other_parent = list_fixture(project_fixture(%{}), nil)

    assert {:error, :not_found} = Lists.create_list(project, other_parent, %{name: "Child"})
  end

  test "gets only Project-owned Lists and rejects malformed IDs" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Owned"})
    other_list = list_fixture(other_project, nil, %{name: "Other"})

    assert Lists.get_list_for_project(project, task_list.id) == task_list
    assert Lists.get_list_for_project(project, Integer.to_string(task_list.id)) == task_list
    assert Lists.get_list_for_project(project, other_list.id) == nil
    assert Lists.get_list_for_project(project, "not-an-id") == nil
    assert Lists.get_list_for_project(project, "#{task_list.id}tail") == nil
    assert Lists.get_list_for_project(project, 0) == nil
    assert Lists.get_list_for_project(project, -1) == nil
    assert Lists.get_list_for_project(project, nil) == nil
  end

  test "validates, trims, and protects List ownership fields" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    parent = list_fixture(project, nil, %{name: "Parent"})

    assert {:ok, task_list} =
             Lists.create_list(project, nil, %{
               name: "  Root  ",
               project_id: other_project.id,
               parent_list_id: parent.id
             })

    assert task_list.name == "Root"
    assert task_list.project_id == project.id
    assert task_list.parent_list_id == nil

    assert {:error, changeset} = Lists.create_list(project, nil, %{name: "   "})
    assert %{name: [_]} = errors_on(changeset)

    assert {:error, changeset} =
             Lists.create_list(project, nil, %{name: String.duplicate("x", 256)})

    assert %{name: [_]} = errors_on(changeset)
  end

  test "renames a List while keeping ownership immutable" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Before"})
    other_parent = list_fixture(other_project, nil, %{name: "Other parent"})

    assert {:ok, renamed} =
             Lists.rename_list(project, task_list, %{
               name: "  After  ",
               project_id: other_project.id,
               parent_list_id: other_parent.id
             })

    assert renamed.name == "After"
    assert renamed.project_id == project.id
    assert renamed.parent_list_id == nil
    assert {:error, :not_found} = Lists.rename_list(other_project, task_list, %{name: "Leaked"})
    assert Lists.get_list_for_project(project, task_list.id).name == "After"
  end

  test "builds a depth-first navigation tree and opens selected ancestors" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    leaf = list_fixture(project, child, %{name: "Copy"})
    _other_root = list_fixture(other_project, nil, %{name: "Other"})

    lists_by_project = %{
      project.id => Lists.list_lists_for_project(project),
      other_project.id => Lists.list_lists_for_project(other_project)
    }

    nodes =
      Lists.navigation_nodes(
        [project, other_project],
        lists_by_project,
        {:list, leaf.id},
        MapSet.new()
      )

    assert Enum.map(nodes, fn node ->
             {node.dom_id, node.kind, node.depth, node.task_list && node.task_list.name}
           end) == [
             {"project-#{project.id}", :project, 1, nil},
             {"list-#{root.id}", :list, 2, "Planning"},
             {"list-#{child.id}", :list, 3, "Launch"},
             {"list-#{leaf.id}", :list, 4, "Copy"},
             {"project-#{other_project.id}", :project, 1, nil}
           ]

    [project_node, root_node, child_node, leaf_node, other_node] = nodes
    assert %NavigationNode{} = project_node
    assert project_node.expanded?
    assert root_node.expanded?
    assert child_node.expanded?
    refute leaf_node.expanded?
    assert leaf_node.selected?
    refute project_node.selected?
    refute other_node.expanded?
    assert other_node.task_list == nil
    assert Enum.all?([project_node, root_node, child_node], & &1.expandable?)
    refute leaf_node.expandable?
    assert other_node.expandable?
  end

  test "selected Project does not force its own descendants open" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    _child = list_fixture(project, root, %{name: "Launch"})

    [project_node] =
      Lists.navigation_nodes(
        [project],
        %{project.id => Lists.list_lists_for_project(project)},
        {:project, project.id},
        MapSet.new()
      )

    assert project_node.selected?
    refute project_node.expanded?
  end

  test "selected root List forces its Project node open" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})

    [project_node, root_node] =
      Lists.navigation_nodes(
        [project],
        %{project.id => Lists.list_lists_for_project(project)},
        {:list, root.id},
        MapSet.new()
      )

    assert project_node.expanded?
    assert root_node.selected?
    refute root_node.expanded?
  end
end
