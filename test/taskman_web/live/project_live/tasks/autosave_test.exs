defmodule TaskmanWeb.ProjectLive.Tasks.AutosaveTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Repo, Tasks}
  alias TaskmanWeb.ProjectLive.Tasks.Autosave

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

    autosave = Autosave.load(Autosave.empty(), first, saved?: false)
    assert autosave.form[:title].value == "First"
    assert autosave.save_state == :idle

    assert {:schedule, autosave, ^first, 60_000, {:autosave_task_field, first_id, "title", 1}} =
             Autosave.change(autosave, project, first, %{"title" => "Changed"}, "title")

    assert first_id == first.id

    cleared = Autosave.clear(autosave)
    assert cleared.sequence == 1
    assert cleared.form == nil
    assert cleared.draft == %{}
    assert cleared.dirty_fields == MapSet.new()
    assert cleared.revisions == %{}
    assert cleared.save_state == :idle

    reloaded = Autosave.load(cleared, second, saved?: true)
    assert reloaded.sequence == 1
    assert reloaded.form[:title].value == "Second"
    assert reloaded.saved?
    assert reloaded.save_state == :saved
  end

  test "save-state messages preserve the existing copy" do
    autosave = Autosave.empty()

    assert Autosave.message(%{autosave | save_state: :idle}) == "Autosaves changes"
    assert Autosave.message(%{autosave | save_state: :saving}) == "Saving…"
    assert Autosave.message(%{autosave | save_state: :saved}) == "Saved"
    assert Autosave.message(%{autosave | save_state: :not_saved}) == "Not saved"

    assert Autosave.message(%{autosave | save_state: :failed}) ==
             "Couldn’t save changes"
  end

  test "debounced changes return effects and only the current revision persists" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert {:schedule, autosave, ^task, 60_000, message} =
             Autosave.change(autosave, project, task, %{"title" => "After"}, "title")

    assert message == {:autosave_task_field, task.id, "title", 1}
    assert autosave.save_state == :saving
    assert autosave.dirty_fields == MapSet.new(["title"])

    assert {:ignored, ^autosave, ^task} =
             Autosave.handle_scheduled_save(
               autosave,
               project,
               task,
               task.id,
               "title",
               0
             )

    assert Tasks.get_task_for_project(project, task.id).title == "Before"

    assert {:ok, autosave, saved_task} =
             Autosave.handle_scheduled_save(
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
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert {:ignored, ^autosave, ^task} =
             Autosave.change(autosave, project, task, %{"project_id" => "9"}, "project_id")
  end

  test "an invalid debounced field is not scheduled" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert {:ok, autosave, ^task} =
             Autosave.change(autosave, project, task, %{"title" => ""}, "title")

    assert autosave.sequence == 0
    assert autosave.save_state == :not_saved
    assert autosave.dirty_fields == MapSet.new(["title"])
  end

  test "a zero delay is returned as an explicit schedule effect" do
    Application.put_env(:taskman, :task_autosave_delay_ms, 0)
    on_exit(fn -> Application.put_env(:taskman, :task_autosave_delay_ms, 60_000) end)

    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert {:schedule, _autosave, ^task, 0, {:autosave_task_field, task_id, "title", 1}} =
             Autosave.change(autosave, project, task, %{"title" => "After"}, "title")

    assert task_id == task.id
  end

  test "an immediate field persists while an unrelated invalid draft remains visible" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert {:ok, autosave, ^task} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"title" => "", "status" => "pending"},
               "title"
             )

    assert {:ok, autosave, updated_task} =
             Autosave.change(
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
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert {:schedule, autosave, ^task, 60_000, _message} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"title" => "Before", "description" => "New"},
               "description"
             )

    assert {:ok, autosave, ^task} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"title" => "", "description" => "New"},
               "title"
             )

    assert {:ok, autosave, updated_task} = Autosave.flush(autosave, project, task)
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
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert {:schedule, autosave, ^task, 60_000, _message} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"title" => "Valid alongside failure", "status" => "pending"},
               "title"
             )

    assert {:ok, autosave, ^task} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"title" => "Valid alongside failure", "status" => "in_review"},
               "status"
             )

    assert autosave.save_state == :failed

    assert {:error, autosave, partially_updated_task} =
             Autosave.flush(autosave, project, task)

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
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert {:ok, autosave, ^task} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"title" => "", "status" => "pending"},
               "title"
             )

    assert {:ok, autosave, ^task} =
             Autosave.change(
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
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert {:ok, autosave, _updated_task} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"status" => "in_progress"},
               "status"
             )

    assert autosave.saved?
    assert autosave.save_state == :saved

    assert {:schedule, autosave, ^task, 60_000, _message} =
             Autosave.change(
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
    autosave = Autosave.load(Autosave.empty(), foreign_task, saved?: false)

    assert {:schedule, autosave, ^foreign_task, 60_000, _message} =
             Autosave.change(
               autosave,
               foreign_project,
               foreign_task,
               %{"title" => "Changed", "status" => "pending"},
               "title"
             )

    assert {:not_found, cleared} =
             Autosave.change(
               autosave,
               selected_project,
               foreign_task,
               %{"title" => "Changed", "status" => "done"},
               "status"
             )

    assert cleared.form == nil
    assert cleared.sequence == autosave.sequence
  end

  test "reconciles clean fields while preserving a dirty title draft" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert autosave.baseline == task

    assert {:schedule, autosave, ^task, 60_000, _message} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"title" => "Mine", "status" => "pending"},
               "title"
             )

    assert {:ok, externally_updated} = Tasks.update_task(project, task, %{status: :in_review})

    reconciled = Autosave.reconcile(autosave, externally_updated)

    assert reconciled.baseline == externally_updated
    assert reconciled.form[:title].value == "Mine"
    assert reconciled.form[:status].value == :in_review
    assert reconciled.draft == %{"title" => "Mine"}
    assert reconciled.dirty_fields == MapSet.new(["title"])
    assert reconciled.conflicts == %{}
  end

  test "reconciles an external change to a dirty field as a conflict and invalidates its timer" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})

    assert {:schedule, autosave, ^task, 60_000, {:autosave_task_field, _, "title", revision}} =
             Autosave.change(
               Autosave.load(Autosave.empty(), task, saved?: false),
               project,
               task,
               %{"title" => "Mine", "status" => "pending"},
               "title"
             )

    assert {:ok, externally_updated} = Tasks.update_task(project, task, %{title: "Latest"})

    conflicted = Autosave.reconcile(autosave, externally_updated)

    assert conflicted.baseline == externally_updated
    assert conflicted.form[:title].value == "Mine"
    assert conflicted.draft == %{"title" => "Mine"}
    assert conflicted.conflicts == %{"title" => "Latest"}
    assert Autosave.conflict_value(conflicted, "title") == "Latest"
    assert conflicted.revisions == %{}
    assert conflicted.save_state == :conflicted
    assert Autosave.message(conflicted) == "Resolve conflicting changes"

    assert {:ignored, ^conflicted, ^externally_updated} =
             Autosave.handle_scheduled_save(
               conflicted,
               project,
               externally_updated,
               task.id,
               "title",
               revision
             )

    assert Tasks.get_task_for_project(project, task.id).title == "Latest"
  end

  test "editing a conflicted debounced field keeps the draft without scheduling or persisting" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})

    assert {:schedule, autosave, ^task, 60_000,
            {:autosave_task_field, task_id, "title", revision}} =
             Autosave.change(
               Autosave.load(Autosave.empty(), task, saved?: false),
               project,
               task,
               %{"title" => "Mine"},
               "title"
             )

    assert task_id == task.id

    assert {:ok, latest} = Tasks.update_task(project, task, %{title: "Latest"})
    conflicted = Autosave.reconcile(autosave, latest)

    assert {:ok, edited, ^latest} =
             Autosave.change(
               conflicted,
               project,
               latest,
               %{"title" => "Mine again"},
               "title"
             )

    assert edited.form[:title].value == "Mine again"
    assert edited.draft == %{"title" => "Mine again"}
    assert edited.dirty_fields == MapSet.new(["title"])
    assert edited.conflicts == %{"title" => "Latest"}
    assert edited.revisions == %{}
    assert edited.save_state == :conflicted
    assert Tasks.get_task_for_project(project, task.id).title == "Latest"

    assert {:ignored, ^edited, ^latest} =
             Autosave.handle_scheduled_save(
               edited,
               project,
               latest,
               task.id,
               "title",
               revision
             )
  end

  test "editing a conflicted immediate field keeps the draft without persisting" do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :pending})
    autosave = Autosave.load(Autosave.empty(), task, saved?: false)

    assert {:ok, latest} = Tasks.update_task(project, task, %{status: :done})

    assert {:conflict, conflicted, ^latest} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"status" => "in_review"},
               "status"
             )

    assert conflicted.form[:status].value == :in_review
    assert conflicted.conflicts == %{"status" => :done}

    assert {:ok, edited, ^latest} =
             Autosave.change(
               conflicted,
               project,
               latest,
               %{"status" => "icebox"},
               "status"
             )

    assert edited.form[:status].value == :icebox
    assert edited.draft == %{"status" => "icebox"}
    assert edited.dirty_fields == MapSet.new(["status"])
    assert edited.conflicts == %{"status" => :done}
    assert edited.revisions == %{}
    assert edited.save_state == :conflicted
    assert Tasks.get_task_for_project(project, task.id).status == :done
  end

  test "replaces a conflict's latest value while retaining unrelated dirty drafts" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", description: "Old", status: :pending})

    assert {:schedule, autosave, ^task, 60_000, _message} =
             Autosave.change(
               Autosave.load(Autosave.empty(), task, saved?: false),
               project,
               task,
               %{"title" => "Mine", "description" => "Old", "status" => "pending"},
               "title"
             )

    assert {:schedule, autosave, ^task, 60_000, _message} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"title" => "Mine", "description" => "Local description", "status" => "pending"},
               "description"
             )

    assert {:ok, first_external} = Tasks.update_task(project, task, %{title: "Latest one"})
    conflicted = Autosave.reconcile(autosave, first_external)

    assert {:ok, second_external} =
             Tasks.update_task(project, first_external, %{title: "Latest two", status: :done})

    reconciled = Autosave.reconcile(conflicted, second_external)

    assert reconciled.conflicts == %{"title" => "Latest two"}
    assert reconciled.form[:title].value == "Mine"
    assert reconciled.form[:description].value == "Local description"
    assert reconciled.form[:status].value == :done
    assert reconciled.dirty_fields == MapSet.new(["description", "title"])
  end

  test "flush refuses unresolved conflicts without writing the local value" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})

    assert {:schedule, autosave, ^task, 60_000, _message} =
             Autosave.change(
               Autosave.load(Autosave.empty(), task, saved?: false),
               project,
               task,
               %{"title" => "Mine"},
               "title"
             )

    assert {:ok, externally_updated} = Tasks.update_task(project, task, %{title: "Latest"})
    conflicted = Autosave.reconcile(autosave, externally_updated)

    assert {:error, ^conflicted, ^externally_updated} =
             Autosave.flush(conflicted, project, externally_updated)

    assert Tasks.get_task_for_project(project, task.id).title == "Latest"
  end

  test "use latest clears an ordinary conflict locally without writing" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})

    assert {:schedule, autosave, ^task, 60_000, _message} =
             Autosave.change(
               Autosave.load(Autosave.empty(), task, saved?: false),
               project,
               task,
               %{"title" => "Mine"},
               "title"
             )

    assert {:ok, latest} = Tasks.update_task(project, task, %{title: "Latest"})
    conflicted = Autosave.reconcile(autosave, latest)

    assert {:ok, resolved, ^latest} =
             Autosave.resolve_conflict(conflicted, project, latest, "title", :use_latest)

    assert resolved.form[:title].value == "Latest"
    assert resolved.draft == %{}
    assert resolved.dirty_fields == MapSet.new()
    assert resolved.revisions == %{}
    assert resolved.conflicts == %{}
    assert Tasks.get_task_for_project(project, task.id).title == "Latest"
  end

  test "keep mine retries only the conflicted field and retains a further conflict" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})

    assert {:schedule, autosave, ^task, 60_000, _message} =
             Autosave.change(
               Autosave.load(Autosave.empty(), task, saved?: false),
               project,
               task,
               %{"title" => "Mine", "status" => "pending"},
               "title"
             )

    assert {:ok, latest} = Tasks.update_task(project, task, %{title: "Latest"})
    conflicted = Autosave.reconcile(autosave, latest)

    assert {:ok, resolved, saved} =
             Autosave.resolve_conflict(conflicted, project, latest, "title", :keep_mine)

    assert saved.title == "Mine"
    assert resolved.conflicts == %{}
    assert resolved.dirty_fields == MapSet.new()

    assert {:schedule, autosave, ^saved, 60_000, _message} =
             Autosave.change(
               resolved,
               project,
               saved,
               %{"title" => "Mine again", "status" => "pending"},
               "title"
             )

    assert {:ok, latest} = Tasks.update_task(project, saved, %{title: "Latest again"})
    conflicted = Autosave.reconcile(autosave, latest)
    assert {:ok, raced} = Tasks.update_task(project, latest, %{title: "Latest after retry"})

    assert {:conflict, retried, ^raced} =
             Autosave.resolve_conflict(conflicted, project, latest, "title", :keep_mine)

    assert retried.form[:title].value == "Mine again"
    assert retried.conflicts == %{"title" => "Latest after retry"}
    assert retried.save_state == :conflicted
  end

  test "a direct autosave context conflict reconciles like an external update" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})

    assert {:schedule, autosave, ^task, 60_000, {:autosave_task_field, _, "title", revision}} =
             Autosave.change(
               Autosave.load(Autosave.empty(), task, saved?: false),
               project,
               task,
               %{"title" => "Mine"},
               "title"
             )

    assert {:ok, latest} = Tasks.update_task(project, task, %{title: "Latest"})

    assert {:conflict, conflicted, ^latest} =
             Autosave.handle_scheduled_save(
               autosave,
               project,
               task,
               task.id,
               "title",
               revision
             )

    assert conflicted.form[:title].value == "Mine"
    assert conflicted.conflicts == %{"title" => "Latest"}
    assert conflicted.save_state == :conflicted
  end

  test "a successful disjoint save detects a conflict in another unsaved field" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", description: "Original description"})

    assert {:schedule, autosave, ^task, 60_000,
            {:autosave_task_field, _, "title", title_revision}} =
             Autosave.change(
               Autosave.load(Autosave.empty(), task, saved?: false),
               project,
               task,
               %{"title" => "Mine", "description" => "Original description"},
               "title"
             )

    assert {:schedule, autosave, ^task, 60_000,
            {:autosave_task_field, _, "description", description_revision}} =
             Autosave.change(
               autosave,
               project,
               task,
               %{"title" => "Mine", "description" => "My description"},
               "description"
             )

    assert {:ok, externally_updated} =
             Tasks.update_task(project, task, %{description: "Latest description"})

    assert {:ok, autosave, title_saved} =
             Autosave.handle_scheduled_save(
               autosave,
               project,
               task,
               task.id,
               "title",
               title_revision
             )

    assert title_saved.title == "Mine"
    assert title_saved.description == "Latest description"
    assert autosave.form[:description].value == "My description"
    assert autosave.conflicts == %{"description" => "Latest description"}
    assert autosave.revisions == %{}
    assert autosave.save_state == :conflicted

    assert {:ignored, ^autosave, ^title_saved} =
             Autosave.handle_scheduled_save(
               autosave,
               project,
               title_saved,
               task.id,
               "description",
               description_revision
             )

    assert externally_updated.description == "Latest description"
    assert Tasks.get_task_for_project(project, task.id).description == "Latest description"
  end
end
