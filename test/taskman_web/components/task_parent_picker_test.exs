defmodule TaskmanWeb.TaskParentPickerComponentTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Lists.TaskList
  alias Taskman.Tasks.{Task, TaskWithLocation}
  alias TaskmanWeb.{TaskParentPicker, TaskParentPickerComponent}

  test "renders an accessible combobox and stable candidate option IDs" do
    parent = %Task{id: 41, title: "Roadmap", project_id: 7}
    child = %Task{id: 42, title: "Launch", project_id: 7}

    state = %TaskParentPicker{
      mode: :create,
      query: "",
      selected_parent: nil,
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
      render_component(&TaskParentPickerComponent.parent_picker/1, %{picker: state})
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
               "#task-parent-search[role='combobox'][aria-controls='task-parent-results'][aria-expanded='true'][phx-hook='TaskmanWeb.TaskParentPickerComponent.TaskParentPickerKeyboard']"
             )
           )

    refute Enum.empty?(LazyHTML.query(document, "#task-parent-results[role='listbox']"))
    refute Enum.empty?(LazyHTML.query(document, "#task-parent-option-41[role='option']"))
    refute Enum.empty?(LazyHTML.query(document, "#task-parent-option-42[role='option']"))
    assert LazyHTML.text(LazyHTML.query(document, "#task-parent-option-41")) =~ "Planning"
  end

  test "exposes the keyboard active descendant and prevents option buttons from stealing focus" do
    state = %TaskParentPicker{
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
      render_component(&TaskParentPickerComponent.parent_picker/1, %{picker: state})
      |> LazyHTML.from_fragment()

    assert LazyHTML.query(
             document,
             "#task-parent-search[aria-activedescendant='task-parent-option-41']"
           ) != []

    assert LazyHTML.query(document, "#task-parent-option-41[data-active='true'][tabindex='-1']") !=
             []

    assert LazyHTML.query(document, "#task-parent-option-41[data-title='Roadmap']") != []

    assert File.read!("lib/taskman_web/components/task_parent_picker.ex") =~
             "event.stopPropagation()"

    assert File.read!("lib/taskman_web/components/task_parent_picker.ex") =~
             "this.pushEvent(\"task_parent_keydown\""

    source = File.read!("lib/taskman_web/components/task_parent_picker.ex")
    assert source =~ "this.onKeyup"
    assert source =~ "this.el.value ="
    assert source =~ "activeId === \"task-parent-clear\" ? \"\""
    assert source =~ "dataset.title"
  end

  test "renders No parent for edit mode and a selected create draft" do
    selected = %Task{id: 41, title: "Roadmap", project_id: 7}

    edit_state = %TaskParentPicker{mode: :edit, query: "", options: []}

    edit_document =
      render_component(&TaskParentPickerComponent.parent_picker/1, %{picker: edit_state})
      |> LazyHTML.from_fragment()

    refute Enum.empty?(
             LazyHTML.query(
               edit_document,
               "#task-parent-clear[phx-click='clear_task_parent'][type='button']"
             )
           )

    create_state = %TaskParentPicker{
      mode: :create,
      query: "Roadmap",
      selected_parent: selected,
      options: []
    }

    create_document =
      render_component(&TaskParentPickerComponent.parent_picker/1, %{picker: create_state})
      |> LazyHTML.from_fragment()

    refute Enum.empty?(LazyHTML.query(create_document, "#task-parent-clear"))
    refute Enum.empty?(LazyHTML.query(create_document, "#task-parent-selected"))
    assert LazyHTML.text(LazyHTML.query(create_document, "#task-parent-selected")) =~ "Roadmap"
  end

  test "marks the combobox invalid and links a draft-preserving error" do
    rejected = %Task{id: 41, title: "Rejected parent", project_id: 7}

    state = %TaskParentPicker{
      mode: :edit,
      query: rejected.title,
      selected_parent: rejected,
      options_open?: false,
      error: "That parent would create a cycle."
    }

    document =
      render_component(&TaskParentPickerComponent.parent_picker/1, %{picker: state})
      |> LazyHTML.from_fragment()

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-parent-search[aria-invalid='true'][aria-describedby='task-parent-error'][value='Rejected parent']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-parent-error[role='alert']"
             )
           )

    assert Enum.empty?(LazyHTML.query(document, "#task-parent-results"))
    assert LazyHTML.text(LazyHTML.query(document, "#task-parent-selected")) =~ "Rejected parent"
  end
end
