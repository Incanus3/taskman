defmodule Taskman.Tasks.AvailabilityTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.AvailabilityPersistence

  test "groups only additional states at their direct owning location within the Project" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    direct_list = list_fixture(project)
    child_list = list_fixture(project, direct_list)
    empty_list = list_fixture(project)
    other_list = list_fixture(other_project)

    for location <- [nil, direct_list, child_list] do
      task_fixture(project, location, %{status: :not_planned})
      task_fixture(project, location, %{status: :not_planned})
      task_fixture(project, location, %{status: :pending})
      task_fixture(project, location, %{status: :will_not_do})
    end

    task_fixture(project, empty_list, %{status: :done})
    task_fixture(other_project, %{status: :not_planned})
    task_fixture(other_project, other_list, %{status: :not_planned})

    handler_id = {__MODULE__, make_ref()}
    owner = self()

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, _metadata, owner ->
          if self() == owner, do: send(owner, :bootstrap_query)
        end,
        owner
      )

    on_exit(fn -> :telemetry.detach(handler_id) end)

    assert Tasks.additional_states_by_location(project) == %{
             nil => MapSet.new([:not_planned]),
             direct_list.id => MapSet.new([:not_planned]),
             child_list.id => MapSet.new([:not_planned])
           }

    assert_receive :bootstrap_query
    refute_receive :bootstrap_query, 0
  end

  test "Projects with no additional-state Tasks have no observations" do
    project = project_fixture(%{})
    task_fixture(project, %{status: :pending})

    assert Tasks.additional_states_by_location(project) == %{}
    assert Tasks.additional_states_by_location(project_fixture(%{})) == %{}
  end

  test "storage returns distinct state-location rows restricted to the requested states" do
    project = project_fixture(%{})
    task_fixture(project, %{status: :not_planned})
    task_fixture(project, %{status: :not_planned})
    task_fixture(project, %{status: :pending})

    assert AvailabilityPersistence.list_location_states(project.id, [:not_planned]) == [
             {nil, :not_planned}
           ]

    assert AvailabilityPersistence.list_location_states(project.id, []) == []
  end
end
