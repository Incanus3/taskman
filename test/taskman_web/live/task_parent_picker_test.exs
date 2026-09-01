defmodule TaskmanWeb.TaskParentPickerTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.TaskWithLocation
  alias TaskmanWeb.TaskParentPicker

  test "empty state and create initialization start with no parent" do
    project = project_fixture(%{})

    assert %TaskParentPicker{} = empty = TaskParentPicker.empty()
    assert empty.query == ""
    assert empty.options == []
    assert empty.selected_parent == nil
    refute empty.options_open?
    assert empty.error == nil

    state = TaskParentPicker.open_create(empty, project, nil)

    assert state.mode == :create
    assert state.current_task == nil
    assert state.selected_parent == nil
    assert state.query == ""
    assert state.options == []
    refute state.options_open?
  end

  test "create initialization can preselect a parent without opening options" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})

    state = TaskParentPicker.open_create(TaskParentPicker.empty(), project, parent)

    assert state.mode == :create
    assert state.selected_parent == parent
    assert state.query == "Parent"
    refute state.options_open?
  end

  test "edit initialization resolves the persisted parent and excludes current descendants" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    task = task_fixture(project, %{title: "Task"}, parent: parent)
    _child = task_fixture(project, %{title: "Child"}, parent: task)

    state = TaskParentPicker.open_edit(TaskParentPicker.empty(), project, task)

    assert state.mode == :edit
    assert state.current_task == task
    assert state.selected_parent == parent
    assert state.query == "Parent"

    searched = TaskParentPicker.search(state, project, "")
    assert searched.options_open?
    assert [%TaskWithLocation{task: ^parent}] = searched.options
  end

  test "empty, exact-id, and title searches return TaskWithLocation candidates" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    first = task_fixture(project, %{title: "First"})
    exact = task_fixture(project, planning, %{title: "Roadmap"})
    title_match = task_fixture(project, %{title: "Roadmap #{exact.id} follow-up"})

    state = TaskParentPicker.open_create(TaskParentPicker.empty(), project, nil)

    empty = TaskParentPicker.search(state, project, "  ")

    assert Enum.map(empty.options, & &1.task.id) == [first.id, exact.id, title_match.id]

    exact_search = TaskParentPicker.search(state, project, Integer.to_string(exact.id))

    assert [%TaskWithLocation{task: ^exact}, %TaskWithLocation{task: ^title_match}] =
             exact_search.options

    title_search = TaskParentPicker.search(state, project, "  ROADMAP  ")

    assert Enum.map(title_search.options, & &1.task.id) == [exact.id, title_match.id]
    assert hd(title_search.options).location_path == [planning]
  end

  test "keeps the selected parent visible independently of the result page and query" do
    project = project_fixture(%{})

    for index <- 1..20 do
      task_fixture(project, %{title: "Earlier #{index}"})
    end

    selected = task_fixture(project, %{title: "Selected"})

    state =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_create(project, selected)
      |> TaskParentPicker.open_options(project)

    assert length(state.options) == 20
    assert hd(state.options).task == selected

    searched = TaskParentPicker.search(state, project, "No matching title")

    assert [%TaskWithLocation{task: ^selected}] = searched.options
  end

  test "opening options loads candidates without clearing a rejected draft error" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    _earlier = task_fixture(project, %{title: "Earlier"})

    failed =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task_fixture(project, %{title: "Task"}))
      |> TaskParentPicker.select_draft(project, parent.id)
      |> TaskParentPicker.reject_draft("That parent would create a cycle.")

    reopened = TaskParentPicker.open_options(failed, project)

    assert reopened.options_open?
    assert reopened.selected_parent == parent
    assert reopened.query == ""
    assert hd(reopened.options).task == parent
    assert reopened.error == "That parent would create a cycle."
  end

  test "toggle and outside dismissal close options without changing the draft" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})

    open =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_create(project, parent)
      |> TaskParentPicker.open_options(project)
      |> TaskParentPicker.reject_draft("Keep this error")
      |> TaskParentPicker.open_options(project)

    toggled = TaskParentPicker.toggle_options(open, project)

    refute toggled.options_open?
    assert toggled.selected_parent == parent
    assert toggled.query == "Parent"
    assert toggled.error == "Keep this error"

    reopened = TaskParentPicker.toggle_options(toggled, project)
    assert reopened.options_open?
    assert reopened.query == ""

    dismissed = TaskParentPicker.close_options(reopened)

    refute dismissed.options_open?
    assert dismissed.selected_parent == parent
    assert dismissed.query == "Parent"
    assert dismissed.error == "Keep this error"
  end

  test "selection closes options, labels the draft, and only a different draft clears errors" do
    project = project_fixture(%{})
    first = task_fixture(project, %{title: "First"})
    second = task_fixture(project, %{title: "Second"})

    state =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_create(project, nil)
      |> TaskParentPicker.search(project, "")
      |> TaskParentPicker.reject_draft("stale")

    same = TaskParentPicker.select_draft(state, project, first.id)

    assert same.selected_parent == first
    assert same.query == "First"
    refute same.options_open?
    assert same.error == nil

    rejected =
      same
      |> TaskParentPicker.reject_draft("stale")
      |> TaskParentPicker.select_draft(project, first.id)

    assert rejected.selected_parent == first
    assert rejected.error == "stale"

    changed = TaskParentPicker.select_draft(rejected, project, second.id)
    assert changed.selected_parent == second
    assert changed.error == nil
  end

  test "keyboard navigation tracks an active option and selects it on Enter" do
    project = project_fixture(%{})
    first = task_fixture(project, %{title: "First"})
    second = task_fixture(project, %{title: "Second"})

    state =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_create(project, nil)
      |> TaskParentPicker.search(project, "")

    assert {:move, down} = TaskParentPicker.keydown(state, "ArrowDown")
    assert TaskParentPicker.active_option_id(down) == "task-parent-option-#{first.id}"

    assert TaskParentPicker.active_option_id(TaskParentPicker.search(down, project, "")) ==
             "task-parent-option-#{first.id}"

    assert {:move, second_active} = TaskParentPicker.keydown(down, "ArrowDown")
    assert TaskParentPicker.active_option_id(second_active) == "task-parent-option-#{second.id}"

    assert {:move, up} = TaskParentPicker.keydown(second_active, "ArrowUp")
    assert TaskParentPicker.active_option_id(up) == "task-parent-option-#{first.id}"

    assert {:select, selected_id} = TaskParentPicker.keydown(second_active, "Enter")
    assert selected_id == second.id
  end

  test "keyboard Escape closes the list and Enter without an active option is inert" do
    project = project_fixture(%{})
    _parent = task_fixture(project, %{title: "Parent"})

    state =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_create(project, nil)
      |> TaskParentPicker.search(project, "")

    assert :ignore = TaskParentPicker.keydown(state, "Enter")
    assert {:close, closed} = TaskParentPicker.keydown(state, "Escape")
    refute closed.options_open?
    assert TaskParentPicker.active_option_id(closed) == nil
  end

  test "keyboard navigation exposes No parent as an option in edit mode" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    task = task_fixture(project, %{title: "Task"}, parent: parent)

    state =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task)
      |> TaskParentPicker.search(project, "")

    assert {:move, moved} = TaskParentPicker.keydown(state, "ArrowDown")
    assert TaskParentPicker.active_option_id(moved) == "task-parent-clear"
    assert {:select, nil} = TaskParentPicker.keydown(moved, "Enter")
  end

  test "clearing a selected draft exposes No parent and clears the error" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})

    state =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_create(project, parent)
      |> TaskParentPicker.reject_draft("stale")

    cleared = TaskParentPicker.clear_draft(state)

    assert TaskParentPicker.selected_parent(cleared) == nil
    assert cleared.query == ""
    refute cleared.options_open?
    assert cleared.error == nil
  end

  test "save_edit persists a replacement parent and refreshes the selected draft" do
    project = project_fixture(%{})
    original_parent = task_fixture(project, %{title: "Original"})
    replacement = task_fixture(project, %{title: "Replacement"})
    task = task_fixture(project, %{title: "Task"}, parent: original_parent)

    state =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task)
      |> TaskParentPicker.select_draft(project, replacement.id)

    assert {:ok, saved, updated} = TaskParentPicker.save_edit(state, project, task)
    assert updated.parent_task_id == replacement.id
    assert saved.selected_parent == replacement
    assert saved.current_task.parent_task_id == replacement.id
    refute saved.options_open?
    assert saved.error == nil
  end

  test "save_edit keeps a rejected parent draft and maps a cycle error" do
    project = project_fixture(%{})
    original_parent = task_fixture(project, %{title: "Original"})
    task = task_fixture(project, %{title: "Task"}, parent: original_parent)
    rejected_parent = task_fixture(project, %{title: "Descendant"}, parent: task)

    state =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task)
      |> TaskParentPicker.select_draft(project, rejected_parent.id)

    assert {:error, failed, %Ecto.Changeset{}} =
             TaskParentPicker.save_edit(state, project, task)

    persisted = Tasks.get_task_for_project(project, task.id)

    assert failed.options_open? == false
    assert failed.selected_parent.id == rejected_parent.id
    assert failed.error == "That parent would create a cycle."
    assert persisted.parent_task_id == original_parent.id

    reopened = TaskParentPicker.open_options(failed, project)
    assert reopened.error == failed.error
    assert reopened.selected_parent.id == rejected_parent.id

    changed = TaskParentPicker.select_draft(reopened, project, original_parent.id)
    assert changed.error == nil
  end

  test "save_edit maps stale parent failures and preserves the rejected draft" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Task"})
    rejected_parent = task_fixture(project, %{title: "Removed parent"})

    state =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task)
      |> TaskParentPicker.select_draft(project, rejected_parent.id)

    Taskman.Repo.delete!(rejected_parent)

    assert {:error, failed, :not_found} = TaskParentPicker.save_edit(state, project, task)
    assert failed.error == "That parent Task is no longer available."
    assert failed.selected_parent.id == rejected_parent.id
    refute failed.options_open?
  end

  test "reconciles an external parent change without resetting an open search" do
    project = project_fixture(%{})
    first_parent = task_fixture(project, %{title: "First parent"})
    second_parent = task_fixture(project, %{title: "Second parent"})
    task = task_fixture(project, %{title: "Task"}, parent: first_parent)

    picker =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task)
      |> TaskParentPicker.search(project, "Second")

    assert {:ok, latest} = Tasks.update_task(project, task, %{}, parent: second_parent)
    reconciled = TaskParentPicker.reconcile(picker, project, latest)

    assert reconciled.current_task == latest
    assert reconciled.selected_parent == second_parent
    assert reconciled.query == "Second"
    assert reconciled.options_open?
    assert reconciled.conflict_parent == nil
  end

  test "reconcile rebuilds open candidate options from canonical Tasks while preserving the draft" do
    project = project_fixture(%{})
    selected_parent = task_fixture(project, %{title: "Roadmap"})
    task = task_fixture(project, %{title: "Task"}, parent: selected_parent)

    picker =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task)
      |> TaskParentPicker.select_draft(project, selected_parent.id)
      |> TaskParentPicker.search(project, "Road")

    external_candidate = task_fixture(project, %{title: "Roadmap follow-up"})
    latest = Tasks.get_task_for_project(project, task.id)
    reconciled = TaskParentPicker.reconcile(picker, project, latest)

    assert reconciled.current_task == latest
    assert reconciled.selected_parent == selected_parent
    assert reconciled.query == "Road"
    assert reconciled.options_open?
    assert Enum.any?(reconciled.options, &(&1.task.id == external_candidate.id))
  end

  test "retains a local parent selection when a stale save conflicts and resolves it inline" do
    project = project_fixture(%{})
    initial_parent = task_fixture(project, %{title: "Initial"})
    mine = task_fixture(project, %{title: "Mine"})
    latest_parent = task_fixture(project, %{title: "Latest"})
    task = task_fixture(project, %{title: "Task"}, parent: initial_parent)

    picker =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task)
      |> TaskParentPicker.select_draft(project, mine.id)

    assert {:ok, latest} = Tasks.update_task(project, task, %{}, parent: latest_parent)

    assert {:conflict, conflicted, ^latest} = TaskParentPicker.save_edit(picker, project, task)

    assert conflicted.current_task == latest
    assert conflicted.selected_parent == mine
    assert conflicted.conflict_parent == latest_parent

    assert {:ok, used_latest, ^latest} =
             TaskParentPicker.resolve_conflict(conflicted, project, :use_latest)

    assert used_latest.selected_parent == latest_parent
    assert used_latest.conflict_parent == nil
    assert Tasks.get_task_for_project(project, task.id).parent_task_id == latest_parent.id
  end

  test "keep mine retries the latest parent baseline and clears an uncontested conflict" do
    project = project_fixture(%{})
    initial_parent = task_fixture(project, %{title: "Initial"})
    mine = task_fixture(project, %{title: "Mine"})
    latest_parent = task_fixture(project, %{title: "Latest"})
    task = task_fixture(project, %{title: "Task"}, parent: initial_parent)

    picker =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task)
      |> TaskParentPicker.select_draft(project, mine.id)

    assert {:ok, latest} = Tasks.update_task(project, task, %{}, parent: latest_parent)
    assert {:conflict, conflicted, ^latest} = TaskParentPicker.save_edit(picker, project, task)

    assert {:ok, resolved, saved} =
             TaskParentPicker.resolve_conflict(conflicted, project, :keep_mine)

    assert saved.parent_task_id == mine.id
    assert resolved.current_task == saved
    assert resolved.selected_parent == mine
    assert resolved.conflict_parent == nil
    refute resolved.parent_conflicted?
    assert Tasks.get_task_for_project(project, task.id).parent_task_id == mine.id
  end

  test "keep mine remains conflicted when the latest parent changes during conflict resolution" do
    project = project_fixture(%{})
    initial_parent = task_fixture(project, %{title: "Initial"})
    mine = task_fixture(project, %{title: "Mine"})
    latest_parent = task_fixture(project, %{title: "Latest"})
    later_parent = task_fixture(project, %{title: "Later"})
    task = task_fixture(project, %{title: "Task"}, parent: initial_parent)

    picker =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task)
      |> TaskParentPicker.select_draft(project, mine.id)

    assert {:ok, latest} = Tasks.update_task(project, task, %{}, parent: latest_parent)
    assert {:conflict, conflicted, ^latest} = TaskParentPicker.save_edit(picker, project, task)

    assert {:ok, raced} = Tasks.update_task(project, latest, %{}, parent: later_parent)

    assert {:conflict, still_conflicted, ^raced} =
             TaskParentPicker.resolve_conflict(conflicted, project, :keep_mine)

    assert still_conflicted.current_task == raced
    assert still_conflicted.selected_parent == mine
    assert still_conflicted.conflict_parent == later_parent
    assert Tasks.get_task_for_project(project, task.id).parent_task_id == later_parent.id
  end
end
