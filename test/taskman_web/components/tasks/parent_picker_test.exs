defmodule TaskmanWeb.Tasks.ParentPickerTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Lists.TaskList
  alias Taskman.Tasks.{Task, TaskWithLocation}
  alias TaskmanWeb.ProjectLive.Tasks.ParentPicker
  alias TaskmanWeb.Tasks.ParentPicker, as: ParentPickerComponent

  test "renders an accessible combobox and stable candidate option IDs" do
    parent = %Task{id: 41, title: "Roadmap", project_id: 7}
    child = %Task{id: 42, title: "Launch", project_id: 7}

    state = %ParentPicker{
      mode: :create,
      query: "",
      selected_parent: parent,
      options_open?: true,
      options: [
        %TaskWithLocation{
          task: parent,
          location_path: [%TaskList{id: 11, name: "Planning"}]
        },
        %TaskWithLocation{task: child, location_path: []}
      ]
    }

    document =
      render_component(&ParentPickerComponent.parent_picker/1, %{picker: state})
      |> LazyHTML.from_fragment()

    refute Enum.empty?(LazyHTML.query(document, "#task-parent-picker"))

    refute Enum.empty?(
             LazyHTML.query(document, "#task-parent-picker .fieldset #task-parent-search")
           )

    refute Enum.empty?(
             LazyHTML.query(document, "#task-parent-picker .fieldset > label > span.label")
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-parent-search[value=''][role='combobox'][aria-controls='task-parent-results'][aria-expanded='true'][phx-hook='TaskmanWeb.Tasks.ParentPicker.TaskParentPickerKeyboard']"
             )
           )

    refute Enum.empty?(LazyHTML.query(document, "#task-parent-results[role='listbox']"))
    assert Enum.empty?(LazyHTML.query(document, "#task-parent-trigger"))
    assert Enum.empty?(LazyHTML.query(document, "#task-parent-selected"))
    refute Enum.empty?(LazyHTML.query(document, "#task-parent-option-41[role='option']"))
    refute Enum.empty?(LazyHTML.query(document, "#task-parent-option-42[role='option']"))
    assert LazyHTML.text(LazyHTML.query(document, "#task-parent-option-41")) =~ "Planning"

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-parent-picker[phx-click-away='close_task_parent_options']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "button[aria-label='Close parent Task options'][phx-click='toggle_task_parent_options']"
             )
           )
  end

  test "closed picker renders an open-options toggle without click-away dismissal" do
    parent = %Task{id: 41, title: "Roadmap", project_id: 7}

    selected_document =
      render_component(&ParentPickerComponent.parent_picker/1, %{
        picker: %ParentPicker{mode: :create, selected_parent: parent, query: "Roadmap"}
      })
      |> LazyHTML.from_fragment()

    assert Enum.empty?(
             LazyHTML.query(
               selected_document,
               "#task-parent-picker[phx-click-away='close_task_parent_options']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               selected_document,
               "#task-parent-trigger[aria-label='Open parent Task options'][phx-click='open_task_parent_options']"
             )
           )

    assert Enum.empty?(LazyHTML.query(selected_document, "#task-parent-search"))
    assert Enum.empty?(LazyHTML.query(selected_document, "#task-parent-selected"))
    assert LazyHTML.text(LazyHTML.query(selected_document, "#task-parent-trigger")) =~ "Roadmap"
    refute LazyHTML.text(LazyHTML.query(selected_document, "#task-parent-trigger")) =~ "Task #41"

    no_parent_document =
      render_component(&ParentPickerComponent.parent_picker/1, %{
        picker: %ParentPicker{mode: :create}
      })
      |> LazyHTML.from_fragment()

    assert LazyHTML.text(LazyHTML.query(no_parent_document, "#task-parent-trigger")) =~
             "No parent"
  end

  test "exposes the keyboard active descendant and prevents option buttons from stealing focus" do
    state = %ParentPicker{
      mode: :edit,
      options_open?: true,
      active_option_id: "task-parent-option-41",
      options: [
        %TaskWithLocation{
          task: %Task{id: 41, title: "Roadmap", project_id: 7},
          location_path: []
        }
      ]
    }

    document =
      render_component(&ParentPickerComponent.parent_picker/1, %{picker: state})
      |> LazyHTML.from_fragment()

    assert LazyHTML.query(
             document,
             "#task-parent-search[aria-activedescendant='task-parent-option-41']"
           ) != []

    assert LazyHTML.query(document, "#task-parent-option-41[data-active='true'][tabindex='-1']") !=
             []

    assert LazyHTML.query(document, "#task-parent-option-41[data-title='Roadmap']") != []

    assert File.read!("lib/taskman_web/components/tasks/parent_picker.ex") =~
             "event.stopPropagation()"

    assert File.read!("lib/taskman_web/components/tasks/parent_picker.ex") =~
             "this.pushEvent(\"task_parent_keydown\""

    source = File.read!("lib/taskman_web/components/tasks/parent_picker.ex")
    assert source =~ "this.el.value ="
    assert source =~ "activeId === \"task-parent-clear\" ? \"\""
    assert source =~ "dataset.title"
  end

  test "renders No parent only inside an open eligible picker" do
    selected = %Task{id: 41, title: "Roadmap", project_id: 7}

    closed_edit_state = %ParentPicker{mode: :edit, query: "", options: []}

    closed_edit_document =
      render_component(&ParentPickerComponent.parent_picker/1, %{picker: closed_edit_state})
      |> LazyHTML.from_fragment()

    assert Enum.empty?(LazyHTML.query(closed_edit_document, "#task-parent-clear"))

    open_edit_document =
      render_component(&ParentPickerComponent.parent_picker/1, %{
        picker: %{closed_edit_state | options_open?: true}
      })
      |> LazyHTML.from_fragment()

    refute Enum.empty?(
             LazyHTML.query(
               open_edit_document,
               "#task-parent-results #task-parent-clear[role='option'][phx-click='clear_task_parent']"
             )
           )

    closed_create_state = %ParentPicker{
      mode: :create,
      query: "Roadmap",
      selected_parent: selected,
      options: []
    }

    closed_create_document =
      render_component(&ParentPickerComponent.parent_picker/1, %{
        picker: closed_create_state
      })
      |> LazyHTML.from_fragment()

    assert Enum.empty?(LazyHTML.query(closed_create_document, "#task-parent-clear"))
    refute Enum.empty?(LazyHTML.query(closed_create_document, "#task-parent-trigger"))

    assert LazyHTML.text(LazyHTML.query(closed_create_document, "#task-parent-trigger")) =~
             "Roadmap"

    open_create_document =
      render_component(&ParentPickerComponent.parent_picker/1, %{
        picker: %{closed_create_state | options_open?: true}
      })
      |> LazyHTML.from_fragment()

    refute Enum.empty?(
             LazyHTML.query(
               open_create_document,
               "#task-parent-results #task-parent-clear[role='option'][phx-click='clear_task_parent']"
             )
           )
  end

  test "marks the combobox invalid and links a draft-preserving error" do
    rejected = %Task{id: 41, title: "Rejected parent", project_id: 7}

    state = %ParentPicker{
      mode: :edit,
      query: rejected.title,
      selected_parent: rejected,
      options_open?: false,
      error: "That parent would create a cycle."
    }

    document =
      render_component(&ParentPickerComponent.parent_picker/1, %{picker: state})
      |> LazyHTML.from_fragment()

    assert Enum.empty?(LazyHTML.query(document, "#task-parent-search"))

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-parent-error[role='alert']"
             )
           )

    assert Enum.empty?(LazyHTML.query(document, "#task-parent-results"))
    assert LazyHTML.text(LazyHTML.query(document, "#task-parent-trigger")) =~ "Rejected parent"
  end

  test "renders an accessible parent conflict notice with resolution actions" do
    state = %ParentPicker{
      mode: :edit,
      selected_parent: %Task{id: 41, title: "Mine", project_id: 7},
      conflict_parent: %Task{id: 42, title: "Latest parent", project_id: 7}
    }

    document =
      render_component(&ParentPickerComponent.parent_picker/1, %{picker: state})
      |> LazyHTML.from_fragment()

    refute Enum.empty?(LazyHTML.query(document, "#task-parent-conflict[role='alert']"))
    assert LazyHTML.text(LazyHTML.query(document, "#task-parent-conflict")) =~ "Latest parent"

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#use-latest-parent_task_id[phx-click='resolve_task_parent_conflict'][phx-value-resolution='use_latest']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#keep-mine-parent_task_id[phx-click='resolve_task_parent_conflict'][phx-value-resolution='keep_mine']"
             )
           )
  end
end
