defmodule TaskmanWeb.ProjectLive.Tasks.CreationTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures

  alias Taskman.Tasks
  alias TaskmanWeb.ProjectLive.Tasks.Creation.State

  test "an enabled form requires a valid canonical location" do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    valid_form = project |> Tasks.change_task(%{"title" => "Ship"}) |> Phoenix.Component.to_form()

    state =
      State.empty()
      |> State.open(valid_form, "list:#{task_list.id}", "List #{task_list.name}", [
        {"Project #{project.name}", "project"},
        {"List #{task_list.name}", "list:#{task_list.id}"}
      ])

    assert state.enabled?
    assert state.location == "list:#{task_list.id}"

    assert State.clear(state) == State.empty()
  end

  test "validation preserves location and derives enabled state from the form and location" do
    project = project_fixture(%{})

    state =
      State.empty()
      |> State.open(
        Tasks.change_task(project) |> Phoenix.Component.to_form(),
        "project",
        "Project #{project.name}",
        [
          {"Project #{project.name}", "project"}
        ]
      )

    valid_form = project |> Tasks.change_task(%{"title" => "Ship"}) |> Phoenix.Component.to_form()

    validated = State.validate(state, valid_form)
    assert validated.enabled?
    assert validated.location == "project"
  end

  test "opening with an unavailable location keeps its label and disables creation" do
    project = project_fixture(%{})
    valid_form = project |> Tasks.change_task(%{"title" => "Ship"}) |> Phoenix.Component.to_form()

    state =
      State.empty()
      |> State.open(valid_form, "list:999", "List Removed", [
        {"Project #{project.name}", "project"}
      ])

    refute state.enabled?
    assert state.location == "list:999"
    assert state.location_label == "List Removed"
    assert state.location_error == "This List is no longer available. Choose another location."
  end
end
