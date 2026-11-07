defmodule Taskman.Tasks.DescendantDoneConfirmationTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Conflict
  alias Taskman.Tasks.Hierarchy
  alias Taskman.Tasks.Status

  test "Done reviews unfinished descendants through non-resolving ancestors" do
    project = project_fixture(%{})
    root = task_fixture(project)
    child = task_fixture(project, %{title: "A child"}, parent: root)
    grandchild = task_fixture(project, %{title: "B grandchild"}, parent: child)

    assert {:error, {:unresolved_blockers, summaries}} =
             Tasks.update_task(project, root, %{title: "Changed", status: :done})

    assert Enum.map(summaries, & &1.id) == [child.id, grandchild.id]
    assert Tasks.get_task_for_project(project, root.id).title == root.title
    assert Tasks.get_task_for_project(project, root.id).status == :pending
  end

  test "resolving descendants stop the review of their branches" do
    project = project_fixture(%{})
    root = task_fixture(project)
    child = task_fixture(project, %{status: :done}, parent: root)
    grandchild = task_fixture(project, %{}, parent: child)

    assert {:ok, %{status: :done}} = Tasks.update_task(project, root, %{status: :done})
    assert Tasks.get_task_for_project(project, grandchild.id).status == :pending
  end

  for status <- Status.values() do
    test "a #{status} child follows the resolving classification" do
      project = project_fixture(%{})
      root = task_fixture(project)
      child = task_fixture(project, %{status: unquote(status), title: "A"}, parent: root)
      grandchild = task_fixture(project, %{title: "B"}, parent: child)

      if Status.resolves_blocker?(unquote(status)) do
        assert Hierarchy.unresolved_descendant_ids(root) == []
        assert {:ok, %{status: :done}} = Tasks.update_task(project, root, %{status: :done})
      else
        assert {:error, {:unresolved_blockers, summaries}} =
                 Tasks.update_task(project, root, %{status: :done})

        assert Enum.map(summaries, & &1.id) == [child.id, grandchild.id]
      end
    end
  end

  test "non-resolving terminal descendants remain included through each other" do
    project = project_fixture(%{})
    root = task_fixture(project)
    child = task_fixture(project, %{title: "A", status: :not_planned}, parent: root)
    grandchild = task_fixture(project, %{title: "B", status: :will_not_do}, parent: child)

    assert {:error, {:unresolved_blockers, summaries}} =
             Tasks.update_task(project, root, %{status: :done})

    assert Enum.map(summaries, & &1.id) == [child.id, grandchild.id]
  end

  test "review crosses Lists and sibling branches while pruning nested resolving nodes" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    first_list = list_fixture(project, nil, %{name: "First"})
    second_list = list_fixture(project, first_list, %{name: "Second"})
    ancestor = task_fixture(project)
    root = task_fixture(project, first_list, %{}, parent: ancestor)
    child = task_fixture(project, second_list, %{title: "alpha"}, parent: root)
    sibling = task_fixture(project, %{title: "Beta"}, parent: root)
    grandchild = task_fixture(project, %{title: "gamma"}, parent: sibling)
    boundary = task_fixture(project, %{status: :done}, parent: child)
    _hidden = task_fixture(project, %{}, parent: boundary)
    _unrelated = task_fixture(project)
    _foreign = task_fixture(other_project)

    assert {:error, {:unresolved_blockers, summaries}} =
             Tasks.update_task(project, root, %{status: :done})

    assert Enum.map(summaries, & &1.id) == [child.id, sibling.id, grandchild.id]

    assert hd(summaries).location == %{
             kind: "list",
             list_id: second_list.id,
             path: ["First", "Second"]
           }

    assert Hierarchy.unresolved_descendant_ids(%{root | project_id: other_project.id}) == []
  end

  test "direct blockers beneath a resolving boundary remain in the combined review set" do
    project = project_fixture(%{name: "Zulu"})
    other_project = project_fixture(%{name: "Alpha"})
    root = task_fixture(project)
    boundary = task_fixture(project, %{status: :done}, parent: root)
    hidden_blocker = task_fixture(project, %{title: "beta"}, parent: boundary)
    dual = task_fixture(project, %{title: "Alpha"}, parent: root)
    foreign_blocker = task_fixture(other_project)
    assert {:ok, _} = Tasks.add_block(project, hidden_blocker, root)
    assert {:ok, _} = Tasks.add_block(project, dual, root)
    assert {:ok, _} = Tasks.add_block(other_project, foreign_blocker, root)

    assert {:error, {:unresolved_blockers, summaries}} =
             Tasks.update_task(project, root, %{status: :done})

    assert Enum.map(summaries, & &1.id) == [foreign_blocker.id, dual.id, hidden_blocker.id]

    assert {:ok, %{status: :done}} =
             Tasks.update_task(project, root, %{status: :done},
               done_confirmation: {:ids, [foreign_blocker.id, dual.id, hidden_blocker.id]}
             )
  end

  test "missing descendant confirmation leaves every field and parent unchanged" do
    project = project_fixture(%{})
    root = task_fixture(project)
    child = task_fixture(project, %{title: "A child"}, parent: root)
    blocker = task_fixture(project, %{title: "B blocker"})
    parent = task_fixture(project)
    assert {:ok, _} = Tasks.add_block(project, blocker, root)

    attrs = %{
      title: "Changed",
      description: "Updated",
      priority: :urgent,
      due_at: ~N[2026-11-08 12:00:00],
      status: :done
    }

    assert {:error, {:unresolved_blockers, summaries}} =
             Tasks.update_task(project, root, attrs,
               parent: parent,
               done_confirmation: {:ids, [blocker.id]}
             )

    assert Enum.map(summaries, & &1.id) == [child.id, blocker.id]
    assert Tasks.get_task_for_project(project, root.id) == root

    assert {:ok, updated} =
             Tasks.update_task(project, root, attrs,
               parent: parent,
               done_confirmation: {:ids, [child.id, blocker.id]}
             )

    for {field, value} <- attrs, do: assert(Map.fetch!(updated, field) == value)
    assert updated.parent_task_id == parent.id
  end

  test "force accepts the combined set without modifying descendants" do
    project = project_fixture(%{})
    root = task_fixture(project)
    child = task_fixture(project, %{}, parent: root)
    blocker = task_fixture(project)
    assert {:ok, _} = Tasks.add_block(project, blocker, root)

    assert {:ok, %{status: :done}} =
             Tasks.update_task(project, root, %{status: :done}, done_confirmation: :force)

    assert Tasks.get_task_for_project(project, child.id) == child
    assert Tasks.get_task_for_project(project, blocker.id) == blocker
  end

  test "previously confirmed descendant sets may shrink or become empty" do
    project = project_fixture(%{})

    for emptied? <- [false, true] do
      root = task_fixture(project)
      child = task_fixture(project, %{title: "A"}, parent: root)
      sibling = task_fixture(project, %{title: "B"}, parent: root)

      assert {:error, {:unresolved_blockers, summaries}} =
               Tasks.update_task(project, root, %{status: :done})

      confirmed = Enum.map(summaries, & &1.id)
      assert confirmed == [child.id, sibling.id]
      assert {:ok, _} = Tasks.update_task(project, child, %{status: :done})
      if emptied?, do: assert({:ok, _} = Tasks.update_task(project, sibling, %{status: :done}))

      assert {:ok, %{status: :done}} =
               Tasks.update_task(project, root, %{status: :done},
                 done_confirmation: {:ids, confirmed}
               )
    end
  end

  test "reopening a resolving boundary exposes its whole unfinished branch" do
    project = project_fixture(%{})
    root = task_fixture(project)
    known = task_fixture(project, %{title: "A"}, parent: root)
    boundary = task_fixture(project, %{title: "B", status: :done}, parent: root)
    grandchild = task_fixture(project, %{title: "C"}, parent: boundary)

    assert {:error, {:unresolved_blockers, [%{id: known_id}]}} =
             Tasks.update_task(project, root, %{status: :done})

    assert known_id == known.id
    assert {:ok, _} = Tasks.update_task(project, boundary, %{status: :pending})

    assert {:error, {:unresolved_blockers, summaries}} =
             Tasks.update_task(project, root, %{status: :done},
               done_confirmation: {:ids, [known.id]}
             )

    assert Enum.map(summaries, & &1.id) == [known.id, boundary.id, grandchild.id]
  end

  test "new children require fresh confirmation between requests" do
    project = project_fixture(%{})
    root = task_fixture(project)
    known = task_fixture(project, %{title: "A"}, parent: root)

    assert {:error, {:unresolved_blockers, [_]}} =
             Tasks.update_task(project, root, %{status: :done})

    added = task_fixture(project, %{title: "B"}, parent: root)

    assert {:error, {:unresolved_blockers, summaries}} =
             Tasks.update_task(project, root, %{status: :done},
               done_confirmation: {:ids, [known.id]}
             )

    assert Enum.map(summaries, & &1.id) == [known.id, added.id]
    assert Tasks.get_task_for_project(project, root.id).status == :pending
  end

  test "reparented subtrees require fresh confirmation between requests" do
    project = project_fixture(%{})
    root = task_fixture(project)
    known = task_fixture(project, %{title: "A"}, parent: root)
    subtree = task_fixture(project, %{title: "B"})
    grandchild = task_fixture(project, %{title: "C"}, parent: subtree)

    assert {:error, {:unresolved_blockers, [_]}} =
             Tasks.update_task(project, root, %{status: :done})

    assert {:ok, _} = Tasks.update_task(project, subtree, %{}, parent: root)

    assert {:error, {:unresolved_blockers, summaries}} =
             Tasks.update_task(project, root, %{status: :done},
               done_confirmation: {:ids, [known.id]}
             )

    assert Enum.map(summaries, & &1.id) == [known.id, subtree.id, grandchild.id]
  end

  test "unfinished child creation and reparenting do not reopen Done Tasks" do
    project = project_fixture(%{})
    root = task_fixture(project, %{status: :done})
    _child = task_fixture(project, %{}, parent: root)
    subtree = task_fixture(project)
    assert {:ok, _} = Tasks.update_task(project, subtree, %{}, parent: root)
    assert Tasks.get_task_for_project(project, root.id) == root

    assert {:ok, same} = Tasks.update_task(project, root, %{status: :done})
    assert same.lock_version == root.lock_version
    assert {:ok, edited} = Tasks.update_task(project, same, %{title: "Changed", status: :done})
    assert edited.status == :done
  end

  test "descendant confirmation does not bypass invalid or stale updates" do
    project = project_fixture(%{})
    root = task_fixture(project)
    child = task_fixture(project, %{}, parent: root)

    for confirmation <- [{:ids, [child.id]}, :force] do
      assert {:error, changeset} =
               Tasks.update_task(project, root, %{title: "", status: :done},
                 done_confirmation: confirmation
               )

      assert %{title: [_]} = errors_on(changeset)
    end

    assert {:ok, _} = Tasks.update_task(project, root, %{status: :in_review})

    for confirmation <- [{:ids, [child.id]}, :force] do
      assert {:error, %Conflict{fields: [:status]}} =
               Tasks.update_task(project, root, %{status: :done}, done_confirmation: confirmation)
    end

    assert Tasks.get_task_for_project(project, root.id).status == :in_review
  end

  test "descendant traversal and combined warning query counts do not grow with the tree" do
    project = project_fixture(%{})
    first_list = list_fixture(project, nil, %{name: "First"})
    second_list = list_fixture(project, first_list, %{name: "Second"})
    small = task_fixture(project)
    small_child = task_fixture(project, %{}, parent: small)
    wide = task_fixture(project)
    left = task_fixture(project, first_list, %{}, parent: wide)
    right = task_fixture(project, second_list, %{}, parent: wide)
    deep = task_fixture(project, second_list, %{}, parent: left)
    deeper = task_fixture(project, %{}, parent: deep)
    boundary = task_fixture(project, %{status: :done}, parent: right)
    hidden = task_fixture(project, %{}, parent: boundary)
    _hidden_deeper = task_fixture(project, %{}, parent: hidden)
    trees = [{small, [small_child.id]}, {wide, [left.id, right.id, deep.id, deeper.id]}]

    counts =
      for {root, expected_ids} <- trees do
        {ids, helper_queries} =
          capture_queries(fn -> Hierarchy.unresolved_descendant_ids(root) end)

        assert Enum.sort(ids) == Enum.sort(expected_ids)
        assert length(helper_queries) == 1

        {result, warning_queries} =
          capture_queries(fn -> Tasks.update_task(project, root, %{status: :done}) end)

        assert {:error, {:unresolved_blockers, summaries}} = result
        assert Enum.sort(Enum.map(summaries, & &1.id)) == Enum.sort(expected_ids)
        length(warning_queries)
      end

    assert [small_count, wide_count] = counts
    assert small_count == wide_count
  end

  defp capture_queries(fun) do
    test_pid = self()
    handler_id = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, %{query: query}, _config ->
          send(test_pid, {:captured_query, query})
        end,
        nil
      )

    try do
      result = fun.()
      {result, drain_captured_queries([])}
    after
      :telemetry.detach(handler_id)
    end
  end

  defp drain_captured_queries(queries) do
    receive do
      {:captured_query, query} -> drain_captured_queries([query | queries])
    after
      0 -> Enum.reverse(queries)
    end
  end
end
