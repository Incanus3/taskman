defmodule Taskman.Tasks.DoneConfirmationTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Ecto.Adapters.SQL
  alias Ecto.Adapters.SQL.Sandbox
  alias Taskman.Accounts.User
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Conflict

  @graph_lock_key {724_150, 1}

  setup do
    existing_user_ids = Repo.all(from u in User, select: u.id)

    on_exit(fn ->
      Sandbox.unboxed_run(Repo, fn ->
        Repo.delete_all(from u in User, where: u.id not in ^existing_user_ids)
      end)
    end)

    :ok
  end

  test "Done warns for unresolved direct blockers and rolls back a mixed update" do
    project = project_fixture(%{})
    blocker = task_fixture(project, %{title: "Needs review", priority: :urgent})
    indirect = task_fixture(project, %{title: "Indirect"})
    blocked = task_fixture(project, %{title: "Before"})

    assert {:ok, _} =
             Tasks.add_block(project, indirect, blocker,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:ok, _} =
             Tasks.add_block(project, blocker, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:error, {:unresolved_blockers, [summary]}} =
             Tasks.update_task(project, blocked, %{title: "After", status: :done},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert summary.id == blocker.id
    assert summary.title == "Needs review"
    assert summary.priority == :urgent
    assert summary.status == :pending
    assert summary.project_id == project.id
    assert Tasks.get_task_for_project(project, blocked.id).title == "Before"
    assert Tasks.get_task_for_project(project, blocked.id).status == :pending
    assert Tasks.get_task_for_project(project, indirect.id).status == :pending
  end

  test "resolved direct blockers and indirect unresolved blockers allow Done" do
    project = project_fixture(%{})
    done_blocker = task_fixture(project, %{status: :done})
    indirect = task_fixture(project)
    blocked = task_fixture(project)

    assert {:ok, _} =
             Tasks.add_block(project, indirect, done_blocker,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:ok, _} =
             Tasks.add_block(project, done_blocker, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:ok, updated} =
             Tasks.update_task(project, blocked, %{status: :done},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert updated.status == :done
    assert Tasks.get_task_for_project(project, indirect.id).status == :pending
  end

  for state <- [:in_analysis, :not_planned, :will_not_do] do
    test "#{state} remains unresolved and requires explicit Done confirmation" do
      project = project_fixture(%{})

      assert {:ok, blocker} =
               Tasks.create_task(project, nil, %{title: "Closed blocker", status: unquote(state)},
                 actor: Taskman.AccountsFixtures.user_fixture()
               )

      blocked = task_fixture(project)

      assert {:ok, _} =
               Tasks.add_block(project, blocker, blocked,
                 actor: Taskman.AccountsFixtures.user_fixture()
               )

      assert {:error, {:unresolved_blockers, [summary]}} =
               Tasks.update_task(project, blocked, %{status: :done},
                 actor: Taskman.AccountsFixtures.user_fixture()
               )

      assert summary.id == blocker.id
      assert summary.status == unquote(state)
      assert Tasks.get_task_for_project(project, blocked.id).status == :pending

      assert {:ok, %{status: :done}} =
               Tasks.update_task(project, blocked, %{status: :done},
                 done_confirmation: {:ids, [blocker.id]},
                 actor: Taskman.AccountsFixtures.user_fixture()
               )
    end
  end

  test "an already Done Task needs no confirmation for a no-op or ordinary edit" do
    project = project_fixture(%{})
    blocker = task_fixture(project)
    blocked = task_fixture(project, %{status: :done})

    assert {:ok, _} =
             Tasks.add_block(project, blocker, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:ok, same} =
             Tasks.update_task(project, blocked, %{status: :done},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert same.lock_version == blocked.lock_version

    assert {:ok, edited} =
             Tasks.update_task(project, same, %{title: "Edited"},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert edited.status == :done
  end

  test "other status changes and added links never change a dependent Task status" do
    project = project_fixture(%{})
    blocker = task_fixture(project)
    blocked = task_fixture(project, %{status: :done})

    assert {:ok, _} =
             Tasks.add_block(project, blocker, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert Tasks.get_task_for_project(project, blocked.id).status == :done

    assert {:ok, reopened} =
             Tasks.update_task(project, blocked, %{status: :in_progress},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert reopened.status == :in_progress

    assert {:ok, _} =
             Tasks.update_task(project, blocker, %{status: :done},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert Tasks.get_task_for_project(project, blocked.id).status == :in_progress
  end

  test "known blocker IDs can confirm the first request and an equal current set" do
    project = project_fixture(%{})
    blocker = task_fixture(project)
    blocked = task_fixture(project)

    assert {:ok, _} =
             Tasks.add_block(project, blocker, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:ok, updated} =
             Tasks.update_task(project, blocked, %{title: "Done now", status: :done},
               done_confirmation: {:ids, [blocker.id]},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert updated.title == "Done now"
    assert updated.status == :done
  end

  test "confirmation remains valid as the unresolved set diminishes or empties" do
    project = project_fixture(%{})
    first = task_fixture(project)
    second = task_fixture(project)
    diminished = task_fixture(project)
    empty = task_fixture(project)

    for blocked <- [diminished, empty], blocker <- [first, second] do
      assert {:ok, _} =
               Tasks.add_block(project, blocker, blocked,
                 actor: Taskman.AccountsFixtures.user_fixture()
               )
    end

    assert {:ok, _} =
             Tasks.update_task(project, first, %{status: :done},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:ok, updated} =
             Tasks.update_task(project, diminished, %{status: :done},
               done_confirmation: {:ids, [first.id, second.id]},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert updated.status == :done

    assert {:ok, _} =
             Tasks.update_task(project, second, %{status: :done},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:ok, updated} =
             Tasks.update_task(project, empty, %{status: :done},
               done_confirmation: {:ids, [first.id, second.id]},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert updated.status == :done
  end

  test "a new unconfirmed blocker returns the full current list without writing fields" do
    project = project_fixture(%{})
    first = task_fixture(project, %{title: "A"})
    second = task_fixture(project, %{title: "B"})
    blocked = task_fixture(project, %{title: "Before"})

    assert {:ok, _} =
             Tasks.add_block(project, first, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:ok, _} =
             Tasks.add_block(project, second, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:error, {:unresolved_blockers, summaries}} =
             Tasks.update_task(project, blocked, %{title: "After", status: :done},
               done_confirmation: {:ids, [first.id]},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert Enum.map(summaries, & &1.id) == [first.id, second.id]
    assert Tasks.get_task_for_project(project, blocked.id).title == "Before"
    assert Tasks.get_task_for_project(project, blocked.id).status == :pending
  end

  test "force permits Done with unresolved blockers after the blocker set changes" do
    project = project_fixture(%{})
    blocker = task_fixture(project)
    blocked = task_fixture(project)

    assert {:ok, _} =
             Tasks.add_block(project, blocker, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:ok, updated} =
             Tasks.update_task(project, blocked, %{status: :done},
               done_confirmation: :force,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert updated.status == :done
    assert Tasks.get_task_for_project(project, blocker.id).status == :pending
  end

  test "confirmation and force preserve optimistic same-field conflicts" do
    project = project_fixture(%{})
    blocker = task_fixture(project)
    blocked = task_fixture(project, %{status: :pending})

    assert {:ok, _} =
             Tasks.add_block(project, blocker, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    {first_baseline, stale_baseline} = loaded_task_baselines(project, blocked)

    assert {:ok, _} =
             Tasks.update_task(project, first_baseline, %{status: :in_review},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    for confirmation <- [{:ids, [blocker.id]}, :force] do
      assert {:error, %Conflict{fields: [:status]}} =
               Tasks.update_task(project, stale_baseline, %{status: :done},
                 done_confirmation: confirmation,
                 actor: Taskman.AccountsFixtures.user_fixture()
               )
    end

    assert Tasks.get_task_for_project(project, blocked.id).status == :in_review
  end

  test "confirmation and force retain ordinary validation failures" do
    project = project_fixture(%{})
    blocker = task_fixture(project)
    blocked = task_fixture(project, %{title: "Before"})

    assert {:ok, _} =
             Tasks.add_block(project, blocker, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    for confirmation <- [{:ids, [blocker.id]}, :force] do
      assert {:error, changeset} =
               Tasks.update_task(project, blocked, %{title: "", status: :done},
                 done_confirmation: confirmation,
                 actor: Taskman.AccountsFixtures.user_fixture()
               )

      assert %{title: [_]} = errors_on(changeset)
    end

    assert Tasks.get_task_for_project(project, blocked.id).status == :pending
    assert Tasks.get_task_for_project(project, blocked.id).title == "Before"
  end

  test "Done with a parent change warns without persisting either change" do
    project = project_fixture(%{})
    parent = task_fixture(project)
    blocker = task_fixture(project)
    blocked = task_fixture(project, %{title: "Before"})

    assert {:ok, _} =
             Tasks.add_block(project, blocker, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:error, {:unresolved_blockers, [_summary]}} =
             Tasks.update_task(project, blocked, %{title: "After", status: :done},
               parent: parent,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    persisted = Tasks.get_task_for_project(project, blocked.id)
    assert persisted.parent_task_id == nil
    assert persisted.title == "Before"
    assert persisted.status == :pending
  end

  test "confirmed Done persists atomically with a parent change" do
    project = project_fixture(%{})
    parent = task_fixture(project)
    blocker = task_fixture(project)
    blocked = task_fixture(project, %{title: "Before"})

    assert {:ok, _} =
             Tasks.add_block(project, blocker, blocked,
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert {:ok, updated} =
             Tasks.update_task(project, blocked, %{title: "After", status: :done},
               parent: parent,
               done_confirmation: {:ids, [blocker.id]},
               actor: Taskman.AccountsFixtures.user_fixture()
             )

    assert updated.title == "After"
    assert updated.status == :done
    assert updated.parent_task_id == parent.id
  end

  test "a queued Done write warns for a blocker added before it takes the graph lock" do
    Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      blocker = task_fixture(project)
      blocked = task_fixture(project, %{title: "Before"})
      cleanup_project(project)

      result =
        queued_done_write(
          fn ->
            Tasks.update_task(project, blocked, %{title: "After", status: :done},
              actor: Taskman.AccountsFixtures.user_fixture()
            )
          end,
          fn ->
            assert {:ok, _} =
                     Tasks.add_block(project, blocker, blocked,
                       actor: Taskman.AccountsFixtures.user_fixture()
                     )
          end
        )

      assert {:error, {:unresolved_blockers, [%{id: blocker_id}]}} = result
      assert blocker_id == blocker.id
      assert Repo.get!(Tasks.Task, blocked.id).title == "Before"
      assert Repo.get!(Tasks.Task, blocked.id).status == :pending
    end)
  end

  test "a queued confirmed Done write rejects a newly added unconfirmed blocker" do
    Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      first = task_fixture(project, %{title: "A"})
      second = task_fixture(project, %{title: "B"})
      blocked = task_fixture(project, %{title: "Before"})

      assert {:ok, _} =
               Tasks.add_block(project, first, blocked,
                 actor: Taskman.AccountsFixtures.user_fixture()
               )

      cleanup_project(project)

      result =
        queued_done_write(
          fn ->
            Tasks.update_task(project, blocked, %{title: "After", status: :done},
              done_confirmation: {:ids, [first.id]},
              actor: Taskman.AccountsFixtures.user_fixture()
            )
          end,
          fn ->
            assert {:ok, _} =
                     Tasks.add_block(project, second, blocked,
                       actor: Taskman.AccountsFixtures.user_fixture()
                     )
          end
        )

      assert {:error, {:unresolved_blockers, summaries}} = result
      assert Enum.map(summaries, & &1.id) == [first.id, second.id]
      assert Repo.get!(Tasks.Task, blocked.id).title == "Before"
      assert Repo.get!(Tasks.Task, blocked.id).status == :pending
    end)
  end

  test "a queued confirmed Done write warns when a linked blocker becomes unresolved" do
    Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      first = task_fixture(project, %{title: "A"})
      second = task_fixture(project, %{title: "B", status: :done})
      blocked = task_fixture(project, %{title: "Before"})

      assert {:ok, _} =
               Tasks.add_block(project, first, blocked,
                 actor: Taskman.AccountsFixtures.user_fixture()
               )

      assert {:ok, _} =
               Tasks.add_block(project, second, blocked,
                 actor: Taskman.AccountsFixtures.user_fixture()
               )

      cleanup_project(project)

      result =
        queued_done_write(
          fn ->
            Tasks.update_task(project, blocked, %{title: "After", status: :done},
              done_confirmation: {:ids, [first.id]},
              actor: Taskman.AccountsFixtures.user_fixture()
            )
          end,
          fn ->
            assert {:ok, _} =
                     Tasks.update_task(project, second, %{status: :pending},
                       actor: Taskman.AccountsFixtures.user_fixture()
                     )
          end
        )

      assert {:error, {:unresolved_blockers, summaries}} = result
      assert Enum.map(summaries, & &1.id) == [first.id, second.id]
      assert Enum.map(summaries, & &1.status) == [:pending, :pending]
      assert Repo.get!(Tasks.Task, blocked.id).title == "Before"
      assert Repo.get!(Tasks.Task, blocked.id).status == :pending
    end)
  end

  test "a queued confirmed Done write accepts a blocker resolved before it takes the lock" do
    Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      blocker = task_fixture(project)
      blocked = task_fixture(project)

      assert {:ok, _} =
               Tasks.add_block(project, blocker, blocked,
                 actor: Taskman.AccountsFixtures.user_fixture()
               )

      cleanup_project(project)

      result =
        queued_done_write(
          fn ->
            Tasks.update_task(project, blocked, %{status: :done},
              done_confirmation: {:ids, [blocker.id]},
              actor: Taskman.AccountsFixtures.user_fixture()
            )
          end,
          fn ->
            assert {:ok, _} =
                     Tasks.update_task(project, blocker, %{status: :done},
                       actor: Taskman.AccountsFixtures.user_fixture()
                     )
          end
        )

      assert {:ok, %{status: :done}} = result
      assert Repo.get!(Tasks.Task, blocked.id).status == :done
    end)
  end

  test "a queued forced Done write permits a newly added blocker" do
    Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      blocker = task_fixture(project)
      blocked = task_fixture(project)
      cleanup_project(project)

      result =
        queued_done_write(
          fn ->
            Tasks.update_task(project, blocked, %{status: :done},
              done_confirmation: :force,
              actor: Taskman.AccountsFixtures.user_fixture()
            )
          end,
          fn ->
            assert {:ok, _} =
                     Tasks.add_block(project, blocker, blocked,
                       actor: Taskman.AccountsFixtures.user_fixture()
                     )
          end
        )

      assert {:ok, %{status: :done}} = result
      assert Repo.get!(Tasks.Task, blocked.id).status == :done
      assert Repo.get!(Tasks.Task, blocker.id).status == :pending
    end)
  end

  test "a queued confirmed Done write sees a reopened descendant boundary" do
    Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      root = task_fixture(project, %{title: "Before"})
      known = task_fixture(project, %{title: "A"}, parent: root)
      boundary = task_fixture(project, %{title: "B", status: :done}, parent: root)
      grandchild = task_fixture(project, %{title: "C"}, parent: boundary)
      cleanup_project(project)

      result =
        queued_done_write(
          fn ->
            Tasks.update_task(project, root, %{title: "After", status: :done},
              done_confirmation: {:ids, [known.id]},
              actor: Taskman.AccountsFixtures.user_fixture()
            )
          end,
          fn ->
            assert {:ok, _} =
                     Tasks.update_task(project, boundary, %{status: :pending},
                       actor: Taskman.AccountsFixtures.user_fixture()
                     )
          end
        )

      assert {:error, {:unresolved_blockers, summaries}} = result
      assert Enum.map(summaries, & &1.id) == [known.id, boundary.id, grandchild.id]
      assert Enum.map(summaries, & &1.status) == [:pending, :pending, :pending]
      assert Repo.get!(Tasks.Task, root.id) == root
    end)
  end

  test "a queued confirmed Done write sees a reparented unfinished subtree" do
    Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      root = task_fixture(project, %{title: "Before"})
      known = task_fixture(project, %{title: "A"}, parent: root)
      subtree = task_fixture(project, %{title: "B"})
      grandchild = task_fixture(project, %{title: "C"}, parent: subtree)
      cleanup_project(project)

      result =
        queued_done_write(
          fn ->
            Tasks.update_task(project, root, %{title: "After", status: :done},
              done_confirmation: {:ids, [known.id]},
              actor: Taskman.AccountsFixtures.user_fixture()
            )
          end,
          fn ->
            assert {:ok, _} =
                     Tasks.update_task(project, subtree, %{},
                       parent: root,
                       actor: Taskman.AccountsFixtures.user_fixture()
                     )
          end
        )

      assert {:error, {:unresolved_blockers, summaries}} = result
      assert Enum.map(summaries, & &1.id) == [known.id, subtree.id, grandchild.id]
      assert Repo.get!(Tasks.Task, root.id) == root
    end)
  end

  defp queued_done_write(operation, before_release) do
    {first_key, second_key} = @graph_lock_key
    SQL.query!(Repo, "SELECT pg_advisory_lock($1, $2)", [first_key, second_key])

    try do
      owner_backend = database_backend_pid()
      test_pid = self()
      supervisor = start_supervised!(Task.Supervisor)

      {:ok, worker} =
        Task.Supervisor.start_child(supervisor, fn ->
          :ok = Sandbox.checkout(Repo, sandbox: false)

          try do
            send(test_pid, {:done_worker_ready, self(), database_backend_pid()})

            receive do
              :start_done_write -> send(test_pid, {:done_worker_result, self(), operation.()})
            end
          after
            :ok = Sandbox.checkin(Repo)
          end
        end)

      ref = Process.monitor(worker)
      assert_receive {:done_worker_ready, ^worker, worker_backend}, 5_000
      send(worker, :start_done_write)
      await_blocked(worker_backend, owner_backend)
      before_release.()

      assert %{rows: [[true]]} =
               SQL.query!(Repo, "SELECT pg_advisory_unlock($1, $2)", [
                 first_key,
                 second_key
               ])

      assert_receive {:done_worker_result, ^worker, result}, 5_000
      assert_receive {:DOWN, ^ref, :process, ^worker, :normal}, 5_000
      result
    after
      SQL.query!(Repo, "SELECT pg_advisory_unlock($1, $2)", [first_key, second_key])
    end
  end

  defp database_backend_pid do
    %{rows: [[backend]]} = SQL.query!(Repo, "SELECT pg_backend_pid()")
    backend
  end

  defp await_blocked(worker_backend, owner_backend) do
    deadline = System.monotonic_time(:millisecond) + 5_000
    poll_blocked(worker_backend, owner_backend, deadline)
  end

  defp poll_blocked(worker_backend, owner_backend, deadline) do
    %{rows: [[blockers]]} =
      SQL.query!(Repo, "SELECT pg_blocking_pids($1::integer)", [worker_backend])

    cond do
      owner_backend in blockers ->
        :ok

      System.monotonic_time(:millisecond) < deadline ->
        poll_blocked(worker_backend, owner_backend, deadline)

      true ->
        flunk("Done writer did not wait for the graph lock")
    end
  end

  defp cleanup_project(project) do
    on_exit(fn -> delete_project(project) end)
  end

  defp delete_project(project) do
    Sandbox.unboxed_run(Repo, fn ->
      if persisted = Repo.get(Project, project.id), do: Repo.delete!(persisted)
    end)
  end
end
