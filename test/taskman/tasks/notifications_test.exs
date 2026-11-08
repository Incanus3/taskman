defmodule Taskman.Tasks.NotificationsTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Conflict
  import Taskman.AccountsFixtures
  alias Taskman.ChangeNotifications
  alias Taskman.ChangeNotifications.Event

  test "Task creation publishes its full persisted mutation metadata after success" do
    project = project_fixture(%{})
    topic = subscribe_task_events(project)

    assert {:ok, task} = create_task(project, nil, %{title: "Published", status: :not_planned})

    refute_receive %Event{}, 50

    assert_receive {:task_event, ^topic,
                    %Event{
                      entity: :task,
                      operation: :created,
                      status: :not_planned,
                      list_id: nil,
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
             update_task(project, task, %{description: "Changed", status: :not_planned})

    assert_receive {:task_event, ^topic,
                    %Event{
                      entity: :task,
                      operation: :updated,
                      entity_id: task_id,
                      lock_version: lock_version,
                      status: :not_planned,
                      list_id: nil,
                      fields: [:description, :status]
                    }}

    assert task_id == updated.id
    assert lock_version == updated.lock_version
  end

  test "title-only updates include the final persisted state and List" do
    project = project_fixture(%{})
    location = list_fixture(project)
    task = task_fixture(project, location, %{title: "Before", status: :not_planned})
    topic = subscribe_task_events(project)

    assert {:ok, updated} = update_task(project, task, %{title: "After"})

    assert_receive {:task_event, ^topic,
                    %Event{
                      entity: :task,
                      operation: :updated,
                      entity_id: id,
                      status: :not_planned,
                      list_id: list_id,
                      lock_version: revision,
                      fields: [:title]
                    }}

    assert {id, list_id, revision} == {updated.id, location.id, updated.lock_version}
  end

  test "parent mutations publish parent_task_id with their ordinary changed fields" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Before", status: :not_planned})
    topic = subscribe_task_events(project)

    assert {:ok, updated} = update_task(project, child, %{title: "After"}, parent: parent)

    assert_receive {:task_event, ^topic,
                    %Event{
                      operation: :updated,
                      entity_id: task_id,
                      lock_version: lock_version,
                      status: :not_planned,
                      list_id: nil,
                      fields: [:parent_task_id, :title]
                    }}

    assert task_id == updated.id
    assert lock_version == updated.lock_version
  end

  test "Task movement publishes only list_id after persistence" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me", status: :not_planned})
    topic = subscribe_task_events(project)

    assert {:ok, moved} = move_task(project, task, destination)

    assert_receive {:task_event, ^topic,
                    %Event{
                      operation: :moved,
                      entity_id: task_id,
                      lock_version: lock_version,
                      status: :not_planned,
                      list_id: destination_id,
                      fields: [:list_id]
                    }}

    assert task_id == moved.id
    assert lock_version == moved.lock_version
    assert destination_id == destination.id

    assert {:ok, direct} = move_task(project, moved, nil)

    assert_receive {:task_event, ^topic,
                    %Event{operation: :moved, entity_id: id, status: :not_planned, list_id: nil}}

    assert id == direct.id
  end

  test "failed, conflicting, and unchanged Task mutations publish no event" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Before"})
    topic = subscribe_task_events(project)

    assert {:error, _changeset} = create_task(project, nil, %{title: "", status: :not_planned})

    refute_receive {:task_event, ^topic, %Event{}}, 50

    assert {:error, _changeset} = create_task(project, nil, %{title: ""}, parent: task)

    refute_receive {:task_event, ^topic, %Event{}}, 50

    assert {:error, _changeset} = update_task(project, task, %{title: ""})

    refute_receive {:task_event, ^topic, %Event{}}, 50

    assert {:error, _changeset} = update_task(project, task, %{}, parent: task)

    refute_receive {:task_event, ^topic, %Event{}}, 50

    {first_baseline, second_baseline} = loaded_task_baselines(project, task)

    assert {:ok, updated} = update_task(project, first_baseline, %{title: "First writer"})

    assert_receive {:task_event, ^topic, %Event{entity: :task, entity_id: updated_id}}
    assert_receive {:task_event, ^topic, %Event{entity: :activity}}
    assert updated_id == updated.id

    assert {:error, %{__struct__: Conflict}} =
             update_task(project, second_baseline, %{title: "Second writer"})

    refute_receive {:task_event, ^topic, %Event{}}, 50

    assert {:ok, moved} = move_task(project, updated, destination)

    assert_receive {:task_event, ^topic, %Event{operation: :moved}}
    assert_receive {:task_event, ^topic, %Event{entity: :activity}}

    assert {:error, :unchanged_location} = move_task(project, moved, destination)

    refute_receive {:task_event, ^topic, %Event{}}, 50
  end

  test "a committed comment publishes one refetchable comment event and no Task event" do
    project = project_fixture(%{})
    task = task_fixture(project)
    actor = user_fixture()
    topic = subscribe_task_events(project)

    assert {:ok, comment} = Tasks.create_comment(project, task, actor, %{text: "Posted"})

    assert_receive {:task_event, ^topic,
                    %Event{
                      entity: :comment,
                      operation: :created,
                      project_id: project_id,
                      task_id: task_id,
                      entity_id: comment_id
                    }}

    assert {project_id, task_id, comment_id} == {project.id, task.id, comment.id}
    assert {:ok, [%{id: ^comment_id, text: "Posted"}]} = Tasks.list_comments(project, task)
    refute_receive {:task_event, ^topic, %Event{}}, 50
  end

  test "rejected comment creation publishes no event" do
    project = project_fixture(%{})
    task = task_fixture(project)
    actor = user_fixture()
    topic = subscribe_task_events(project)

    assert {:error, %Ecto.Changeset{}} =
             Tasks.create_comment(project, task, actor, %{text: "  "})

    refute_receive {:task_event, ^topic, %Event{}}, 50
  end

  test "link mutations invalidate each endpoint Project after success and deduplicate a shared Project" do
    first_project = project_fixture(%{})
    second_project = project_fixture(%{})
    source = task_fixture(first_project)
    same_project_target = task_fixture(first_project)
    foreign_target = task_fixture(second_project)
    first_topic = subscribe_task_events(first_project)
    second_topic = subscribe_task_events(second_project)

    assert {:ok, _edge} = add_block(first_project, source, same_project_target)

    assert_receive {:task_event, ^first_topic,
                    %Event{entity: :relationship, operation: :invalidated, task_ids: task_ids}}

    assert task_ids == [source.id, same_project_target.id]
    refute_receive {:task_event, ^first_topic, %Event{entity: :relationship}}, 50
    refute_receive {:task_event, ^second_topic, %Event{entity: :relationship}}, 50

    assert {:ok, _edge} = remove_block(first_project, source, same_project_target)

    assert_receive {:task_event, ^first_topic,
                    %Event{entity: :relationship, operation: :invalidated, task_ids: task_ids}}

    assert task_ids == [source.id, same_project_target.id]
    refute_receive {:task_event, ^first_topic, %Event{entity: :relationship}}, 50

    assert {:ok, _edge} = add_block(first_project, source, foreign_target)

    assert_receive {:task_event, ^first_topic,
                    %Event{entity: :relationship, operation: :invalidated, task_ids: [source_id]}}

    assert source_id == source.id

    assert_receive {:task_event, ^second_topic,
                    %Event{entity: :relationship, operation: :invalidated, task_ids: [target_id]}}

    assert target_id == foreign_target.id

    assert {:ok, _edge} = remove_block(first_project, source, foreign_target)

    assert_receive {:task_event, ^first_topic,
                    %Event{entity: :relationship, operation: :invalidated, task_ids: [source_id]}}

    assert source_id == source.id

    assert_receive {:task_event, ^second_topic,
                    %Event{entity: :relationship, operation: :invalidated, task_ids: [target_id]}}

    assert target_id == foreign_target.id
  end

  test "rejected link mutations publish no relationship invalidation" do
    project = project_fixture(%{})
    source = task_fixture(project)
    target = task_fixture(project)
    topic = subscribe_task_events(project)

    assert {:error, _} = add_block(project, source, source)

    refute_receive {:task_event, ^topic, %Event{entity: :relationship}}, 50

    assert {:ok, _edge} = add_block(project, source, target)

    assert_receive {:task_event, ^topic, %Event{entity: :relationship}}

    assert {:error, _} = add_block(project, source, target)

    assert {:error, :not_found} = remove_block(project, target, source)

    refute_receive {:task_event, ^topic, %Event{entity: :relationship}}, 50
  end

  test "linked Task field and location changes invalidate the other endpoint's local ID" do
    source_project = project_fixture(%{})
    target_project = project_fixture(%{})
    destination = list_fixture(source_project, nil, %{name: "Moved"})
    source = task_fixture(source_project, %{title: "Source"})
    target = task_fixture(target_project)

    assert {:ok, _edge} = add_block(source_project, source, target)

    source_topic = subscribe_task_events(source_project)
    target_topic = subscribe_task_events(target_project)

    for attrs <- [
          %{title: "Renamed"},
          %{priority: :high},
          %{status: :in_progress}
        ] do
      source = Tasks.get_task_for_project(source_project, source.id)

      assert {:ok, _updated} = update_task(source_project, source, attrs)

      assert_receive {:task_event, ^source_topic, %Event{entity: :task}}

      assert_receive {:task_event, ^target_topic,
                      %Event{
                        entity: :relationship,
                        operation: :invalidated,
                        task_ids: [target_id]
                      }}

      assert target_id == target.id
      refute_receive {:task_event, ^target_topic, %Event{entity: :task}}, 0
    end

    source = Tasks.get_task_for_project(source_project, source.id)

    assert {:ok, _moved} = move_task(source_project, source, destination)

    assert_receive {:task_event, ^source_topic, %Event{entity: :task, operation: :moved}}

    assert_receive {:task_event, ^target_topic,
                    %Event{entity: :relationship, operation: :invalidated, task_ids: [target_id]}}

    assert target_id == target.id
    refute_receive {:task_event, ^target_topic, %Event{entity: :task}}, 50
  end

  test "same-Project linked Task updates invalidate the other local Task ID" do
    project = project_fixture(%{})
    source = task_fixture(project)
    target = task_fixture(project)

    assert {:ok, _edge} = add_block(project, source, target)

    topic = subscribe_task_events(project)

    assert {:ok, _updated} = update_task(project, target, %{title: "Renamed"})

    assert_receive {:task_event, ^topic, %Event{entity: :task, entity_id: target_id}}
    assert target_id == target.id
    assert_receive {:task_event, ^topic, %Event{entity: :relationship, task_ids: [source_id]}}
    assert source_id == source.id
    refute_receive {:task_event, ^topic, %Event{entity: :relationship}}, 50
  end

  test "comment creation rejects an outer transaction without writing or publishing" do
    project = project_fixture(%{})
    task = task_fixture(project)
    actor = user_fixture()
    topic = subscribe_task_events(project)

    assert_raise ArgumentError, ~r/outside an existing transaction/, fn ->
      Repo.transaction(fn ->
        Tasks.create_comment(project, task, actor, %{text: "Rolled back"})
        Repo.rollback(:abort)
      end)
    end

    assert {:ok, []} = Tasks.list_comments(project, task)
    assert Tasks.get_task_for_project(project, task.id).updated_at == task.updated_at
    refute_receive {:task_event, ^topic, %Event{}}, 50
  end

  test "block history invalidates both Projects and cancellation refreshes both endpoint feeds" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    source = task_fixture(project)
    target = task_fixture(other_project)
    actor = user_fixture()
    source_topic = subscribe_task_events(project)
    target_topic = subscribe_task_events(other_project)

    for operation <- [&Tasks.add_block/4, &Tasks.remove_block/4] do
      assert {:ok, _} = operation.(project, source, target, actor: actor)

      assert_receive {:task_event, ^source_topic,
                      %Event{entity: :activity, operation: :invalidated, task_ids: [source_id]}}

      assert_receive {:task_event, ^target_topic,
                      %Event{entity: :activity, operation: :invalidated, task_ids: [target_id]}}

      assert source_id == source.id
      assert target_id == target.id
    end
  end

  test "reparent consolidation publishes the original intermediate final and child Tasks" do
    project = project_fixture(%{})
    original = task_fixture(project)
    middle = task_fixture(project)
    final = task_fixture(project)
    child = task_fixture(project, %{}, parent: original)
    actor = user_fixture()
    assert {:ok, changed} = Tasks.update_task(project, child, %{}, parent: middle, actor: actor)
    topic = subscribe_task_events(project)
    assert {:ok, _} = Tasks.update_task(project, changed, %{}, parent: final, actor: actor)
    assert_receive {:task_event, ^topic, %Event{entity: :activity, task_ids: ids}}
    assert MapSet.new(ids) == MapSet.new([child.id, original.id, middle.id, final.id])
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
