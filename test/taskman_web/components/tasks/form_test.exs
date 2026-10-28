defmodule TaskmanWeb.Tasks.FormTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Tasks.ParentPicker
  alias TaskmanWeb.Tasks.Form

  test "due enhancement keeps the text field outside its isolated staged popup" do
    for mode <- [:new, :edit], value <- [nil, ~N[1960-05-06 09:30:42], ~N[0999-05-06 09:30:00]] do
      document =
        render_component(&Form.form/1, %{
          form: %Task{id: 41, due_at: value} |> Tasks.change_task() |> to_form(),
          mode: mode,
          change: "autosave_task",
          cancel: "/projects/7",
          parent_picker: ParentPicker.empty()
        })
        |> LazyHTML.from_fragment()

      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 "#task-due-at[type='text'][name='task[due_at]'][placeholder='DD.MM.YYYY HH:mm'][aria-describedby='task-due-at-format']:not([readonly])"
               )
             )

      picker =
        LazyHTML.query(
          document,
          "#task-due-at-picker[phx-hook='DateTimePicker'][phx-update='ignore'][data-context-id='41']"
        )

      refute Enum.empty?(picker)
      assert Enum.empty?(LazyHTML.query(picker, "#task-due-at"))

      refute Enum.empty?(
               LazyHTML.query(
                 picker,
                 "#task-due-at-trigger[type='button'][hidden][aria-expanded='false']"
               )
             )

      refute Enum.empty?(
               LazyHTML.query(picker, "#task-due-at-popup[popover='manual'][role='dialog']")
             )

      for action <- ~w(apply cancel clear) do
        refute Enum.empty?(LazyHTML.query(picker, "#task-due-at-#{action}[type='button']"))
      end
    end
  end

  test "recovery retains the readable day-first due field" do
    document =
      render_component(&Form.form/1, %{
        form: %Task{id: 41, due_at: ~N[2030-05-06 09:30:00]} |> Tasks.change_task() |> to_form(),
        mode: :edit,
        change: "autosave_task",
        cancel: "/projects/7",
        recovery?: true,
        parent_picker: ParentPicker.empty()
      })
      |> LazyHTML.from_fragment()

    refute Enum.empty?(
             LazyHTML.query(document, "#task-due-at[readonly][value='06.05.2030 09:30']")
           )
  end

  test "ordinary pickers retain native submission and expose server values for enhancement" do
    for mode <- [:new, :edit] do
      document =
        render_component(&Form.form/1, %{
          form: %Task{status: :in_review, priority: :high} |> Tasks.change_task() |> to_form(),
          mode: mode,
          change: "autosave_task",
          cancel: "/projects/7",
          parent_picker: ParentPicker.empty()
        })
        |> LazyHTML.from_fragment()

      for {field, value, label} <- [
            {"status", "in_review", "Status"},
            {"priority", "high", "Priority"}
          ] do
        refute Enum.empty?(
                 LazyHTML.query(
                   document,
                   "select#task-#{field}[name='task[#{field}]']:not([disabled]) option[value='#{value}'][selected]"
                 )
               )

        picker =
          LazyHTML.query(
            document,
            "#task-#{field}-picker[phx-update='ignore'][data-value='#{value}'][hidden]"
          )

        refute Enum.empty?(picker)

        refute Enum.empty?(
                 LazyHTML.query(
                   picker,
                   "#task-#{field}-trigger[aria-haspopup='listbox'][aria-expanded='false'][aria-labelledby='task-#{field}-label']"
                 )
               )

        assert LazyHTML.text(LazyHTML.query(picker, "#task-#{field}-label")) == label
        refute Enum.empty?(LazyHTML.query(picker, "#task-#{field}-options[role='listbox']"))
        assert Enum.empty?(LazyHTML.query(picker, "select"))
      end
    end
  end

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
