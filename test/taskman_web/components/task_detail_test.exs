defmodule TaskmanWeb.TaskDetailTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Tasks.{Hierarchy, HierarchyNode, Task}
  alias TaskmanWeb.{TaskAutosave, TaskDetail, TaskHierarchy, TaskMove, TaskParentPicker}

  test "renders the Task form and save status from one autosave state" do
    task = %Task{id: 41, project_id: 7, title: "Launch", status: :pending, priority: :none}
    task_autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: true)

    html =
      render_component(&TaskDetail.detail/1, %{
        task: task,
        task_autosave: task_autosave,
        parent_picker: TaskParentPicker.empty(),
        cancel: "/projects/7",
        task_hierarchy:
          TaskHierarchy.load(
            TaskHierarchy.empty(),
            %Hierarchy{selected_task_id: task.id, root: hierarchy_node(task.id)}
          ),
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        task_move: TaskMove.empty()
      })

    document = LazyHTML.from_fragment(html)

    assert document |> LazyHTML.query("#task-modal-title") |> LazyHTML.text() |> String.trim() ==
             "Task #41"

    refute Enum.empty?(LazyHTML.query(document, "#task-form"))
    refute Enum.empty?(LazyHTML.query(document, "#task-save-status[data-state='saved']"))
    assert LazyHTML.text(LazyHTML.query(document, "#task-save-status")) =~ "Saved"
  end

  test "renders a progressive semantic Task tree with the current context expanded" do
    hierarchy = hierarchy(2)

    html =
      render_component(&TaskDetail.detail/1, %{
        task: hierarchy.root.children |> hd() |> Map.fetch!(:task),
        task_autosave: TaskAutosave.load(TaskAutosave.empty(), task(2), saved?: true),
        parent_picker: TaskParentPicker.empty(),
        cancel: "/projects/7",
        task_hierarchy: TaskHierarchy.load(TaskHierarchy.empty(), hierarchy),
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        task_move: TaskMove.empty()
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
      render_component(&TaskDetail.detail/1, %{
        task: task(7),
        task_autosave: TaskAutosave.load(TaskAutosave.empty(), task(7), saved?: true),
        parent_picker: TaskParentPicker.empty(),
        cancel: "/projects/7",
        task_hierarchy: TaskHierarchy.load(TaskHierarchy.empty(), hierarchy),
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        task_move: TaskMove.empty()
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

  defp hierarchy_node(id, children \\ []) do
    %HierarchyNode{task: task(id), location_path: [], children: children}
  end

  defp task(id),
    do: %Task{id: id, project_id: 7, title: "Task #{id}", status: :pending, priority: :none}
end
