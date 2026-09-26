defmodule Taskman.Projects.ActionsTest do
  use Taskman.DataCase, async: false

  alias Phoenix.PubSub
  alias Taskman.ChangeNotifications
  alias Taskman.ChangeNotifications.Event
  alias Taskman.Projects

  test "create_project/1 trims and persists a name-only Project" do
    assert {:ok, project} = Projects.create_project(%{name: "  Taskman  "})
    assert project.name == "Taskman"
    assert {project.description, project.icon, project.color} == {"", "briefcase", "#6366F1"}
  end

  test "create_project/1 trims description and normalizes a custom color" do
    assert {:ok, project} =
             Projects.create_project(%{
               name: "Alpha",
               description: "  First release  ",
               icon: "rocket-launch",
               color: "#a1b2c3"
             })

    assert {project.description, project.icon, project.color} ==
             {"First release", "rocket-launch", "#A1B2C3"}

    assert Projects.get_project(project.id) == project
  end

  test "create_project/1 normalizes empty description to an empty string" do
    assert {:ok, project} = Projects.create_project(%{name: "Alpha", description: "   "})
    assert project.description == ""
  end

  test "create_project/1 returns validation errors for null identity input" do
    assert {:error, changeset} =
             Projects.create_project(%{name: "Alpha", description: nil, color: nil})

    assert %{description: [_], color: [_]} = errors_on(changeset)
  end

  test "create_project/1 validates description length, icon, and color" do
    assert {:error, changeset} =
             Projects.create_project(%{name: "Alpha", description: String.duplicate("a", 161)})

    assert %{description: [_]} = errors_on(changeset)

    for icon <- [
          "check-circle",
          "folder",
          "briefcase",
          "code-bracket",
          "rocket-launch",
          "beaker",
          "light-bulb",
          "wrench-screwdriver"
        ] do
      assert {:ok, project} = Projects.create_project(%{name: "Alpha", icon: icon})
      assert project.icon == icon
    end

    assert {:error, changeset} = Projects.create_project(%{name: "Alpha", icon: "unknown"})
    assert %{icon: [_]} = errors_on(changeset)

    for color <- ["a1b2c3", "#abc", "#gggggg", "#1234567"] do
      assert {:error, changeset} = Projects.create_project(%{name: "Alpha", color: color})
      assert %{color: [_]} = errors_on(changeset)
    end
  end

  test "update_project/2 persists partial and full edits and returns canonical no-op" do
    assert {:ok, project} = Projects.create_project(%{"name" => "  Alpha  "})

    assert {project.name, project.description, project.icon, project.color} ==
             {"Alpha", "", "briefcase", "#6366F1"}

    assert {:ok, updated} = Projects.update_project(project, %{"color" => "#a1b2c3"})

    assert {updated.name, updated.description, updated.icon, updated.color} ==
             {"Alpha", "", "briefcase", "#A1B2C3"}

    assert {:ok, updated} =
             Projects.update_project(updated, %{
               "name" => "  Beta  ",
               "description" => "  Next  ",
               "icon" => "beaker",
               "color" => "#123abc"
             })

    assert {updated.name, updated.description, updated.icon, updated.color} ==
             {"Beta", "Next", "beaker", "#123ABC"}

    assert {:ok, ^updated} = Projects.update_project(updated, %{})
    assert Projects.get_project(updated.id) == updated
  end

  test "a no-op update returns the persisted Project when the caller holds stale data" do
    assert {:ok, stale} = Projects.create_project(%{name: "Alpha"})
    assert {:ok, current} = Projects.update_project(stale, %{description: "Current"})
    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder("workspace:changes")

    assert {:ok, ^current} = Projects.update_project(stale, %{})
    refute_receive {:forwarded, %Event{}}, 50
  end

  test "updates return error changesets when the Project was deleted" do
    assert {:ok, stale} = Projects.create_project(%{name: "Alpha"})
    assert {:ok, _deleted} = Repo.delete(stale)
    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder("workspace:changes")

    assert {:error, %Ecto.Changeset{} = changeset} = Projects.update_project(stale, %{})
    assert %{base: [_]} = errors_on(changeset)

    assert {:error, %Ecto.Changeset{} = changeset} =
             Projects.update_project(stale, %{name: "Changed"})

    assert %{base: [_]} = errors_on(changeset)
    refute_receive {:forwarded, %Event{}}, 50
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
                      fields: [:name, :description, :icon, :color]
                    }}

    assert project_id == project.id
  end

  test "update_project/2 publishes only changed persisted fields" do
    assert {:ok, project} = Projects.create_project(%{name: "Alpha"})
    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder("workspace:changes")

    assert {:ok, updated} =
             Projects.update_project(project, %{
               name: "  Beta  ",
               description: "  Notes  ",
               icon: "beaker",
               color: "#a1b2c3"
             })

    assert_receive {:forwarded,
                    %Event{
                      entity: :project,
                      operation: :updated,
                      project_id: project_id,
                      entity_id: entity_id,
                      fields: [:name, :description, :icon, :color]
                    }}

    assert project_id == updated.id
    assert entity_id == updated.id

    assert {:ok, updated} =
             Projects.update_project(updated, %{description: "Notes", color: "#A1B2C3"})

    refute_receive {:forwarded, %Event{}}, 50

    assert {:ok, updated} = Projects.update_project(updated, %{description: ""})
    assert_receive {:forwarded, %Event{operation: :updated, fields: [:description]}}

    assert {:ok, ^updated} = Projects.update_project(updated, %{})
    refute_receive {:forwarded, %Event{}}, 50
  end

  test "invalid Project update publishes no event" do
    assert {:ok, project} = Projects.create_project(%{name: "Alpha"})
    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder("workspace:changes")

    assert {:error, changeset} = Projects.update_project(project, %{icon: "unknown"})
    assert %{icon: [_]} = errors_on(changeset)
    assert Projects.get_project(project.id) == project
    refute_receive {:forwarded, %Event{}}, 50
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
