defmodule TaskmanWeb.ProjectLive.ExpandableTaskListingTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks

  setup %{conn: conn}, do: {:ok, conn: log_in_user(conn, user_fixture())}

  test "disclosure reveals excluded descendants and collapses the complete group", %{conn: conn} do
    %{project: project, selected: selected, root: a, middle: b, leaf: c} = hierarchy()
    {:ok, view, _} = live(conn, listing_path(project, selected))

    assert row_ids(view) == [a.id]

    assert has_element?(
             view,
             "#toggle-task-subtasks-#{a.id}[aria-expanded='false'][aria-label='Expand subtasks for A']"
           )

    toggle(view, a)
    assert row_ids(view) == [a.id, b.id, c.id]

    assert has_element?(
             view,
             "#toggle-task-subtasks-#{a.id}[aria-expanded='true'][aria-label='Collapse subtasks for A']"
           )

    assert_depths(view, [{a, 0}, {b, 1}, {c, 2}])
    refute has_element?(view, "#toggle-task-subtasks-#{b.id}")
    refute has_element?(view, "#task-location-cell-#{b.id}")
    refute has_element?(view, "#task-modal-content")
    refute_patched(view, listing_path(project, selected))
    toggle(view, a)
    assert row_ids(view) == [a.id]
  end

  test "location identity resets expansion but detail navigation does not", %{conn: conn} do
    %{project: project, selected: selected, root: a, middle: b, leaf: c} = hierarchy()
    {:ok, view, _} = live(conn, listing_path(project, selected))
    toggle(view, a)
    view |> element("#open-task-#{b.id}") |> render_click()
    assert has_element?(view, "#task-modal-content")
    assert row_ids(view) == [a.id, b.id, c.id]
    render_patch(view, listing_path(project, selected))
    assert row_ids(view) == [a.id, b.id, c.id]
    render_patch(view, "/projects/#{project.id}")
    render_patch(view, listing_path(project, selected))
    assert row_ids(view) == [a.id]
    assert has_element?(view, "#toggle-task-subtasks-#{a.id}[aria-expanded='false']")
  end

  test "status filters preserve promoted branches and returning ancestors", %{conn: conn} do
    %{project: project, selected: selected, root: a, middle: b, leaf: c} = hierarchy()
    child = task_fixture(project, selected, %{title: "D"}, parent: c)
    {:ok, a} = Tasks.update_task(project, a, %{status: :in_review})
    {:ok, view, _} = live(conn, listing_path(project, selected, "in_review,pending"))
    toggle(view, a)
    filter(view, ["pending"])
    assert row_ids(view) == [c.id, child.id]
    assert_depths(view, [{c, 0}, {child, 1}])
    assert has_element?(view, "#toggle-task-subtasks-#{c.id}[aria-expanded='true']")
    assert has_element?(view, "#location-task-count-value", "2 / 3")
    filter(view, ["in_review", "pending"])
    assert row_ids(view) == [a.id, b.id, c.id, child.id]
    assert_depths(view, [{a, 0}, {b, 1}, {c, 2}, {child, 3}])
    assert has_element?(view, "#toggle-task-subtasks-#{a.id}[aria-expanded='true']")
    assert has_element?(view, "#location-task-count-value", "3 / 3")
  end

  test "child inclusion preserves promoted branches and returning ancestors", %{conn: conn} do
    project = project_fixture(%{})
    selected = list_fixture(project, %{name: "Selected"})
    child_list = list_fixture(project, selected, %{name: "Child"})
    a = task_fixture(project, child_list, %{title: "A"})
    b = task_fixture(project, selected, %{title: "B"}, parent: a)
    c = task_fixture(project, child_list, %{title: "C"}, parent: b)
    {:ok, view, _} = live(conn, listing_path(project, selected) <> "&include_children=true")
    toggle(view, a)
    assert row_ids(view) == [a.id, b.id, c.id]
    assert has_element?(view, "#task-location-cell-#{a.id}", "Child")
    assert has_element?(view, "#task-location-cell-#{b.id}", "Selected")
    view |> element("#include-child-lists") |> render_click()
    assert row_ids(view) == [b.id, c.id]
    assert_depths(view, [{b, 0}, {c, 1}])
    assert has_element?(view, "#toggle-task-subtasks-#{b.id}[aria-expanded='true']")
    refute has_element?(view, "#task-location-cell-#{b.id}")
    view |> element("#include-child-lists") |> render_click()
    assert row_ids(view) == [a.id, b.id, c.id]
    assert_depths(view, [{a, 0}, {b, 1}, {c, 2}])
    assert has_element?(view, "#toggle-task-subtasks-#{a.id}[aria-expanded='true']")
    assert has_element?(view, "#task-location-cell-#{c.id}", "Child")
    assert Tasks.get_task_for_project(project, a.id).list_id == child_list.id
    assert Tasks.get_task_for_project(project, b.id).list_id == selected.id
    assert Tasks.get_task_for_project(project, c.id).list_id == child_list.id
  end

  test "empty filters preserve expansion but location changes reset it", %{conn: conn} do
    %{project: project, selected: selected, root: a, middle: b, leaf: c} = hierarchy()
    other_project = project_fixture(%{})
    {:ok, view, _} = live(conn, listing_path(project, selected))
    toggle(view, a)
    memory = MapSet.new([a.id, b.id])
    filter(view, [])
    assert row_ids(view) == []
    assert assigns(view).listing.tasks_empty?
    assert assigns(view).listing.tasks_filtered_empty?
    assert assigns(view).listing.expanded_task_ids == memory
    filter(view, ["pending"])
    assert row_ids(view) == [a.id, b.id, c.id]
    assert has_element?(view, "#toggle-task-subtasks-#{a.id}[aria-expanded='true']")
    render_patch(view, "/projects/#{other_project.id}")
    render_patch(view, listing_path(project, selected))
    assert row_ids(view) == [a.id]
    assert assigns(view).listing.expanded_task_ids == MapSet.new()
    toggle(view, a)
    render_patch(view, "/projects/#{project.id}/lists/999999999")
    assert assigns(view).listing.expanded_task_ids == MapSet.new()
    render_patch(view, listing_path(project, selected))
    assert row_ids(view) == [a.id]
    toggle(view, a)
    assert row_ids(view) == [a.id, b.id, c.id]
    {:ok, remounted, _} = live(conn, listing_path(project, selected))
    assert row_ids(remounted) == [a.id]
    assert has_element?(remounted, "#toggle-task-subtasks-#{a.id}[aria-expanded='false']")
  end

  test "stale root toggles preserve hidden ancestor memory", %{conn: conn} do
    %{project: project, selected: selected, root: a, middle: b, leaf: c} = hierarchy()
    child = task_fixture(project, selected, %{}, parent: c)
    {:ok, view, _} = live(conn, listing_path(project, selected))
    toggle(view, a)
    memory = MapSet.new([a.id, b.id, c.id])
    a |> Ecto.Changeset.change(status: :done) |> Taskman.Repo.update!()
    toggle(view, a)
    assert row_ids(view) == [c.id, child.id]
    assert assigns(view).listing.expanded_task_ids == memory
    assert has_element?(view, "#toggle-task-subtasks-#{c.id}[aria-expanded='true']")
    filter(view, ["pending", "done"])
    assert row_ids(view) == [a.id, b.id, c.id, child.id]
    assert has_element?(view, "#toggle-task-subtasks-#{a.id}[aria-expanded='true']")
  end

  test "child inclusion and delayed preference hydration preserve surviving expansion", %{
    conn: conn
  } do
    %{project: project, selected: selected, root: a, middle: b, leaf: c} = hierarchy()
    child_list = list_fixture(project, selected)
    extra = task_fixture(project, child_list, %{})
    {:ok, view, _} = live(conn, listing_path(project, selected))
    toggle(view, a)
    view |> element("#include-child-lists") |> render_click()
    assert row_ids(view) == [a.id, b.id, c.id, extra.id]
    view |> element("#include-child-lists") |> render_click()
    assert row_ids(view) == [a.id, b.id, c.id]

    render_hook(view, "hydrate_task_table_preferences", %{
      "include_children" => true,
      "statuses" => ["done"]
    })

    assert row_ids(view) == [a.id, b.id, c.id, extra.id]
    assert has_element?(view, "#toggle-task-subtasks-#{a.id}[aria-expanded='true']")
    assert has_element?(view, "#task-location-cell-#{b.id}")
  end

  test "delayed status hydration preserves expansion at the same location", %{conn: conn} do
    %{project: project, selected: selected, root: a, middle: b, leaf: c} = hierarchy()
    {:ok, view, _} = live(conn, "/projects/#{project.id}/lists/#{selected.id}")
    toggle(view, a)

    render_hook(view, "hydrate_task_table_preferences", %{
      "include_children" => false,
      "statuses" => ["pending", "done"]
    })

    assert row_ids(view) == [a.id, b.id, c.id]
    assert has_element?(view, "#toggle-task-subtasks-#{a.id}[aria-expanded='true']")
    assert assigns(view).listing.visible_statuses == [:pending, :done]
  end

  test "invalid disclosure targets leave visibility unchanged", %{conn: conn} do
    %{project: project, selected: selected, root: a, middle: b, leaf: c} = hierarchy()
    foreign = task_fixture(project_fixture(%{}), %{})
    {:ok, view, _} = live(conn, listing_path(project, selected))
    toggle(view, a)

    for target <- [
          "garbage",
          "#{a.id}x",
          "+#{a.id}",
          " #{a.id}",
          "0",
          "-1",
          "",
          b.id,
          c.id,
          foreign.id,
          999_999_999
        ] do
      render_click(view, "toggle_task_subtasks", %{"task-id" => to_string(target)})
      assert row_ids(view) == [a.id, b.id, c.id]
      assert has_element?(view, "#toggle-task-subtasks-#{a.id}[aria-expanded='true']")
    end

    Taskman.Repo.delete!(c)
    Taskman.Repo.delete!(b)
    Taskman.Repo.delete!(a)
    notify(view, project, a)
    assert row_ids(view) == []
    render_click(view, "toggle_task_subtasks", %{"task-id" => to_string(a.id)})
    assert row_ids(view) == []
    assert assigns(view).listing.expanded_task_ids == MapSet.new([a.id, b.id])
  end

  test "expansion and sorting preserve counts without count queries", %{conn: conn} do
    %{project: project, selected: selected, root: a} = hierarchy()
    {:ok, view, _} = live(conn, listing_path(project, selected))
    assert has_element?(view, "#location-task-count-value", "2 / 2")
    assert has_element?(view, "#list-task-count-#{selected.id}-value", "2")
    before_counts = assigns(view).listing.task_counts
    receiver = self()
    handler = "expansion-counts-#{System.unique_integer([:positive])}"

    :telemetry.attach(
      handler,
      [:taskman, :repo, :query],
      fn _, _, metadata, pid ->
        if self() == pid, do: send(receiver, {:listing_query, metadata.query})
      end,
      view.pid
    )

    on_exit(fn -> :telemetry.detach(handler) end)
    toggle(view, a)
    view |> element("#sort-task-title") |> render_click()
    assert has_element?(view, "#toggle-task-subtasks-#{a.id}[aria-expanded='true']")
    toggle(view, a)
    assert row_ids(view) == [a.id]
    assert assigns(view).listing.task_counts == before_counts
    assert has_element?(view, "#location-task-count-value", "2 / 2")
    assert has_element?(view, "#list-task-count-#{selected.id}-value", "2")
    queries = drain_queries()
    assert queries != []

    refute Enum.any?(
             queries,
             &(String.contains?(&1, "IS NOT DISTINCT FROM") or
                 String.contains?(&1, ~s(SELECT DISTINCT t0."list_id")))
           )
  end

  test "external changes refresh expanded groups", %{conn: conn} do
    %{project: project, selected: selected, root: a, middle: b, leaf: c} = hierarchy()
    other = list_fixture(project)
    {:ok, view, _} = live(conn, listing_path(project, selected))
    toggle(view, a)
    d = task_fixture(project, %{title: "D"}, parent: b)
    notify(view, project, d, :created)
    assert row_ids(view) == [a.id, b.id, c.id, d.id]
    {:ok, d} = Tasks.update_task(project, d, %{title: "Changed", status: :done})
    notify(view, project, d)
    assert has_element?(view, "#task-#{d.id}", "Changed")
    assert has_element?(view, "#task-status-#{d.id}", "Done")
    {:ok, d} = Tasks.move_task(project, d, other)
    notify(view, project, d, :moved)
    assert row_ids(view) == [a.id, b.id, c.id, d.id]
    {:ok, d} = Tasks.update_task(project, d, %{}, parent: a)
    notify(view, project, d)
    assert_depths(view, [{d, 1}])
    assert row_ids(view) == [a.id, b.id, c.id, d.id]
    Taskman.Repo.delete!(c)
    notify(view, project, c)
    assert row_ids(view) == [a.id, b.id, d.id]
    Taskman.Repo.delete!(b)
    Taskman.Repo.delete!(d)
    notify(view, project, d)
    assert row_ids(view) == [a.id]
    refute has_element?(view, "#toggle-task-subtasks-#{a.id}")
    assert assigns(view).listing.expanded_task_ids == MapSet.new()
  end

  test "nested Task actions preserve browse context", %{conn: conn} do
    %{project: project, selected: selected, root: a, middle: b, leaf: c} = hierarchy()
    {:ok, view, _} = live(conn, listing_path(project, selected))
    toggle(view, a)
    view |> element("#open-task-#{b.id}") |> render_click()
    assert_patch(view, "/projects/#{project.id}/lists/#{selected.id}/tasks/#{b.id}")
    assert assigns(view).editing.selected_task.id == b.id
    render_patch(view, listing_path(project, selected))
    view |> element("#add-subtask-#{b.id}") |> render_click()

    assert_patch(
      view,
      "/projects/#{project.id}/lists/#{selected.id}/tasks/new?parent_task_id=#{b.id}"
    )

    assert has_element?(view, "#task-form")
    assert has_element?(view, "#task-parent-trigger", "B")
    render_patch(view, listing_path(project, selected))
    assert row_ids(view) == [a.id, b.id, c.id]
  end

  defp hierarchy do
    project = project_fixture(%{})
    selected = list_fixture(project)
    other = list_fixture(project)
    a = task_fixture(project, selected, %{title: "A", status: :pending})
    b = task_fixture(project, other, %{title: "B", status: :done}, parent: a)
    c = task_fixture(project, selected, %{title: "C", status: :pending}, parent: b)
    %{project: project, selected: selected, root: a, middle: b, leaf: c}
  end

  defp listing_path(project, selected, statuses \\ "pending"),
    do: "/projects/#{project.id}/lists/#{selected.id}?statuses=#{statuses}"

  defp toggle(view, task),
    do: view |> element("#toggle-task-subtasks-#{task.id}") |> render_click()

  defp assigns(view), do: :sys.get_state(view.pid).socket.assigns

  defp row_ids(view),
    do:
      view
      |> render()
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#tasks > article")
      |> LazyHTML.attribute("id")
      |> Enum.map(&(String.replace_prefix(&1, "tasks-", "") |> String.to_integer()))

  defp assert_depths(view, values),
    do:
      Enum.each(values, fn {task, depth} ->
        assert has_element?(view, "#tasks-#{task.id}[data-task-depth='#{depth}']")
      end)

  defp filter(view, statuses) do
    render_click(view, "filter_task_statuses", %{"status_filter" => %{"statuses" => statuses}})
  end

  defp notify(view, project, task, operation \\ :updated) do
    send(view.pid, %Event{
      entity: :task,
      operation: operation,
      project_id: project.id,
      entity_id: task.id,
      lock_version: task.lock_version,
      fields: [:title, :status, :parent_task_id, :list_id]
    })

    _ = :sys.get_state(view.pid)
  end

  defp drain_queries do
    receive do
      {:listing_query, query} -> [query | drain_queries()]
    after
      0 -> []
    end
  end
end
