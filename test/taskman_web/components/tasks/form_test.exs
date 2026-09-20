defmodule TaskmanWeb.Tasks.FormTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Tasks.ParentPicker
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
        parent_picker: ParentPicker.empty(),
        field_states: %{"title" => :saving},
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

    title_status =
      LazyHTML.query(
        document,
        "#task-title-save-status[data-layout='label-end']:not([data-state])"
      )

    refute Enum.empty?(title_status)
    assert title_status |> LazyHTML.text() |> String.trim() == ""
  end

  test "renders all stable field-local lifecycle statuses immediately after their controls" do
    task = %Task{
      id: 41,
      project_id: 7,
      title: "Mine",
      description: "Draft",
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
        parent_picker: ParentPicker.empty(),
        field_states: %{
          "title" => :saving,
          "description" => :saved,
          "status" => :not_saved,
          "priority" => :failed,
          "due_at" => :saved
        }
      })
      |> LazyHTML.from_fragment()

    for {field, state, message} <- [
          {"title", "saving", "Saving…"},
          {"description", "saved", "Saved"},
          {"status", "not_saved", "Not saved"},
          {"priority", "failed", "Couldn’t save changes"},
          {"due-at", "saved", "Saved"}
        ] do
      selector = "#task-#{field}-save-status[aria-live='polite'][data-state='#{state}']"

      refute Enum.empty?(LazyHTML.query(document, selector))
      assert LazyHTML.text(LazyHTML.query(document, "#task-#{field}-save-status")) =~ message

      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 ".fieldset:has(#task-#{field}) + #task-#{field}-save-status"
               )
             )
    end

    assert Enum.empty?(LazyHTML.query(document, "#task-save-status"))
  end

  test "keeps an empty lifecycle slot on each field's label line" do
    task = %Task{
      id: 41,
      project_id: 7,
      title: "Mine",
      description: "Draft",
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
        parent_picker: ParentPicker.empty()
      })
      |> LazyHTML.from_fragment()

    for field <- ~w(title description status priority due-at) do
      selector =
        "[data-lifecycle-field='#{field}'] > .fieldset:has(#task-#{field}) + " <>
          "#task-#{field}-save-status[data-layout='label-end']:not([data-state])"

      status = LazyHTML.query(document, selector)

      refute Enum.empty?(status)
      assert status |> LazyHTML.text() |> String.trim() == ""
    end
  end
end
