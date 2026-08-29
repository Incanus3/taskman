defmodule TaskmanWeb.TaskDetailTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Tasks.Task
  alias TaskmanWeb.{TaskAutosave, TaskDetail, TaskMove}

  test "renders the Task form and save status from one autosave state" do
    task = %Task{id: 41, project_id: 7, title: "Launch", status: :pending, priority: :none}
    task_autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: true)

    html =
      render_component(&TaskDetail.detail/1, %{
        task: task,
        task_autosave: task_autosave,
        cancel: "/projects/7",
        task_move: TaskMove.empty()
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(LazyHTML.query(document, "#task-form"))
    refute Enum.empty?(LazyHTML.query(document, "#task-save-status[data-state='saved']"))
    assert LazyHTML.text(LazyHTML.query(document, "#task-save-status")) =~ "Saved"
  end
end
