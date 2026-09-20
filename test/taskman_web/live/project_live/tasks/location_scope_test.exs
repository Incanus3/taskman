defmodule TaskmanWeb.ProjectLive.Tasks.LocationScopeTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures

  alias Taskman.Lists
  alias TaskmanWeb.ProjectLive.Tasks.LocationScope

  setup do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Root"})
    child = list_fixture(project, root, %{name: "Child"})
    grandchild = list_fixture(project, child, %{name: "Grandchild"})
    sibling = list_fixture(project, nil, %{name: "Sibling"})
    unrelated = list_fixture(project_fixture(%{}), nil, %{name: "Unrelated"})
    task_lists = Lists.list_lists_for_project(project)

    %{
      project: project,
      root: root,
      child: child,
      grandchild: grandchild,
      sibling: sibling,
      unrelated: unrelated,
      task_lists: task_lists
    }
  end

  test "Project root and identical List locations are directly visible", context do
    assert LocationScope.visible?(nil, nil, false, context.task_lists)
    assert LocationScope.visible?(context.root, context.root, false, context.task_lists)
  end

  test "Project root sees List Tasks only when child Lists are included", context do
    refute LocationScope.visible?(nil, context.root, false, context.task_lists)
    assert LocationScope.visible?(nil, context.root, true, context.task_lists)
  end

  test "an ancestor List sees transitive descendants only when child Lists are included",
       context do
    refute LocationScope.visible?(context.root, context.child, false, context.task_lists)
    refute LocationScope.visible?(context.root, context.grandchild, false, context.task_lists)
    assert LocationScope.visible?(context.root, context.child, true, context.task_lists)
    assert LocationScope.visible?(context.root, context.grandchild, true, context.task_lists)
  end

  test "a selected List does not see ancestors, siblings, unrelated Lists, or Project-root Tasks",
       context do
    refute LocationScope.visible?(context.child, context.root, true, context.task_lists)
    refute LocationScope.visible?(context.root, context.sibling, true, context.task_lists)
    refute LocationScope.visible?(context.root, context.unrelated, true, context.task_lists)
    refute LocationScope.visible?(context.root, nil, true, context.task_lists)
  end

  test "a missing previous backdrop selects the actual Task location", context do
    workspace = %{
      selected_list: context.root,
      include_children?: true,
      location_not_found?: true
    }

    assert LocationScope.backdrop(workspace, context.child, context.task_lists) == context.child
  end

  test "a visible Task retains the previous backdrop", context do
    workspace = %{
      selected_list: context.root,
      include_children?: true,
      location_not_found?: false
    }

    assert LocationScope.backdrop(workspace, context.grandchild, context.task_lists) ==
             context.root
  end

  test "an out-of-scope Task selects its actual location", context do
    workspace = %{
      selected_list: context.root,
      include_children?: false,
      location_not_found?: false
    }

    assert LocationScope.backdrop(workspace, context.grandchild, context.task_lists) ==
             context.grandchild
  end

  test "Project-root fallback preserves visibility semantics for the caller's child setting",
       context do
    narrow = %{selected_list: nil, include_children?: false, location_not_found?: false}
    broad = %{narrow | include_children?: true}

    assert LocationScope.backdrop(narrow, context.child, context.task_lists) == context.child
    assert LocationScope.backdrop(broad, context.child, context.task_lists) == nil
  end
end
