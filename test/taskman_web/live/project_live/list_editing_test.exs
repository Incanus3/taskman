defmodule TaskmanWeb.ProjectLive.ListEditingTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures

  alias Taskman.Lists
  alias TaskmanWeb.ProjectLive.{ListEdit, ListEditing}

  test "opens a root editor with omitted, empty, or nil parent" do
    project = project_fixture(%{})

    for params <- [
          %{"kind" => "new"},
          %{"kind" => "new", "parent-id" => ""},
          %{"kind" => "new", "parent_id" => nil}
        ] do
      assert {:changed, edit} = ListEditing.open(project, params)
      assert edit.project == project
      assert edit.action == {:new, nil}
      assert edit.form.name == "list"
      assert edit.form[:name].value == nil
      assert edit.form.source.data.project_id == project.id
    end
  end

  test "opens scoped child and rename editors using supported parameter aliases" do
    project = project_fixture(%{})
    parent = list_fixture(project, %{name: "Planning"})

    for {project_key, parent_key, list_key} <- [
          {"project-id", "parent-id", "list-id"},
          {"project_id", "parent_id", "list_id"}
        ],
        project_id <- [project.id, to_string(project.id)] do
      assert {:changed, child} =
               ListEditing.open(project, %{
                 "kind" => "new",
                 project_key => project_id,
                 parent_key => to_string(parent.id)
               })

      assert child.action == {:new, parent}
      assert child.form.source.data.parent_list_id == parent.id

      assert {:changed, rename} =
               ListEditing.open(project, %{
                 "kind" => "rename",
                 project_key => project_id,
                 list_key => parent.id
               })

      assert rename.action == {:rename, parent}
      assert rename.form[:name].value == "Planning"
    end
  end

  test "hyphenated parameters retain precedence over underscored aliases" do
    project = project_fixture(%{})
    parent = list_fixture(project)
    other = list_fixture(project)

    assert {:changed, child} =
             ListEditing.open(project, %{
               "kind" => "new",
               "project-id" => project.id,
               "project_id" => -1,
               "parent-id" => parent.id,
               "parent_id" => other.id
             })

    assert child.action == {:new, parent}

    assert {:changed, rename} =
             ListEditing.open(project, %{
               "kind" => "rename",
               "list-id" => parent.id,
               "list_id" => other.id
             })

    assert rename.action == {:rename, parent}
  end

  test "invalid actions and targets cannot open editors outside the selected Project" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    foreign = list_fixture(other_project)

    assert :unchanged = ListEditing.open(nil, %{"kind" => "new"})

    for params <- [
          %{},
          %{"kind" => "unknown"},
          %{"kind" => "new", "project-id" => other_project.id},
          %{"kind" => "new", "project_id" => "invalid"},
          %{"kind" => "new", "parent-id" => foreign.id},
          %{"kind" => "new", "parent_id" => "invalid"},
          %{"kind" => "rename", "list-id" => foreign.id},
          %{"kind" => "rename", "list_id" => "invalid"},
          %{"kind" => "rename"}
        ] do
      assert :unchanged = ListEditing.open(project, params)
    end
  end

  test "validation retains draft input and errors without persisting" do
    project = project_fixture(%{})
    task_list = list_fixture(project, %{name: "Original"})
    edit = ListEdit.open_rename(project, task_list)

    assert {:changed, draft} = ListEditing.validate(edit, %{"name" => "Draft"})
    assert draft.form[:name].value == "Draft"
    assert draft.form.source.action == :validate

    assert {:changed, invalid} =
             ListEditing.validate(ListEdit.open_new(project, nil), %{"name" => "   "})

    assert invalid.form[:name].value == "   "
    assert Keyword.has_key?(invalid.form.source.errors, :name)
    assert Lists.get_list_for_project(project, task_list.id).name == "Original"
  end

  test "creates root and child Lists with persisted scope and explicit parent outcomes" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    parent = list_fixture(project)

    assert {:created, root, nil} =
             ListEditing.save(ListEdit.open_new(project, nil), %{
               "name" => "Root",
               "project_id" => other_project.id,
               "parent_list_id" => parent.id
             })

    assert root.name == "Root"
    assert root.project_id == project.id
    assert root.parent_list_id == nil
    assert Lists.get_list_for_project(project, root.id) == root

    assert {:created, child, ^parent} =
             ListEditing.save(ListEdit.open_new(project, parent), %{"name" => "Child"})

    assert child.name == "Child"
    assert child.parent_list_id == parent.id
    assert Lists.get_list_for_project(project, child.id) == child
  end

  test "rename uses the current scoped target" do
    project = project_fixture(%{})
    task_list = list_fixture(project, %{name: "Original"})
    edit = ListEdit.open_rename(project, task_list)
    {:ok, _latest} = Lists.rename_list(project, task_list, %{name: "Latest"})

    assert {:renamed, renamed} = ListEditing.save(edit, %{"name" => "Renamed"})
    assert renamed.id == task_list.id
    assert renamed.name == "Renamed"
    assert Lists.get_list_for_project(project, task_list.id) == renamed
  end

  test "duplicate creation and rename retain submitted drafts and validation errors" do
    project = project_fixture(%{})
    existing = list_fixture(project, %{name: "Taken"})
    other = list_fixture(project, %{name: "Other"})

    for edit <- [ListEdit.open_new(project, nil), ListEdit.open_rename(project, other)] do
      assert {:changed, draft} = ListEditing.save(edit, %{"name" => "Taken"})
      assert draft.action == edit.action
      assert draft.form[:name].value == "Taken"
      assert draft.form.source.action == :validate
      assert Keyword.has_key?(draft.form.source.errors, :name)
    end

    assert Lists.get_list_for_project(project, existing.id).name == "Taken"
    assert Lists.get_list_for_project(project, other.id).name == "Other"
    assert length(Lists.list_lists_for_project(project)) == 2
  end

  test "blank root and child creation retain errors without persisting" do
    project = project_fixture(%{})
    task_list = list_fixture(project, %{name: "Original"})

    for edit <- [ListEdit.open_new(project, nil), ListEdit.open_new(project, task_list)] do
      assert {:changed, draft} = ListEditing.save(edit, %{"name" => "   "})
      assert draft.form[:name].value == "   "
      assert Keyword.has_key?(draft.form.source.errors, :name)
    end

    assert Lists.get_list_for_project(project, task_list.id).name == "Original"
    assert length(Lists.list_lists_for_project(project)) == 1
  end

  test "closed and disappeared targets ignore validation and save" do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    child_edit = ListEdit.open_new(project, task_list)
    rename_edit = ListEdit.open_rename(project, task_list)
    Repo.delete!(task_list)

    for edit <- [ListEdit.empty(), child_edit, rename_edit] do
      assert :unchanged = ListEditing.validate(edit, %{"name" => "Ignored"})
      assert :unchanged = ListEditing.save(edit, %{"name" => "Ignored"})
    end

    assert Lists.list_lists_for_project(project) == []
    assert :unchanged = ListEditing.open(project, %{"kind" => "new", "parent-id" => task_list.id})

    assert :unchanged =
             ListEditing.open(project, %{"kind" => "rename", "list-id" => task_list.id})
  end
end
