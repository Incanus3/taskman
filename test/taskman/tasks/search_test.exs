defmodule Taskman.Tasks.SearchTest do
  use Taskman.DataCase, async: true

  import Ecto.Query
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Search
  alias Taskman.Tasks.Task

  test "each term may match the decimal ID or title, including both fields" do
    project = project_fixture(%{})
    published = task_fixture(project, %{title: "Publish site"})
    other = task_fixture(project, %{title: "Other work"})
    id = Integer.to_string(published.id)

    assert task_ids("#{id} publish") == [published.id]
    assert task_ids("publish #{id}") == [published.id]
    assert task_ids("#{id} #{id}") == [published.id]
    refute other.id in task_ids("#{id} publish")
  end

  test "title terms ignore case and split on whitespace" do
    project = project_fixture(%{})
    matching = task_fixture(project, %{title: "Publish Site"})
    _other = task_fixture(project, %{title: "Publish Notes"})

    assert task_ids("  pUbLiSh\tSITE  ") == [matching.id]
  end

  test "Unicode whitespace separates title and ID terms" do
    project = project_fixture(%{})
    matching = task_fixture(project, %{title: "Publish Site"})
    _other = task_fixture(project, %{title: "Publish Notes"})

    for whitespace <- ["\u00A0", "\u2003", "\u202F", "\u3000"] do
      assert task_ids("#{whitespace}publish site#{whitespace}") == [matching.id]
      assert task_ids("publish#{whitespace}site") == [matching.id]
      assert task_ids("#{matching.id}#{whitespace}site") == [matching.id]
    end
  end

  test "search callers share Unicode separators and retain their blank-query behavior" do
    project = project_fixture(%{})
    matching = task_fixture(project, %{title: "Publish Site"})
    selected = task_fixture(project, %{title: "Selected"})
    _other = task_fixture(project, %{title: "Publish Notes"})

    for query <- ["\u00A0publish site\u00A0", "publish\u00A0site"] do
      assert Enum.map(Tasks.search_tasks(query, nil), & &1.id) == [matching.id]

      assert Enum.map(Tasks.search_blocking_candidates(project, selected, query), & &1.id) ==
               [matching.id]

      assert Enum.map(Tasks.search_parent_candidates(project, selected, query), & &1.task.id) ==
               [matching.id]
    end

    assert task_ids("\u00A0\u2003\u202F\u3000") == task_ids("")
    assert Tasks.search_tasks("\u00A0\u2003\u202F\u3000", nil) == []

    assert Tasks.search_blocking_candidates(project, selected, "\u00A0\u2003\u202F\u3000") ==
             Tasks.search_blocking_candidates(project, selected, "")

    assert Tasks.search_parent_candidates(project, selected, "\u00A0\u2003\u202F\u3000") ==
             Tasks.search_parent_candidates(project, selected, "")
  end

  test "percent and underscore are literal characters" do
    project = project_fixture(%{})
    percent = task_fixture(project, %{title: "100% done"})
    underscore = task_fixture(project, %{title: "review_stage"})
    _plain = task_fixture(project, %{title: "100x done reviewXstage"})

    assert task_ids("%") == [percent.id]
    assert task_ids("_") == [underscore.id]
  end

  test "blank terms leave caller eligibility unchanged" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Visible"})

    assert task_ids(" \n ") == [task.id]
  end

  test "global search covers Projects, statuses and complete owning paths with exact summaries" do
    beta = project_fixture(%{name: "beta"})
    alpha = project_fixture(%{name: "Alpha"})
    root = list_fixture(beta, %{name: "Release"})
    leaf = list_fixture(beta, root, %{name: "Copy"})

    second =
      task_fixture(beta, leaf, %{title: "publish", status: :will_not_do, priority: :urgent})

    first = task_fixture(alpha, %{title: "Publish", status: :done, priority: :high})

    assert second.id < first.id

    assert Tasks.search_tasks("PUBLISH", nil) == [
             %{
               id: first.id,
               title: "Publish",
               status: :done,
               priority: :high,
               project_id: alpha.id,
               project_name: "Alpha",
               location: %{kind: "project", list_id: nil, path: []}
             },
             %{
               id: second.id,
               title: "publish",
               status: :will_not_do,
               priority: :urgent,
               project_id: beta.id,
               project_name: "beta",
               location: %{kind: "list", list_id: leaf.id, path: ["Release", "Copy"]}
             }
           ]

    assert Enum.map(Tasks.search_tasks("publish", beta), & &1.id) == [second.id]
    assert Tasks.search_tasks(" \n ", nil) == []
  end

  test "global search matches literal terms, promotes exact ID, and caps stable ordering" do
    project = project_fixture(%{name: "Search"})
    exact = task_fixture(project, %{title: "Zebra %_"})
    early = task_fixture(project, %{title: "alpha %_"})
    later = task_fixture(project, %{title: "Alpha %_"})
    _plain = task_fixture(project, %{title: "alpha XX"})

    assert Enum.map(Tasks.search_tasks("%_", nil), & &1.id) == [early.id, later.id, exact.id]
    assert Enum.map(Tasks.search_tasks("#{exact.id} zebra", nil), & &1.id) == [exact.id]
    assert hd(Tasks.search_tasks(Integer.to_string(exact.id), nil)).id == exact.id

    for number <- 1..25, do: task_fixture(project, %{title: "Cap #{number}"})
    results = Tasks.search_tasks("Cap", nil)
    assert length(results) == 20

    assert Enum.map(results, & &1.title) ==
             Enum.sort_by(Enum.map(results, & &1.title), &String.downcase/1)
  end

  test "blocking candidates stay in the chosen Project across Lists and exclude selected Task" do
    project = project_fixture(%{name: "Chosen"})
    foreign = project_fixture(%{name: "Other"})
    list = list_fixture(project, %{name: "Phase"})
    selected = task_fixture(project, %{title: "Alpha"})
    root = task_fixture(project, %{title: "beta"})
    nested = task_fixture(project, list, %{title: "Beta"})
    _foreign = task_fixture(foreign, %{title: "Beta"})

    assert Enum.map(Tasks.search_blocking_candidates(project, selected, ""), & &1.id) == [
             root.id,
             nested.id
           ]

    assert Enum.map(Tasks.search_blocking_candidates(project, selected, "bEtA"), & &1.id) == [
             root.id,
             nested.id
           ]

    assert hd(Tasks.search_blocking_candidates(project, selected, Integer.to_string(root.id))).id ==
             root.id

    assert Enum.map(
             Tasks.search_blocking_candidates(project, selected, "#{nested.id} beta"),
             & &1.id
           ) == [nested.id]

    assert Enum.at(Tasks.search_blocking_candidates(project, selected, "beta"), 1).location ==
             %{kind: "list", list_id: list.id, path: ["Phase"]}
  end

  test "global search filters and limits Tasks in SQL with bounded location queries" do
    first_project = project_fixture(%{name: "First"})
    second_project = project_fixture(%{name: "Second"})
    task_fixture(first_project, list_fixture(first_project), %{title: "Needle one"})
    task_fixture(second_project, list_fixture(second_project), %{title: "Needle two"})
    test_pid = self()
    handler_id = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, %{query: sql}, ^test_pid ->
          if self() == test_pid and is_binary(sql) and String.starts_with?(sql, "SELECT") do
            send(test_pid, {:search_sql, sql})
          end
        end,
        test_pid
      )

    on_exit(fn -> :telemetry.detach(handler_id) end)

    assert length(Tasks.search_tasks("needle", nil)) == 2
    assert_receive {:search_sql, task_sql}
    assert task_sql =~ ~s(FROM "tasks")
    assert task_sql =~ "strpos("
    assert task_sql =~ "LIMIT"
    assert_receive {:search_sql, list_sql}
    assert list_sql =~ ~s(FROM "lists")
    refute_receive {:search_sql, _extra_sql}
  end

  test "blocking candidates use literal terms and stop at twenty results" do
    project = project_fixture(%{})
    selected = task_fixture(project, %{title: "Selected"})
    literal = task_fixture(project, %{title: "100%_ ready"})
    _plain = task_fixture(project, %{title: "100xx ready"})

    assert Enum.map(Tasks.search_blocking_candidates(project, selected, "%_"), & &1.id) ==
             [literal.id]

    for number <- 1..25, do: task_fixture(project, %{title: "Candidate #{number}"})
    assert length(Tasks.search_blocking_candidates(project, selected, "Candidate")) == 20
  end

  defp task_ids(query) do
    Task
    |> Search.filter_by_terms(query)
    |> order_by([task], asc: task.id)
    |> Repo.all()
    |> Enum.map(& &1.id)
  end
end
