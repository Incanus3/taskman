defmodule Taskman.Tasks.BlockingPersistenceTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks.BlockingLink
  alias Taskman.Tasks.BlockingPersistence

  test "an ordered link can connect Tasks in different Projects" do
    blocker = task_fixture(project_fixture(%{}))
    blocked = task_fixture(project_fixture(%{}))

    assert {:ok, link} = BlockingPersistence.insert(blocker.id, blocked.id)
    assert %BlockingLink{blocking_task_id: blocker_id, blocked_task_id: blocked_id} = link
    assert {blocker_id, blocked_id} == {blocker.id, blocked.id}
    assert [%BlockingLink{id: link_id}] = BlockingPersistence.outgoing(blocker.id)
    assert [%BlockingLink{id: ^link_id}] = BlockingPersistence.incoming(blocked.id)
    assert BlockingPersistence.incoming(blocker.id) == []
    assert BlockingPersistence.outgoing(blocked.id) == []
  end

  test "the same ordered pair is unique while direction remains significant" do
    blocker = task_fixture(project_fixture(%{}))
    blocked = task_fixture(project_fixture(%{}))

    assert {:ok, _forward} = BlockingPersistence.insert(blocker.id, blocked.id)
    assert {:error, duplicate} = BlockingPersistence.insert(blocker.id, blocked.id)
    assert :blocking_task_id in Keyword.keys(duplicate.errors)
    assert {:ok, _reverse} = BlockingPersistence.insert(blocked.id, blocker.id)
  end

  test "a Task cannot link to itself" do
    task = task_fixture(project_fixture(%{}))

    assert {:error, changeset} = BlockingPersistence.insert(task.id, task.id)
    assert :blocked_task_id in Keyword.keys(changeset.errors)
    assert BlockingPersistence.outgoing(task.id) == []
  end

  test "both endpoints are required and must reference existing Tasks" do
    blocker = task_fixture(project_fixture(%{}))
    blocked = task_fixture(project_fixture(%{}))

    for {from, to, field} <- [
          {nil, blocked.id, :blocking_task_id},
          {blocker.id, nil, :blocked_task_id},
          {0, blocked.id, :blocking_task_id},
          {blocker.id, 0, :blocked_task_id}
        ] do
      assert {:error, changeset} = BlockingPersistence.insert(from, to)
      assert field in Keyword.keys(changeset.errors)
    end
  end

  test "stored IDs are bigint and timestamps are UTC" do
    blocker = task_fixture(project_fixture(%{}))
    blocked = task_fixture(project_fixture(%{}))

    assert {:ok, link} = BlockingPersistence.insert(blocker.id, blocked.id)
    assert is_integer(link.id)
    assert %DateTime{time_zone: "Etc/UTC"} = link.inserted_at
    assert %DateTime{time_zone: "Etc/UTC"} = link.updated_at

    %{rows: rows} =
      Ecto.Adapters.SQL.query!(
        Repo,
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'task_blocking_links' AND column_name IN ('id', 'blocking_task_id', 'blocked_task_id') ORDER BY column_name",
        []
      )

    assert rows == [
             ["blocked_task_id", "bigint", "NO"],
             ["blocking_task_id", "bigint", "NO"],
             ["id", "bigint", "NO"]
           ]
  end

  test "both endpoint lookup indexes and the ordered-pair unique index exist" do
    %{rows: rows} =
      Ecto.Adapters.SQL.query!(
        Repo,
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() AND tablename = 'task_blocking_links'",
        []
      )

    definitions = Map.new(rows, fn [name, definition] -> {name, definition} end)

    assert definitions["task_blocking_links_blocking_task_id_index"] =~ "(blocking_task_id)"
    assert definitions["task_blocking_links_blocked_task_id_index"] =~ "(blocked_task_id)"

    assert definitions["task_blocking_links_blocking_task_id_blocked_task_id_index"] =~
             "UNIQUE INDEX"

    assert definitions["task_blocking_links_blocking_task_id_blocked_task_id_index"] =~
             "(blocking_task_id, blocked_task_id)"
  end

  test "deleting either endpoint cascades only its incident links" do
    project = project_fixture(%{})
    first = task_fixture(project)
    middle = task_fixture(project)
    last = task_fixture(project)

    assert {:ok, _} = BlockingPersistence.insert(first.id, middle.id)
    assert {:ok, _} = BlockingPersistence.insert(middle.id, last.id)
    assert {:ok, surviving} = BlockingPersistence.insert(last.id, first.id)

    Repo.delete!(middle)

    assert BlockingPersistence.outgoing(first.id) == []
    assert BlockingPersistence.incoming(last.id) == []
    assert Enum.map(BlockingPersistence.outgoing(last.id), & &1.id) == [surviving.id]
    assert Repo.get(Taskman.Tasks.Task, first.id)
    assert Repo.get(Taskman.Tasks.Task, last.id)

    Repo.delete!(first)

    assert BlockingPersistence.outgoing(last.id) == []
    assert Repo.get(Taskman.Tasks.Task, last.id)
  end

  test "deleting a link retains both endpoint Tasks" do
    blocker = task_fixture(project_fixture(%{}))
    blocked = task_fixture(project_fixture(%{}))
    assert {:ok, link} = BlockingPersistence.insert(blocker.id, blocked.id)

    assert {:ok, %BlockingLink{id: deleted_id}} = BlockingPersistence.delete(link)
    assert deleted_id == link.id
    assert BlockingPersistence.outgoing(blocker.id) == []
    assert Repo.get(Taskman.Tasks.Task, blocker.id)
    assert Repo.get(Taskman.Tasks.Task, blocked.id)
  end
end
