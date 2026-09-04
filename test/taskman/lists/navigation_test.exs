defmodule Taskman.Lists.NavigationTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures

  alias Taskman.Lists
  alias Taskman.Lists.NavigationNode

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
