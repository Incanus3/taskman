defmodule Taskman.Projects.ActionsTest do
  use Taskman.DataCase, async: false

  alias Phoenix.PubSub
  alias Taskman.ChangeNotifications
  alias Taskman.ChangeNotifications.Event
  alias Taskman.Projects

  test "create_project/1 trims and persists a name-only Project" do
    assert {:ok, project} = Projects.create_project(%{name: "  Taskman  "})
    assert project.name == "Taskman"
  end

  test "create_project/1 rejects missing and whitespace names" do
    assert {:error, changeset} = Projects.create_project(%{})
    assert %{name: [_]} = errors_on(changeset)

    assert {:error, changeset} = Projects.create_project(%{name: "   "})
    assert %{name: [_]} = errors_on(changeset)
  end

  test "create_project/1 publishes a workspace event after persistence" do
    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder("workspace:changes")

    assert {:ok, project} = Projects.create_project(%{name: "  Taskman  "})

    project_id = project.id

    assert_receive {:forwarded,
                    %Event{
                      entity: :project,
                      operation: :created,
                      project_id: ^project_id,
                      entity_id: ^project_id,
                      fields: [:name]
                    }}

    assert project_id == project.id
  end

  test "invalid Project creation publishes no workspace event" do
    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder("workspace:changes")

    assert {:error, _changeset} = Projects.create_project(%{})
    refute_receive {:forwarded, %Event{}}, 50
  end

  test "successful Project creation keeps its result when publication fails" do
    previous = Application.get_env(:taskman, :change_notifications_pubsub)
    Application.put_env(:taskman, :change_notifications_pubsub, Taskman.MissingPubSub)

    on_exit(fn ->
      if previous == nil do
        Application.delete_env(:taskman, :change_notifications_pubsub)
      else
        Application.put_env(:taskman, :change_notifications_pubsub, previous)
      end
    end)

    assert {:ok, project} = Projects.create_project(%{name: "Taskman"})

    assert project.id > 0
  end

  test "list_projects/0 is stable and get_project/1 handles invalid IDs" do
    assert {:ok, first} = Projects.create_project(%{name: "First"})
    assert {:ok, second} = Projects.create_project(%{name: "Second"})

    assert Projects.list_projects() == [first, second]
    assert Projects.get_project(Integer.to_string(first.id)) == first
    assert Projects.get_project("not-an-id") == nil
    assert Projects.get_project(-1) == nil
  end

  defp start_forwarder(topic) do
    test_pid = self()

    start_supervised!(
      {Task,
       fn ->
         :ok = PubSub.subscribe(Taskman.PubSub, topic)
         send(test_pid, {:forwarder_ready, topic})
         forward_messages(test_pid)
       end},
      id: {:forwarder, topic}
    )

    assert_receive {:forwarder_ready, ^topic}
  end

  defp forward_messages(test_pid) do
    receive do
      message ->
        send(test_pid, {:forwarded, message})
        forward_messages(test_pid)
    end
  end
end
