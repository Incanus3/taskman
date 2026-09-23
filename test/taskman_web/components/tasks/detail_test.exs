defmodule TaskmanWeb.Tasks.DetailTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks.{Hierarchy, HierarchyNode, Task}
  alias TaskmanWeb.ProjectLive.Tasks.{Autosave, Move, ParentPicker}
  alias TaskmanWeb.ProjectLive.Tasks.Hierarchy, as: TaskHierarchy
  alias TaskmanWeb.Tasks.Detail

  test "renders the selected Task's complete navigable List path before the current Task" do
    project = %Project{id: 7, name: "Atlas"}
    planning = %TaskList{id: 11, project_id: project.id, name: "Planning"}
    launch = %TaskList{id: 12, project_id: project.id, name: "Launch"}
    task = task(41)

    task_hierarchy =
      TaskHierarchy.load(
        TaskHierarchy.empty(),
        %Hierarchy{
          selected_task_id: task.id,
          root: hierarchy_node(task.id, [], [planning, launch])
        }
      )

    html =
      render_component(&Detail.detail/1, %{
        task: task,
        project: project,
        task_autosave: Autosave.load(Autosave.empty(), task, saved?: true),
        parent_picker: ParentPicker.empty(),
        cancel: "/projects/7/lists/12",
        task_hierarchy: task_hierarchy,
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        browse_path: fn
          nil -> "/projects/7?include_children=true"
          task_list -> "/projects/7/lists/#{task_list.id}?include_children=true"
        end,
        task_move: Move.empty()
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-location-breadcrumbs[aria-label='Task location'][phx-hook='TaskmanWeb.Tasks.Detail.TaskLocationBreadcrumbs']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-location-list-11[href='/projects/7/lists/11?include_children=true'][data-optional-segment]"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-location-list-12[href='/projects/7/lists/12?include_children=true'][data-containing-location]"
             )
           )

    assert document
           |> LazyHTML.query("#task-location-current[aria-current='page']")
           |> LazyHTML.text()
           |> String.trim() == "Task #41"

    refute Enum.empty?(LazyHTML.query(document, "#task-location-ellipsis[hidden]"))
  end

  test "uses the navigable Project name as the root Task location" do
    project = %Project{id: 7, name: "Atlas"}
    task = task(41)

    html =
      render_component(&Detail.detail/1, %{
        task: task,
        project: project,
        task_autosave: Autosave.load(Autosave.empty(), task, saved?: true),
        parent_picker: ParentPicker.empty(),
        cancel: "/projects/7",
        task_hierarchy:
          TaskHierarchy.load(
            TaskHierarchy.empty(),
            %Hierarchy{selected_task_id: task.id, root: hierarchy_node(task.id)}
          ),
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        browse_path: fn
          nil -> "/projects/7?include_children=true"
          task_list -> "/projects/7/lists/#{task_list.id}?include_children=true"
        end,
        task_move: Move.empty()
      })

    document = LazyHTML.from_fragment(html)

    assert document
           |> LazyHTML.query(
             "#task-location-project-7[href='/projects/7?include_children=true'][data-containing-location]"
           )
           |> LazyHTML.text()
           |> String.trim() == "Atlas"
  end

  test "renders independent field-local save status and no form-wide footer" do
    task = %Task{id: 41, project_id: 7, title: "Launch", status: :pending, priority: :none}

    task_autosave = %{
      Autosave.load(Autosave.empty(), task, saved?: false)
      | field_states: %{"title" => :saving, "description" => :saved}
    }

    html =
      render_component(&Detail.detail/1, %{
        task: task,
        project: %Project{id: 7, name: "Atlas"},
        task_autosave: task_autosave,
        parent_picker: ParentPicker.empty(),
        cancel: "/projects/7",
        task_hierarchy:
          TaskHierarchy.load(
            TaskHierarchy.empty(),
            %Hierarchy{selected_task_id: task.id, root: hierarchy_node(task.id)}
          ),
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        browse_path: fn
          nil -> "/projects/7"
          task_list -> "/projects/7/lists/#{task_list.id}"
        end,
        task_move: Move.empty()
      })

    document = LazyHTML.from_fragment(html)

    assert document |> LazyHTML.query("#task-modal-title") |> LazyHTML.text() |> String.trim() ==
             "Task #41"

    refute Enum.empty?(LazyHTML.query(document, "#task-form"))

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-title-save-status[aria-live='polite'][data-state='saving']"
             )
           )

    assert LazyHTML.text(LazyHTML.query(document, "#task-title-save-status")) =~ "Saving…"

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-description-save-status[aria-live='polite'][data-state='saved']"
             )
           )

    assert LazyHTML.text(LazyHTML.query(document, "#task-description-save-status")) =~ "Saved"

    assert Enum.empty?(LazyHTML.query(document, "#task-status-save-status[data-state]"))
    assert Enum.empty?(LazyHTML.query(document, "#task-priority-save-status[data-state]"))
    assert Enum.empty?(LazyHTML.query(document, "#task-due-at-save-status[data-state]"))
    assert Enum.empty?(LazyHTML.query(document, "#task-save-status"))
    refute Enum.empty?(LazyHTML.query(document, "#move-task-detail-button-41.cursor-pointer"))
  end

  test "renders a progressive semantic Task tree with the current context expanded" do
    hierarchy = hierarchy(2)

    html =
      render_component(&Detail.detail/1, %{
        task: hierarchy.root.children |> hd() |> Map.fetch!(:task),
        project: %Project{id: 7, name: "Atlas"},
        task_autosave: Autosave.load(Autosave.empty(), task(2), saved?: true),
        parent_picker: ParentPicker.empty(),
        cancel: "/projects/7",
        task_hierarchy: TaskHierarchy.load(TaskHierarchy.empty(), hierarchy),
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        browse_path: fn
          nil -> "/projects/7"
          task_list -> "/projects/7/lists/#{task_list.id}"
        end,
        task_move: Move.empty()
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(LazyHTML.query(document, "#task-hierarchy [role='tree']"))

    refute Enum.empty?(
             LazyHTML.query(document, "#task-hierarchy-node-1[role='treeitem'][aria-level='1']")
           )

    refute Enum.empty?(
             LazyHTML.query(document, "#task-hierarchy-node-2[role='treeitem'][aria-level='2']")
           )

    refute Enum.empty?(LazyHTML.query(document, "#task-hierarchy-node-1 > [role='group']"))
    refute Enum.empty?(LazyHTML.query(document, "#task-hierarchy-node-2 > [role='group']"))

    assert Enum.empty?(LazyHTML.query(document, "#task-hierarchy-disclosure-1"))
    assert Enum.empty?(LazyHTML.query(document, "#task-hierarchy-disclosure-2"))

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-hierarchy-disclosure-4[aria-expanded='false']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-hierarchy-link-2[aria-current='true'][href='/projects/7/tasks/2']"
             )
           )

    refute Enum.empty?(LazyHTML.query(document, "#task-hierarchy-node-3"))
    refute Enum.empty?(LazyHTML.query(document, "#task-hierarchy-node-4"))
    assert Enum.empty?(LazyHTML.query(document, "#task-hierarchy-node-5"))
    assert Enum.empty?(LazyHTML.query(document, "#task-hierarchy-empty"))
  end

  test "renders a disconnected Task as the truthful hierarchy empty state" do
    hierarchy = %Hierarchy{selected_task_id: 7, root: hierarchy_node(7)}

    html =
      render_component(&Detail.detail/1, %{
        task: task(7),
        project: %Project{id: 7, name: "Atlas"},
        task_autosave: Autosave.load(Autosave.empty(), task(7), saved?: true),
        parent_picker: ParentPicker.empty(),
        cancel: "/projects/7",
        task_hierarchy: TaskHierarchy.load(TaskHierarchy.empty(), hierarchy),
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        browse_path: fn
          nil -> "/projects/7"
          task_list -> "/projects/7/lists/#{task_list.id}"
        end,
        task_move: Move.empty()
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-hierarchy-node-7[role='treeitem'][aria-current='true']"
             )
           )

    refute Enum.empty?(LazyHTML.query(document, "#task-hierarchy-empty"))
    assert Enum.empty?(LazyHTML.query(document, "#task-hierarchy-disclosure-7"))
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

  defp hierarchy_node(id, children \\ [], location_path \\ []) do
    %HierarchyNode{task: task(id), location_path: location_path, children: children}
  end

  defp task(id),
    do: %Task{id: id, project_id: 7, title: "Task #{id}", status: :pending, priority: :none}
end
