defmodule TaskmanWeb.WorkspaceNavigationTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Lists.NavigationNode
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias TaskmanWeb.WorkspaceNavigation

  test "renders a flattened semantic tree with independent controls" do
    project = %Project{id: 7, name: "Taskman", primary_directory: "/workspace/taskman"}
    root = %TaskList{id: 11, project_id: project.id, name: "Planning"}
    child = %TaskList{id: 12, project_id: project.id, parent_list_id: root.id, name: "Launch"}

    nodes = [
      %NavigationNode{
        dom_id: "project-7",
        kind: :project,
        depth: 1,
        project: project,
        task_list: nil,
        expanded?: true,
        expandable?: true,
        selected?: false
      },
      %NavigationNode{
        dom_id: "list-11",
        kind: :list,
        depth: 2,
        project: project,
        task_list: root,
        expanded?: true,
        expandable?: true,
        selected?: false
      },
      %NavigationNode{
        dom_id: "list-12",
        kind: :list,
        depth: 3,
        project: project,
        task_list: child,
        expanded?: false,
        expandable?: false,
        selected?: true
      }
    ]

    html =
      render_component(&WorkspaceNavigation.tree/1, %{
        navigation_nodes: Enum.map(nodes, &{&1.dom_id, &1}),
        selected_project: project,
        selected_list: child,
        include_children?: false,
        active_list_form: nil,
        list_form: nil
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(
             LazyHTML.query(document, "#workspace-tree[role='tree'][phx-update='stream']")
           )

    refute Enum.empty?(LazyHTML.query(document, "#project-7[role='treeitem'][aria-level='1']"))

    refute Enum.empty?(LazyHTML.query(document, "#list-11[role='treeitem'][aria-level='2']"))

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#list-12[role='treeitem'][aria-level='3'][aria-current='page']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(document, "#list-12[role='treeitem'] a[aria-current='page']")
           )

    refute Enum.empty?(LazyHTML.query(document, "#toggle-list-11[aria-expanded='true']"))

    refute Enum.empty?(
             LazyHTML.query(document, "#toggle-list-11[aria-label='Collapse Planning']")
           )

    refute Enum.empty?(LazyHTML.query(document, "#project-7 a[aria-label='Select Taskman']"))
    refute Enum.empty?(LazyHTML.query(document, "#list-11 a[aria-label='Select Planning']"))

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#project-7 a[aria-label='Select Taskman'] [data-project-directory][title='/workspace/taskman']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(document, "#list-11 button[aria-label='Add child List to Planning']")
           )

    refute Enum.empty?(LazyHTML.query(document, "#list-11 button[aria-label='Rename Planning']"))
    assert Enum.empty?(LazyHTML.query(document, "[aria-label*='Delete']"))
  end

  test "keeps List actions visible until a fine pointer can reveal them" do
    project = %Project{id: 7, name: "Taskman"}
    task_list = %TaskList{id: 11, project_id: project.id, name: "Planning"}

    list_node = %NavigationNode{
      dom_id: "list-11",
      kind: :list,
      depth: 2,
      project: project,
      task_list: task_list,
      expanded?: false,
      expandable?: false,
      selected?: false
    }

    document =
      render_component(&WorkspaceNavigation.tree/1, %{
        navigation_nodes: [
          {"project-7", project_node(project)},
          {list_node.dom_id, list_node}
        ],
        selected_project: project,
        selected_list: nil,
        include_children?: false,
        active_list_form: nil,
        list_form: nil
      })
      |> LazyHTML.from_fragment()

    for selector <- [
          "#add-list-project-7",
          "#add-child-list-11",
          "#rename-list-11"
        ] do
      [class_attribute] =
        document
        |> LazyHTML.query(selector)
        |> LazyHTML.attribute("class")

      classes = String.split(class_attribute)

      assert "opacity-100" in classes
      assert "pointer-fine:opacity-0" in classes
      assert "group-hover:opacity-100" in classes
      assert "focus:opacity-100" in classes
      refute "opacity-0" in classes
    end
  end

  test "renders the active root and nested List forms with stable IDs" do
    project = %Project{id: 7, name: "Taskman"}
    parent = %TaskList{id: 11, project_id: project.id, name: "Planning"}
    changeset = Taskman.Lists.change_list(%TaskList{project_id: project.id}, %{})
    form = Phoenix.Component.to_form(changeset, as: :list)

    node = %NavigationNode{
      dom_id: "project-7",
      kind: :project,
      depth: 1,
      project: project,
      task_list: nil,
      expanded?: true,
      expandable?: true,
      selected?: true
    }

    root_html =
      render_component(&WorkspaceNavigation.tree/1, %{
        navigation_nodes: [{node.dom_id, node}],
        selected_project: project,
        selected_list: nil,
        include_children?: false,
        active_list_project: project,
        active_list_form: {:new, nil},
        list_form: form
      })

    refute Enum.empty?(
             LazyHTML.query(LazyHTML.from_fragment(root_html), "#list-create-form-root")
           )

    nested_html =
      render_component(&WorkspaceNavigation.tree/1, %{
        navigation_nodes: [
          {node.dom_id, node},
          {"list-11",
           %NavigationNode{
             dom_id: "list-11",
             kind: :list,
             depth: 2,
             project: project,
             task_list: parent,
             expanded?: false,
             expandable?: false,
             selected?: false
           }}
        ],
        selected_project: project,
        selected_list: nil,
        include_children?: false,
        active_list_project: project,
        active_list_form: {:new, parent},
        list_form: form
      })

    refute Enum.empty?(
             LazyHTML.query(LazyHTML.from_fragment(nested_html), "#list-create-form-11")
           )

    rename_html =
      render_component(&WorkspaceNavigation.tree/1, %{
        navigation_nodes: [
          {node.dom_id, node},
          {"list-11",
           %NavigationNode{
             dom_id: "list-11",
             kind: :list,
             depth: 2,
             project: project,
             task_list: parent,
             expanded?: false,
             expandable?: false,
             selected?: false
           }}
        ],
        selected_project: project,
        selected_list: nil,
        include_children?: false,
        active_list_project: project,
        active_list_form: {:rename, parent},
        list_form: form
      })

    refute Enum.empty?(
             LazyHTML.query(LazyHTML.from_fragment(rename_html), "#list-rename-form-11")
           )
  end

  test "focuses the List name input when add and rename popovers open" do
    for {document, form_id} <- list_form_documents() do
      mounted_actions =
        document
        |> LazyHTML.query("##{form_id}")
        |> LazyHTML.attribute("phx-mounted")

      assert [mounted_actions] = mounted_actions
      assert mounted_actions =~ "focus"
      assert mounted_actions =~ "#list-name"
    end
  end

  test "dismisses add and rename popovers on outside click or Escape" do
    for {document, form_id} <- list_form_documents() do
      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 "##{form_id}[phx-click-away='cancel_list_form'][phx-window-keydown='cancel_list_form'][phx-key='escape']"
               )
             )
    end
  end

  test "renders add and rename popovers outside the tree layout flow" do
    for {document, _form_id} <- list_form_documents() do
      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 "[data-list-popover].absolute.left-9.right-1.top-full.z-30"
               )
             )
    end
  end

  test "keeps List form controls inside a treeitem without an ancestor selection click" do
    project = %Project{id: 7, name: "Taskman"}
    task_list = %TaskList{id: 11, project_id: project.id, name: "Planning"}

    node = %NavigationNode{
      dom_id: "list-11",
      kind: :list,
      depth: 2,
      project: project,
      task_list: task_list,
      expanded?: false,
      expandable?: false,
      selected?: false
    }

    changeset = Taskman.Lists.change_list(task_list, %{})

    html =
      render_component(&WorkspaceNavigation.tree/1, %{
        navigation_nodes: [{node.dom_id, node}],
        selected_project: project,
        selected_list: nil,
        include_children?: false,
        active_list_project: project,
        active_list_form: {:rename, task_list},
        list_form: Phoenix.Component.to_form(changeset, as: :list)
      })

    document = LazyHTML.from_fragment(html)

    assert document
           |> LazyHTML.query("#list-11[role='treeitem']")
           |> LazyHTML.attribute("phx-click") == []

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#list-11 #list-rename-form-11[phx-change='validate_list'][phx-submit='save_list']"
             )
           )

    refute Enum.empty?(LazyHTML.query(document, "#list-11 #list-rename-form-11 #list-name"))

    refute Enum.empty?(
             LazyHTML.query(document, "#list-11 #list-rename-form-11-submit[type='submit']")
           )
  end

  test "renders an active root form only beneath its owning Project" do
    first_project = %Project{id: 7, name: "First"}
    second_project = %Project{id: 8, name: "Second"}
    first_node = project_node(first_project)
    second_node = project_node(second_project)
    changeset = Taskman.Lists.change_list(%TaskList{project_id: first_project.id}, %{})

    html =
      render_component(&WorkspaceNavigation.tree/1, %{
        navigation_nodes: [{first_node.dom_id, first_node}, {second_node.dom_id, second_node}],
        selected_project: first_project,
        selected_list: nil,
        include_children?: false,
        active_list_project: first_project,
        active_list_form: {:new, nil},
        list_form: Phoenix.Component.to_form(changeset, as: :list)
      })

    document = LazyHTML.from_fragment(html)
    forms = LazyHTML.query(document, "#list-create-form-root")

    assert Enum.count(forms) == 1
    refute Enum.empty?(LazyHTML.query(document, "#project-7 #list-create-form-root"))
    assert Enum.empty?(LazyHTML.query(document, "#project-8 #list-create-form-root"))
  end

  defp project_node(project) do
    %NavigationNode{
      dom_id: "project-#{project.id}",
      kind: :project,
      depth: 1,
      project: project,
      task_list: nil,
      expanded?: false,
      expandable?: false,
      selected?: false
    }
  end

  defp list_form_documents do
    project = %Project{id: 7, name: "Taskman"}
    task_list = %TaskList{id: 11, project_id: project.id, name: "Planning"}

    [
      render_form_document(project, project_node(project), {:new, nil}, %TaskList{
        project_id: project.id
      }),
      render_form_document(
        project,
        %NavigationNode{
          dom_id: "list-11",
          kind: :list,
          depth: 2,
          project: project,
          task_list: task_list,
          expanded?: false,
          expandable?: false,
          selected?: false
        },
        {:rename, task_list},
        task_list
      )
    ]
  end

  defp render_form_document(project, node, active_list_form, task_list) do
    changeset = Taskman.Lists.change_list(task_list, %{})

    document =
      render_component(&WorkspaceNavigation.tree/1, %{
        navigation_nodes: [{node.dom_id, node}],
        selected_project: project,
        selected_list: nil,
        include_children?: false,
        active_list_project: project,
        active_list_form: active_list_form,
        list_form: Phoenix.Component.to_form(changeset, as: :list)
      })
      |> LazyHTML.from_fragment()

    {document, form_id(active_list_form)}
  end

  defp form_id({:new, nil}), do: "list-create-form-root"
  defp form_id({:rename, task_list}), do: "list-rename-form-#{task_list.id}"
end
