defmodule TaskmanWeb.ProjectLive.ProjectEditingTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures

  alias Taskman.Projects
  alias TaskmanWeb.ProjectLive.{ProjectEdit, ProjectEditing}

  test "validation retains invalid drafts without persisting" do
    project = project_fixture(%{name: "Original"})
    edit = ProjectEdit.open_edit(project)

    assert {:changed, draft} =
             ProjectEditing.validate(edit, %{"name" => " Draft ", "color" => "12zz"})

    assert draft.form[:name].value == " Draft "
    assert draft.form[:color].value == "12zz"
    assert Keyword.has_key?(draft.changeset.errors, :color)
    assert Projects.get_project(project.id).name == "Original"
  end

  test "closed and unavailable editors ignore validation and submission" do
    closed = ProjectEdit.empty()
    unavailable = project_fixture(%{}) |> ProjectEdit.open_edit() |> ProjectEdit.reconcile([])

    for edit <- [closed, unavailable] do
      assert {:unchanged, ^edit} = ProjectEditing.validate(edit, %{"name" => "Ignored"})
      assert {:unchanged, ^edit} = ProjectEditing.save(edit, %{"name" => "Ignored"})
    end
  end

  test "creation persists editable fields with canonical color and ignores supplied identity" do
    existing = project_fixture(%{})

    assert {:created, created} =
             ProjectEditing.save(ProjectEdit.open_new(), %{
               "id" => existing.id,
               "name" => "Created",
               "description" => "Description",
               "icon" => "rocket-launch",
               "color" => "12abef"
             })

    assert created.id != existing.id
    persisted = Projects.get_project(created.id)
    assert persisted.name == "Created"
    assert persisted.description == "Description"
    assert persisted.icon == "rocket-launch"
    assert persisted.color == "#12ABEF"
  end

  test "invalid submission preserves draft and errors without creating a Project" do
    assert {:changed, draft} =
             ProjectEditing.save(ProjectEdit.open_new(), %{"name" => "", "color" => "12A"})

    assert draft.dirty?
    assert draft.form[:color].value == "12A"
    assert Keyword.has_key?(draft.form.source.errors, :name)
    assert Keyword.has_key?(draft.form.source.errors, :color)
    assert Projects.list_projects() == []
  end

  test "update refreshes the persisted target and retains omitted current fields" do
    original = project_fixture(%{name: "Original", description: "Old", color: "#123456"})
    edit = ProjectEdit.open_edit(original)
    {:ok, _latest} = Projects.update_project(original, %{description: "Latest", color: "#ABCDEF"})

    assert {:updated, updated} = ProjectEditing.save(edit, %{"name" => "Edited"})
    assert updated.name == "Edited"
    assert updated.description == "Latest"
    assert updated.color == "#ABCDEF"
    assert Projects.get_project(original.id) == updated
  end

  test "invalid update reconciles current target while keeping submitted draft" do
    original = project_fixture(%{name: "Original"})
    {:ok, edit} = ProjectEdit.validate(ProjectEdit.open_edit(original), %{"name" => "Earlier"})
    {:ok, latest} = Projects.update_project(original, %{name: "Latest"})

    assert {:changed, draft} = ProjectEditing.save(edit, %{"name" => "", "color" => "12zz"})
    assert draft.project == latest
    assert draft.form[:name].value == ""
    assert draft.form[:color].value == "12zz"
    assert Projects.get_project(original.id).name == "Latest"
  end

  test "missing target becomes unavailable before validating submission" do
    project = project_fixture(%{})

    {:ok, edit} =
      ProjectEdit.validate(ProjectEdit.open_edit(project), %{
        "name" => "Retained",
        "color" => "12abef"
      })

    Repo.delete!(project)

    assert {:changed, unavailable} = ProjectEditing.save(edit, %{"name" => "", "color" => "12zz"})
    refute unavailable.available?
    assert unavailable.form[:name].value == "Retained"
    assert unavailable.form[:color].value == "12abef"
    assert unavailable.changeset.valid?
  end
end
