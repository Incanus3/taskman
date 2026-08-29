defmodule TaskmanWeb.TaskAutosaveTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Repo, Tasks}
  alias TaskmanWeb.TaskAutosave

  setup do
    previous = Application.get_env(:taskman, :task_autosave_delay_ms)
    Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)

    on_exit(fn ->
      case previous do
        nil -> Application.delete_env(:taskman, :task_autosave_delay_ms)
        value -> Application.put_env(:taskman, :task_autosave_delay_ms, value)
      end
    end)

    :ok
  end

  test "load and clear keep the sequence monotonic across edit lifecycles" do
    project = project_fixture(%{})
    first = task_fixture(project, %{title: "First"})
    second = task_fixture(project, %{title: "Second"})

    autosave = TaskAutosave.load(TaskAutosave.empty(), first, saved?: false)
    assert autosave.form[:title].value == "First"
    assert autosave.save_state == :idle

    assert {:schedule, autosave, ^first, 60_000, {:autosave_task_field, first_id, "title", 1}} =
             TaskAutosave.change(autosave, project, first, %{"title" => "Changed"}, "title")

    assert first_id == first.id

    cleared = TaskAutosave.clear(autosave)
    assert cleared.sequence == 1
    assert cleared.form == nil
    assert cleared.draft == %{}
    assert cleared.dirty_fields == MapSet.new()
    assert cleared.revisions == %{}
    assert cleared.save_state == :idle

    reloaded = TaskAutosave.load(cleared, second, saved?: true)
    assert reloaded.sequence == 1
    assert reloaded.form[:title].value == "Second"
    assert reloaded.saved?
    assert reloaded.save_state == :saved
  end

  test "save-state messages preserve the existing copy" do
    autosave = TaskAutosave.empty()

    assert TaskAutosave.message(%{autosave | save_state: :idle}) == "Autosaves changes"
    assert TaskAutosave.message(%{autosave | save_state: :saving}) == "Saving…"
    assert TaskAutosave.message(%{autosave | save_state: :saved}) == "Saved"
    assert TaskAutosave.message(%{autosave | save_state: :not_saved}) == "Not saved"

    assert TaskAutosave.message(%{autosave | save_state: :failed}) ==
             "Couldn’t save changes"
  end

  test "debounced changes return effects and only the current revision persists" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: false)

    assert {:schedule, autosave, ^task, 60_000, message} =
             TaskAutosave.change(autosave, project, task, %{"title" => "After"}, "title")

    assert message == {:autosave_task_field, task.id, "title", 1}
    assert autosave.save_state == :saving
    assert autosave.dirty_fields == MapSet.new(["title"])

    assert {:ignored, ^autosave, ^task} =
             TaskAutosave.handle_scheduled_save(
               autosave,
               project,
               task,
               task.id,
               "title",
               0
             )

    assert Tasks.get_task_for_project(project, task.id).title == "Before"

    assert {:ok, autosave, saved_task} =
             TaskAutosave.handle_scheduled_save(
               autosave,
               project,
               task,
               task.id,
               "title",
               1
             )

    assert saved_task.title == "After"
    assert autosave.dirty_fields == MapSet.new()
    assert autosave.save_state == :saved
  end

  test "an unsupported target is ignored without changing state" do
    project = project_fixture(%{})
    task = task_fixture(project, %{})
    autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: false)

    assert {:ignored, ^autosave, ^task} =
             TaskAutosave.change(autosave, project, task, %{"project_id" => "9"}, "project_id")
  end

  test "an invalid debounced field is not scheduled" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: false)

    assert {:ok, autosave, ^task} =
             TaskAutosave.change(autosave, project, task, %{"title" => ""}, "title")

    assert autosave.sequence == 0
    assert autosave.save_state == :not_saved
    assert autosave.dirty_fields == MapSet.new(["title"])
  end

  test "a zero delay is returned as an explicit schedule effect" do
    Application.put_env(:taskman, :task_autosave_delay_ms, 0)
    on_exit(fn -> Application.put_env(:taskman, :task_autosave_delay_ms, 60_000) end)

    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: false)

    assert {:schedule, _autosave, ^task, 0, {:autosave_task_field, task_id, "title", 1}} =
             TaskAutosave.change(autosave, project, task, %{"title" => "After"}, "title")

    assert task_id == task.id
  end

  test "an immediate field persists while an unrelated invalid draft remains visible" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})
    autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: false)

    assert {:ok, autosave, ^task} =
             TaskAutosave.change(
               autosave,
               project,
               task,
               %{"title" => "", "status" => "pending"},
               "title"
             )

    assert {:ok, autosave, updated_task} =
             TaskAutosave.change(
               autosave,
               project,
               task,
               %{"title" => "", "status" => "in_review"},
               "status"
             )

    assert updated_task.status == :in_review
    assert updated_task.title == "Before"
    assert autosave.save_state == :not_saved
    assert autosave.form[:title].value == ""
  end

  test "flush persists valid dirty fields and retains invalid fields" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", description: "Old"})
    autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: false)

    assert {:schedule, autosave, ^task, 60_000, _message} =
             TaskAutosave.change(
               autosave,
               project,
               task,
               %{"title" => "Before", "description" => "New"},
               "description"
             )

    assert {:ok, autosave, ^task} =
             TaskAutosave.change(
               autosave,
               project,
               task,
               %{"title" => "", "description" => "New"},
               "title"
             )

    assert {:ok, autosave, updated_task} = TaskAutosave.flush(autosave, project, task)
    assert updated_task.title == "Before"
    assert updated_task.description == "New"
    assert autosave.dirty_fields == MapSet.new(["title"])
    assert autosave.save_state == :not_saved
  end

  test "flush reports aggregate failure while retaining successful partial writes" do
    Ecto.Adapters.SQL.query!(Repo, "ALTER TABLE tasks DROP CONSTRAINT tasks_status_check")

    Ecto.Adapters.SQL.query!(
      Repo,
      "ALTER TABLE tasks ADD CONSTRAINT tasks_status_check CHECK (status <> 'in_review')"
    )

    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})
    autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: false)

    assert {:schedule, autosave, ^task, 60_000, _message} =
             TaskAutosave.change(
               autosave,
               project,
               task,
               %{"title" => "Valid alongside failure", "status" => "pending"},
               "title"
             )

    assert {:ok, autosave, ^task} =
             TaskAutosave.change(
               autosave,
               project,
               task,
               %{"title" => "Valid alongside failure", "status" => "in_review"},
               "status"
             )

    assert autosave.save_state == :failed

    assert {:error, autosave, partially_updated_task} =
             TaskAutosave.flush(autosave, project, task)

    assert partially_updated_task.title == "Valid alongside failure"
    assert partially_updated_task.status == :pending
    assert autosave.dirty_fields == MapSet.new(["status"])
    assert autosave.draft["status"] == "in_review"
    assert autosave.form[:status].value == :in_review
    assert autosave.save_state == :failed
    assert autosave.save_failed?
  end

  test "persistence failure takes precedence over an invalid complete form" do
    Ecto.Adapters.SQL.query!(Repo, "ALTER TABLE tasks DROP CONSTRAINT tasks_status_check")

    Ecto.Adapters.SQL.query!(
      Repo,
      "ALTER TABLE tasks ADD CONSTRAINT tasks_status_check CHECK (status <> 'in_review')"
    )

    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})
    autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: false)

    assert {:ok, autosave, ^task} =
             TaskAutosave.change(
               autosave,
               project,
               task,
               %{"title" => "", "status" => "in_review"},
               "status"
             )

    refute autosave.form.source.valid?
    assert autosave.save_failed?
    assert autosave.save_state == :failed
  end

  test "dirty fields take precedence over a previously saved lifecycle" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    autosave = TaskAutosave.load(TaskAutosave.empty(), task, saved?: false)

    assert {:ok, autosave, _updated_task} =
             TaskAutosave.change(
               autosave,
               project,
               task,
               %{"status" => "in_progress"},
               "status"
             )

    assert autosave.saved?
    assert autosave.save_state == :saved

    assert {:schedule, autosave, ^task, 60_000, _message} =
             TaskAutosave.change(
               autosave,
               project,
               task,
               %{"title" => "After", "status" => "in_progress"},
               "title"
             )

    assert autosave.saved?
    assert autosave.dirty_fields == MapSet.new(["title"])
    assert autosave.save_state == :saving
  end

  test "controlled not-found clears the lifecycle while preserving sequence" do
    selected_project = project_fixture(%{})
    foreign_project = project_fixture(%{})
    foreign_task = task_fixture(foreign_project, %{title: "Before", status: :pending})
    autosave = TaskAutosave.load(TaskAutosave.empty(), foreign_task, saved?: false)

    assert {:schedule, autosave, ^foreign_task, 60_000, _message} =
             TaskAutosave.change(
               autosave,
               foreign_project,
               foreign_task,
               %{"title" => "Changed", "status" => "pending"},
               "title"
             )

    assert {:not_found, cleared} =
             TaskAutosave.change(
               autosave,
               selected_project,
               foreign_task,
               %{"title" => "Changed", "status" => "done"},
               "status"
             )

    assert cleared.form == nil
    assert cleared.sequence == autosave.sequence
  end
end
