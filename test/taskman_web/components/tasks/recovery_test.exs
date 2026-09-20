defmodule TaskmanWeb.Tasks.RecoveryTest do
  use ExUnit.Case, async: true
  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Tasks.{Hierarchy, HierarchyNode, Task}
  alias TaskmanWeb.ProjectLive.Tasks.{Autosave, Editing, Move, ParentPicker}
  alias TaskmanWeb.Tasks.Recovery

  test "frames only reused detail fields and recovery controls" do
    task = %Task{id: 4, project_id: 1, title: "Keep me", status: :pending, priority: :high}

    editing =
      Editing.State.open(
        Editing.State.empty(),
        task,
        Autosave.load(Autosave.empty(), task, saved?: true),
        %Hierarchy{
          selected_task_id: task.id,
          root: %HierarchyNode{task: task, location_path: [], children: []}
        }
      )

    document =
      render_component(&Recovery.shell/1, %{
        view: %{
          id: 4,
          copy_value: "Task title: Keep me",
          editing: editing,
          parent_picker: ParentPicker.empty(),
          task_move: Move.empty(),
          error: nil,
          reason: :destination_not_found
        }
      })
      |> LazyHTML.from_fragment()

    refute Enum.empty?(LazyHTML.query(document, "#task-recovery[data-copy-value]"))
    refute Enum.empty?(LazyHTML.query(document, "#task-recovery #task-title[value='Keep me']"))
    assert Enum.empty?(LazyHTML.query(document, "#task-form"))
    assert Enum.empty?(LazyHTML.query(document, "#task-recovery-form"))
    assert Enum.empty?(LazyHTML.query(document, "#task-recovery-destination"))
    refute Enum.empty?(LazyHTML.query(document, "#task-recovery-resume"))
    assert LazyHTML.text(LazyHTML.query(document, "#task-recovery-resume")) =~ "Try again"

    assert LazyHTML.text(LazyHTML.query(document, "#task-recovery-title")) =~
             "This task’s location is no longer available"

    assert Enum.empty?(LazyHTML.query(document, "#task-recovery-source"))
    assert Enum.empty?(LazyHTML.query(document, "#task-activity"))
    refute Enum.empty?(LazyHTML.query(document, "#task-recovery-discard"))
  end
end
