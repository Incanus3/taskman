defmodule TaskmanWeb.ListEditTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures

  alias Taskman.Lists
  alias Taskman.Lists.NavigationNode
  alias Taskman.Projects
  alias TaskmanWeb.ListEdit

  test "clear returns the empty List editing state" do
    project = project_fixture(%{})
    state = ListEdit.open_new(project, nil)

    assert ListEdit.form_id(state) == "list-create-form-root"
    assert ListEdit.title(state) == "New List"
    assert ListEdit.clear(state) == ListEdit.empty()
  end

  test "identifies the navigation node that owns a root, child, or rename form" do
    project = project_fixture(%{})
    parent = list_fixture(project, %{name: "Planning"})

    project_node = navigation_node(:project, project, nil)
    list_node = navigation_node(:list, project, parent)

    assert project |> ListEdit.open_new(nil) |> ListEdit.active_for?(project_node)
    assert project |> ListEdit.open_new(parent) |> ListEdit.active_for?(list_node)
    assert project |> ListEdit.open_rename(parent) |> ListEdit.active_for?(list_node)
  end

  test "validates against a fresh target and retains the complete form interaction" do
    project = project_fixture(%{})
    task_list = list_fixture(project, %{name: "Planning"})

    assert {:ok, validated} =
             project
             |> ListEdit.open_rename(task_list)
             |> ListEdit.validate(%{"name" => "Roadmap"})

    assert validated.form[:name].value == "Roadmap"
    assert validated.form.source.action == :validate
    assert ListEdit.form_id(validated) == "list-rename-form-#{task_list.id}"
    assert ListEdit.title(validated) == "Rename Planning"
  end

  test "rejects validation after the active List target disappears" do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    state = ListEdit.open_rename(project, task_list)

    Taskman.Repo.delete!(task_list)

    assert {:error, :not_found} = ListEdit.validate(state, %{"name" => "Roadmap"})
    assert :error = ListEdit.target(state)
  end

  test "reconciles canonical Project and List targets without replacing the draft form" do
    project = project_fixture(%{})
    task_list = list_fixture(project, %{name: "Before"})

    state =
      project
      |> ListEdit.open_rename(task_list)
      |> then(&ListEdit.validate(&1, %{"name" => "Mine"}))
      |> elem(1)

    canonical_project = Projects.get_project(project.id)

    assert {:ok, canonical_list} =
             Lists.rename_list(canonical_project, task_list, %{name: "Latest"})

    refreshed =
      ListEdit.reconcile(state, [canonical_project], %{canonical_project.id => [canonical_list]})

    assert refreshed.project == canonical_project
    assert {:ok, {:rename, ^canonical_list}, _target} = ListEdit.target(refreshed)
    assert refreshed.form[:name].value == "Mine"
    assert ListEdit.form_id(refreshed) == "list-rename-form-#{task_list.id}"
  end

  defp navigation_node(kind, project, task_list) do
    %NavigationNode{
      dom_id: "#{kind}-node",
      kind: kind,
      depth: 1,
      project: project,
      task_list: task_list,
      expanded?: false,
      expandable?: false,
      selected?: false
    }
  end
end
