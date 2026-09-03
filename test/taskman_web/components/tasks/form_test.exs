defmodule TaskmanWeb.Tasks.FormTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias TaskmanWeb.TaskParentPicker
  alias TaskmanWeb.Tasks.Form

  test "renders accessible ordinary-field conflict notices with stable resolution actions" do
    task = %Task{
      id: 41,
      project_id: 7,
      title: "Mine",
      description: "",
      status: :pending,
      priority: :none
    }

    document =
      render_component(&Form.form/1, %{
        form: task |> Tasks.change_task() |> to_form(),
        mode: :edit,
        change: "autosave_task",
        submit: "submit_task_edit",
        cancel: "/projects/7",
        parent_picker: TaskParentPicker.empty(),
        conflicts: %{"title" => "Latest title"}
      })
      |> LazyHTML.from_fragment()

    refute Enum.empty?(LazyHTML.query(document, "#task-title-conflict[role='alert']"))
    assert LazyHTML.text(LazyHTML.query(document, "#task-title-conflict")) =~ "Latest title"

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#use-latest-title[phx-click='resolve_task_conflict'][phx-value-field='title'][phx-value-resolution='use_latest']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#keep-mine-title[phx-click='resolve_task_conflict'][phx-value-field='title'][phx-value-resolution='keep_mine']"
             )
           )
  end
end
