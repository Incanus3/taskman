defmodule Taskman.Lists.ActionsTest do
  use Taskman.DataCase, async: false

  alias Phoenix.PubSub
  alias Taskman.ChangeNotifications
  alias Taskman.ChangeNotifications.Event

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures

  alias Taskman.Lists

  test "creates arbitrarily nested Lists and returns stable root-to-node paths" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    leaf = list_fixture(project, child, %{name: "Copy"})

    assert Lists.list_lists_for_project(project) == [root, child, leaf]
    assert Lists.path_for(Lists.list_lists_for_project(project), leaf) == [root, child, leaf]
  end

  test "rejects duplicate sibling names without regard to case" do
    project = project_fixture(%{})
    _existing = list_fixture(project, nil, %{name: "Planning"})

    assert {:error, changeset} = Lists.create_list(project, nil, %{name: "planning"})
    assert %{name: [_]} = errors_on(changeset)
  end

  test "allows the same name under different parents" do
    project = project_fixture(%{})
    first_parent = list_fixture(project, nil, %{name: "First"})
    second_parent = list_fixture(project, nil, %{name: "Second"})

    assert {:ok, first_child} = Lists.create_list(project, first_parent, %{name: "Shared"})
    assert {:ok, second_child} = Lists.create_list(project, second_parent, %{name: "shared"})
    assert first_child.parent_list_id == first_parent.id
    assert second_child.parent_list_id == second_parent.id
  end

  test "rejects a parent from another Project" do
    project = project_fixture(%{})
    other_parent = list_fixture(project_fixture(%{}), nil)

    assert {:error, :not_found} = Lists.create_list(project, other_parent, %{name: "Child"})
  end

  test "root and child List creation publish workspace events with ownership fields" do
    project = project_fixture(%{})
    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder("workspace:changes")

    assert {:ok, root} = Lists.create_list(project, nil, %{name: "Planning"})

    assert_receive {:forwarded,
                    %Event{
                      entity: :list,
                      operation: :created,
                      project_id: project_id,
                      entity_id: root_id,
                      fields: [:name, :project_id, :parent_list_id]
                    }}

    assert project_id == project.id
    assert root_id == root.id

    assert {:ok, child} = Lists.create_list(project, root, %{name: "Launch"})

    assert_receive {:forwarded,
                    %Event{
                      entity: :list,
                      operation: :created,
                      project_id: ^project_id,
                      entity_id: child_id,
                      fields: [:name, :project_id, :parent_list_id]
                    }}

    assert child_id == child.id
  end

  test "an invalid List creation or foreign parent publishes no workspace event" do
    project = project_fixture(%{})
    other_parent = list_fixture(project_fixture(%{}), nil)
    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder("workspace:changes")

    assert {:error, _changeset} = Lists.create_list(project, nil, %{name: "   "})
    assert {:error, :not_found} = Lists.create_list(project, other_parent, %{name: "Child"})
    refute_receive {:forwarded, %Event{}}, 50
  end

  test "successful List creation keeps its result when publication fails" do
    project = project_fixture(%{})
    previous = Application.get_env(:taskman, :change_notifications_pubsub)
    Application.put_env(:taskman, :change_notifications_pubsub, Taskman.MissingPubSub)

    on_exit(fn ->
      if previous == nil do
        Application.delete_env(:taskman, :change_notifications_pubsub)
      else
        Application.put_env(:taskman, :change_notifications_pubsub, previous)
      end
    end)

    assert {:ok, task_list} = Lists.create_list(project, nil, %{name: "Planning"})
    assert task_list.project_id == project.id
  end

  test "gets only Project-owned Lists and rejects malformed IDs" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Owned"})
    other_list = list_fixture(other_project, nil, %{name: "Other"})

    assert Lists.get_list_for_project(project, task_list.id) == task_list
    assert Lists.get_list_for_project(project, Integer.to_string(task_list.id)) == task_list
    assert Lists.get_list_for_project(project, other_list.id) == nil
    assert Lists.get_list_for_project(project, "not-an-id") == nil
    assert Lists.get_list_for_project(project, "#{task_list.id}tail") == nil
    assert Lists.get_list_for_project(project, 0) == nil
    assert Lists.get_list_for_project(project, -1) == nil
    assert Lists.get_list_for_project(project, nil) == nil
  end

  test "validates, trims, and protects List ownership fields" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    parent = list_fixture(project, nil, %{name: "Parent"})

    assert {:ok, task_list} =
             Lists.create_list(project, nil, %{
               name: "  Root  ",
               project_id: other_project.id,
               parent_list_id: parent.id
             })

    assert task_list.name == "Root"
    assert task_list.project_id == project.id
    assert task_list.parent_list_id == nil

    assert {:error, changeset} = Lists.create_list(project, nil, %{name: "   "})
    assert %{name: [_]} = errors_on(changeset)

    assert {:error, changeset} =
             Lists.create_list(project, nil, %{name: String.duplicate("x", 256)})

    assert %{name: [_]} = errors_on(changeset)
  end

  test "renames a List while keeping ownership immutable" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Before"})
    other_parent = list_fixture(other_project, nil, %{name: "Other parent"})

    assert {:ok, renamed} =
             Lists.rename_list(project, task_list, %{
               name: "  After  ",
               project_id: other_project.id,
               parent_list_id: other_parent.id
             })

    assert renamed.name == "After"
    assert renamed.project_id == project.id
    assert renamed.parent_list_id == nil
    assert {:error, :not_found} = Lists.rename_list(other_project, task_list, %{name: "Leaked"})
    assert Lists.get_list_for_project(project, task_list.id).name == "After"
  end

  test "a changed List name publishes an updated workspace event" do
    project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Before"})
    project_id = project.id
    task_list_id = task_list.id
    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder("workspace:changes")

    assert {:ok, renamed} = Lists.rename_list(project, task_list, %{name: "After"})

    assert_receive {:forwarded,
                    %Event{
                      entity: :list,
                      operation: :updated,
                      project_id: ^project_id,
                      entity_id: ^task_list_id,
                      fields: [:name]
                    }}

    assert renamed.name == "After"
  end

  test "a rename whose normalized name is unchanged returns success without an event" do
    project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Before"})
    assert :ok = ChangeNotifications.subscribe_workspace()
    start_forwarder("workspace:changes")

    assert {:ok, unchanged} = Lists.rename_list(project, task_list, %{name: "  Before  "})
    assert unchanged.name == task_list.name
    refute_receive {:forwarded, %Event{}}, 50
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
