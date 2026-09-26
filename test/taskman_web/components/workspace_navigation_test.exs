defmodule TaskmanWeb.WorkspaceNavigationTest do
  use ExUnit.Case, async: true
  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Lists.NavigationNode
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias TaskmanWeb.ProjectLive.ListEdit
  alias TaskmanWeb.WorkspaceNavigation

  test "Project tasks link and Add root List button share a row and remain separate controls" do
    project = project()
    document = render_tree(project, [])

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "nav#workspace-navigation[aria-label='Workspace navigation'] > #project-tasks-row"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#project-tasks-row > div > #project-tasks-link[href='/projects/7'][aria-current='page']"
             )
           )

    assert "bg-white/12" in (document
                             |> LazyHTML.query("#project-tasks-row > div")
                             |> LazyHTML.attribute("class")
                             |> hd()
                             |> String.split())

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#project-tasks-row > div > #add-root-list-7[phx-click='open_list_form'][aria-label='Add root List']"
             )
           )

    assert Enum.empty?(LazyHTML.query(document, "#project-tasks-link button"))
    assert Enum.empty?(LazyHTML.query(document, "#project-tasks-row[role='treeitem']"))
  end

  test "missing List route leaves Project tasks unselected" do
    document = render_tree(project(), [], location_not_found?: true)
    assert Enum.empty?(LazyHTML.query(document, "#project-tasks-link[aria-current='page']"))
  end

  test "no selected Project omits the root row and List tree" do
    document = render_tree(nil, [])
    assert Enum.empty?(LazyHTML.query(document, "#project-tasks-row"))
    assert Enum.empty?(LazyHTML.query(document, "#workspace-tree"))
  end

  test "root and child Lists have semantic levels and independent link and disclosure" do
    project = project()
    root = list_node(project, 11, "Planning", 1, "hero-folder-open", true, true)
    child = list_node(project, 12, "Launch", 2, "hero-list-bullet", false, false, true)
    document = render_tree(project, [root, child], selected_list: child.task_list)

    refute Enum.empty?(
             LazyHTML.query(document, "#workspace-tree[role='tree'][phx-update='stream']")
           )

    refute Enum.empty?(LazyHTML.query(document, "#list-11[role='treeitem'][aria-level='1']"))
    refute Enum.empty?(LazyHTML.query(document, "#list-12[role='treeitem'][aria-level='2']"))

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#toggle-list-11[aria-expanded='true'][aria-label='Collapse Planning']"
             )
           )

    refute Enum.empty?(LazyHTML.query(document, "#select-list-11[href='/projects/7/lists/11']"))
    refute Enum.empty?(LazyHTML.query(document, "#select-list-12[aria-current='page']"))
    assert Enum.empty?(LazyHTML.query(document, "#toggle-list-12"))
    assert Enum.empty?(LazyHTML.query(document, "#project-tasks-link[aria-current='page']"))
  end

  test "non-leaf icon and chevron occupy one disclosure button with focus visuals" do
    project = project()
    node = list_node(project, 11, "Planning", 1, "hero-queue-list", true, false)
    document = render_tree(project, [node])

    refute Enum.empty?(LazyHTML.query(document, "#toggle-list-11 .hero-queue-list"))
    refute Enum.empty?(LazyHTML.query(document, "#toggle-list-11 .hero-chevron-right"))
    refute Enum.empty?(LazyHTML.query(document, "#toggle-list-11.focus-visible\\:ring-2"))
    assert Enum.empty?(LazyHTML.query(document, "#select-list-11 .hero-queue-list"))
  end

  test "root and nested forms retain focus and dismissal behavior" do
    project = project()
    root = list_node(project, 11, "Planning", 1, "hero-list-bullet", false, false)

    root_document = render_tree(project, [root], list_edit: ListEdit.open_new(project, nil))

    nested_document =
      render_tree(project, [root], list_edit: ListEdit.open_new(project, root.task_list))

    refute Enum.empty?(LazyHTML.query(root_document, "#project-tasks-row #list-create-form-root"))
    refute Enum.empty?(LazyHTML.query(nested_document, "#list-11 #list-create-form-11"))

    for {document, id} <- [
          {root_document, "list-create-form-root"},
          {nested_document, "list-create-form-11"}
        ] do
      [mounted] = document |> LazyHTML.query("##{id}") |> LazyHTML.attribute("phx-mounted")
      assert mounted =~ "#list-name"

      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 "##{id}[phx-click-away='cancel_list_form'][phx-window-keydown='cancel_list_form'][phx-key='escape']"
               )
             )
    end
  end

  defp project, do: %Project{id: 7, name: "Taskman"}

  defp list_node(project, id, name, depth, icon, expandable?, expanded?, selected? \\ false) do
    %NavigationNode{
      dom_id: "list-#{id}",
      kind: :list,
      depth: depth,
      project: project,
      task_list: %TaskList{id: id, project_id: project.id, name: name},
      list_kind: if(expandable?, do: :child_only, else: :leaf),
      icon: icon,
      expandable?: expandable?,
      expanded?: expanded?,
      selected?: selected?
    }
  end

  defp render_tree(project, nodes, opts \\ []) do
    render_component(&WorkspaceNavigation.tree/1, %{
      selected_project: project,
      selected_list: Keyword.get(opts, :selected_list),
      location_not_found?: Keyword.get(opts, :location_not_found?, false),
      navigation_nodes: Enum.map(nodes, &{&1.dom_id, &1}),
      list_edit: Keyword.get(opts, :list_edit, ListEdit.empty())
    })
    |> LazyHTML.from_fragment()
  end
end
