defmodule Taskman.Tasks.NotificationsTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.ChangeNotifications
  alias Taskman.ChangeNotifications.Event

  test "Task creation publishes its full persisted mutation metadata after success" do
    project = project_fixture(%{})
    topic = subscribe_task_events(project)

    assert {:ok, task} = Tasks.create_task(project, %{title: "Published"})
    refute_receive %Event{}, 50

    assert_receive {:task_event, ^topic,
                    %Event{
                      entity: :task,
                      operation: :created,
                      project_id: project_id,
                      entity_id: task_id,
                      lock_version: lock_version,
                      fields: [
                        :description,
                        :due_at,
                        :list_id,
                        :parent_task_id,
                        :priority,
                        :project_id,
                        :status,
                        :title
                      ]
                    }}

    assert project_id == project.id
    assert task_id == task.id
    assert lock_version == task.lock_version
  end

  test "ordinary Task updates publish their changed fields after persistence" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})
    topic = subscribe_task_events(project)

    assert {:ok, updated} =
             Tasks.update_task(project, task, %{description: "Changed", status: :done})

    assert_receive {:task_event, ^topic,
                    %Event{
                      entity: :task,
                      operation: :updated,
                      entity_id: task_id,
                      lock_version: lock_version,
                      fields: [:description, :status]
                    }}

    assert task_id == updated.id
    assert lock_version == updated.lock_version
  end

  test "parent mutations publish parent_task_id with their ordinary changed fields" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Before"})
    topic = subscribe_task_events(project)

    assert {:ok, updated} =
             Tasks.update_task(project, child, %{title: "After"}, parent: parent)

    assert_receive {:task_event, ^topic,
                    %Event{
                      operation: :updated,
                      entity_id: task_id,
                      lock_version: lock_version,
                      fields: [:parent_task_id, :title]
                    }}

    assert task_id == updated.id
    assert lock_version == updated.lock_version
  end

  test "Task movement publishes only list_id after persistence" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})
    topic = subscribe_task_events(project)

    assert {:ok, moved} = Tasks.move_task(project, task, destination)

    assert_receive {:task_event, ^topic,
                    %Event{
                      operation: :moved,
                      entity_id: task_id,
                      lock_version: lock_version,
                      fields: [:list_id]
                    }}

    assert task_id == moved.id
    assert lock_version == moved.lock_version
  end

  test "failed, conflicting, and unchanged Task mutations publish no event" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Before"})
    topic = subscribe_task_events(project)

    assert {:error, _changeset} = Tasks.update_task(project, task, %{title: ""})
    refute_receive {:task_event, ^topic, %Event{}}, 50

    assert {:error, _changeset} = Tasks.update_task(project, task, %{}, parent: task)
    refute_receive {:task_event, ^topic, %Event{}}, 50

    {first_baseline, second_baseline} = loaded_task_baselines(project, task)
    assert {:ok, updated} = Tasks.update_task(project, first_baseline, %{title: "First writer"})
    assert_receive {:task_event, ^topic, %Event{entity_id: updated_id}}
    assert updated_id == updated.id

    assert {:error, %{__struct__: Taskman.Tasks.Conflict}} =
             Tasks.update_task(project, second_baseline, %{title: "Second writer"})

    refute_receive {:task_event, ^topic, %Event{}}, 50

    assert {:ok, moved} = Tasks.move_task(project, updated, destination)
    assert_receive {:task_event, ^topic, %Event{operation: :moved}}
    assert {:error, :unchanged_location} = Tasks.move_task(project, moved, destination)
    refute_receive {:task_event, ^topic, %Event{}}, 50
  end

  defp subscribe_task_events(project) do
    topic = "projects:#{project.id}:tasks"
    assert :ok = ChangeNotifications.subscribe_project(project)
    start_task_event_forwarder(topic)
    topic
  end

  defp start_task_event_forwarder(topic) do
    test_pid = self()

    start_supervised!(
      {Elixir.Task,
       fn ->
         :ok = Phoenix.PubSub.subscribe(Taskman.PubSub, topic)
         send(test_pid, {:task_event_forwarder_ready, topic})
         forward_task_events(test_pid, topic)
       end},
      id: {:task_event_forwarder, topic}
    )

    assert_receive {:task_event_forwarder_ready, ^topic}
  end

  defp forward_task_events(test_pid, topic) do
    receive do
      event ->
        send(test_pid, {:task_event, topic, event})
        forward_task_events(test_pid, topic)
    end
  end
end
