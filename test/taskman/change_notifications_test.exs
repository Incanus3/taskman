defmodule Taskman.ChangeNotificationsTest do
  use ExUnit.Case, async: false

  alias Phoenix.PubSub
  alias Taskman.ChangeNotifications
  alias Taskman.ChangeNotifications.Event
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks.Task

  @workspace_topic "workspace:changes"

  test "publishes workspace events while excluding the sender and isolating Project topics" do
    project = %Project{id: 17, name: "Taskman", primary_directory: "/workspace/taskman"}

    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder(@workspace_topic)
    start_forwarder("projects:17:tasks")
    assert :ok = ChangeNotifications.subscribe_project(project.id)
    project_id = project.id

    assert :ok =
             ChangeNotifications.publish_project(
               project,
               :created,
               [:primary_directory, :name, :name]
             )

    refute_receive %Event{}, 50

    assert_receive {:forwarded, @workspace_topic,
                    %Event{
                      entity: :project,
                      operation: :created,
                      project_id: ^project_id,
                      entity_id: ^project_id,
                      lock_version: nil,
                      fields: [:name, :primary_directory]
                    }}

    refute_receive {:forwarded, "projects:17:tasks", %Event{entity: :project}}, 50
  end

  test "normalizes List fields and preserves owning and entity IDs" do
    task_list = %TaskList{id: 23, project_id: 17, parent_list_id: 11, name: "Launch"}

    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder(@workspace_topic)

    assert :ok =
             ChangeNotifications.publish_list(
               task_list,
               :created,
               [:parent_list_id, :name, :project_id, :name]
             )

    refute_receive %Event{}, 50

    assert_receive {:forwarded, @workspace_topic,
                    %Event{
                      entity: :list,
                      operation: :created,
                      project_id: 17,
                      entity_id: 23,
                      lock_version: nil,
                      fields: [:name, :project_id, :parent_list_id]
                    }}
  end

  test "publishes Task events to the owning Project topic" do
    task = %Task{id: 29, project_id: 17, lock_version: 3}

    assert :ok = ChangeNotifications.subscribe_project(task.project_id)
    start_forwarder("projects:17:tasks")

    assert :ok = ChangeNotifications.publish_task(task, :moved, [:list_id, :list_id])
    refute_receive %Event{}, 50

    assert_receive {:forwarded, "projects:17:tasks",
                    %Event{
                      entity: :task,
                      operation: :moved,
                      project_id: 17,
                      entity_id: 29,
                      lock_version: 3,
                      fields: [:list_id]
                    }}

    assert :ok = ChangeNotifications.unsubscribe_project(task.project_id)
  end

  test "rejects non-atom fields before publication" do
    project = %Project{id: 17, name: "Taskman", primary_directory: "/workspace/taskman"}

    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder(@workspace_topic)

    assert_raise ArgumentError, fn ->
      ChangeNotifications.publish_project(project, :created, [:name, "primary_directory"])
    end

    refute_receive {:forwarded, @workspace_topic, %Event{}}, 50
  end

  test "returns an error instead of raising when the configured PubSub server is missing" do
    previous = Application.get_env(:taskman, :change_notifications_pubsub)
    Application.put_env(:taskman, :change_notifications_pubsub, Taskman.MissingPubSub)

    on_exit(fn ->
      if previous == nil do
        Application.delete_env(:taskman, :change_notifications_pubsub)
      else
        Application.put_env(:taskman, :change_notifications_pubsub, previous)
      end
    end)

    project = %Project{id: 17, name: "Taskman", primary_directory: "/workspace/taskman"}

    assert {:error, _reason} =
             ChangeNotifications.publish_project(project, :created, [:name])
  end

  test "rejects non-positive Project IDs through function clauses" do
    assert_raise FunctionClauseError, fn ->
      ChangeNotifications.subscribe_project(0)
    end

    invalid_project = %Project{id: nil}

    assert_raise FunctionClauseError, fn ->
      apply(ChangeNotifications, :publish_project, [invalid_project, :created, [:name]])
    end
  end

  defp start_forwarder(topic) do
    test_pid = self()

    start_supervised!(
      {Elixir.Task,
       fn ->
         :ok = PubSub.subscribe(Taskman.PubSub, topic)
         send(test_pid, {:forwarder_ready, topic})
         forward_messages(test_pid, topic)
       end},
      id: {:forwarder, topic}
    )

    assert_receive {:forwarder_ready, ^topic}
  end

  defp forward_messages(test_pid, topic) do
    receive do
      message ->
        send(test_pid, {:forwarded, topic, message})
        forward_messages(test_pid, topic)
    end
  end
end
