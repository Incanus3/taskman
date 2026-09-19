defmodule TaskmanWeb.ProjectLive.Tasks.CreationTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures

  alias Taskman.Tasks
  alias TaskmanWeb.ProjectLive.Tasks.Creation
  alias TaskmanWeb.ProjectLive.Tasks.Creation.State

  test "an enabled form always retains its target location" do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    valid_form = project |> Tasks.change_task(%{"title" => "Ship"}) |> Phoenix.Component.to_form()

    state = State.empty() |> State.open(valid_form, task_list)
    assert state.enabled?
    assert state.location == task_list
    assert Creation.location_copy(project, state) == "Create this Task in List #{task_list.name}."

    assert State.clear(state) == State.empty()
  end

  test "validation preserves location and derives enabled state from the form" do
    project = project_fixture(%{})

    state =
      State.empty() |> State.open(Tasks.change_task(project) |> Phoenix.Component.to_form(), nil)

    valid_form = project |> Tasks.change_task(%{"title" => "Ship"}) |> Phoenix.Component.to_form()

    validated = State.validate(state, valid_form)
    assert validated.enabled?
    assert validated.location == nil
  end
end
