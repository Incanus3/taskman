defmodule TaskmanWeb.ProjectLive.TaskCountsTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "global filters update every location and expansion uses current counts", %{conn: conn} do
    project = project_fixture(%{})
    selected = list_fixture(project)
    sibling = list_fixture(project)
    child = list_fixture(project, sibling)
    empty = list_fixture(project)
    task_fixture(project, %{status: :pending})
    task_fixture(project, %{status: :in_review})
    task_fixture(project, selected, %{status: :pending})
    task_fixture(project, selected, %{status: :pending})
    task_fixture(project, selected, %{status: :in_review})
    task_fixture(project, sibling, %{status: :pending})
    task_fixture(project, child, %{status: :pending})
    task_fixture(project, child, %{status: :in_review})

    {:ok, view, _} =
      live(conn, ~p"/projects/#{project.id}/lists/#{selected.id}?statuses=pending")

    assert_count(view, selected, 2)
    assert_count(view, sibling, 1)
    assert_count(view, empty, 0)
    assert_project_count(view, 1)
    assert_header(view, 2, 3)

    assert has_element?(
             view,
             "#select-list-#{selected.id}[aria-describedby='list-task-count-#{selected.id}-description']"
           )

    assert has_element?(
             view,
             "#list-task-count-#{selected.id}-description",
             "2 tasks match the current filters"
           )

    assert has_element?(view, "#location-task-count-description", "3 eligible tasks")

    view |> element("#include-child-lists") |> render_click()
    assert_project_count(view, 5)
    assert_count(view, sibling, 2)
    assert_header(view, 2, 3)

    view |> element("#task-status-filter-button") |> render_click()

    view
    |> form("#task-status-filter-form", status_filter: %{statuses: ["in_review"]})
    |> render_change()

    assert_project_count(view, 3)
    assert_count(view, sibling, 1)
    assert_count(view, selected, 1)
    assert_header(view, 1, 3)

    view |> element("#toggle-list-#{sibling.id}") |> render_click()
    assert_count(view, child, 1)
    view |> element("#sort-task-title") |> render_click()
    assert_count(view, sibling, 1)
    assert_header(view, 1, 3)

    view |> form("#task-status-filter-form", status_filter: %{statuses: [""]}) |> render_change()
    assert_project_count(view, 0)
    assert_count(view, selected, 0)
    assert_header(view, 0, 3)
  end

  test "sorting refreshes task order without recounting or querying navigation", %{conn: conn} do
    project = project_fixture(%{})
    list = list_fixture(project)
    zebra = task_fixture(project, %{title: "Zebra", status: :pending})
    apple = task_fixture(project, %{title: "Apple", status: :pending})
    task_fixture(project, list, %{status: :pending})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}?statuses=pending")
    owner = self()
    handler_id = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _, _, metadata, {owner, pid} ->
          if self() == pid do
            send(owner, {:sort_query, metadata.query})
          end
        end,
        {owner, view.pid}
      )

    on_exit(fn -> :telemetry.detach(handler_id) end)

    for {first, second} <- [{apple, zebra}, {zebra, apple}] do
      view |> element("#sort-task-title") |> render_click()
      assert has_element?(view, "#tasks > #tasks-#{first.id} ~ #tasks-#{second.id}")
      assert_header(view, 2, 2)
      assert_count(view, list, 1)
      assert_project_count(view, 2)
      queries = drain_sort_queries()
      assert Enum.any?(queries, &String.contains?(&1, ~s(FROM "tasks")))
      refute Enum.any?(queries, &String.contains?(&1, "IS NOT DISTINCT FROM"))
      refute Enum.any?(queries, &String.contains?(&1, ~s(SELECT DISTINCT t0."list_id")))
      # Projected subtrees read List paths; navigation membership must remain untouched.
    end
  end

  test "Project switches, missing locations and empty populations cannot retain old totals", %{
    conn: conn
  } do
    first = project_fixture(%{})
    second = project_fixture(%{})
    empty = list_fixture(second)
    task_fixture(first, %{status: :pending})
    task_fixture(first, %{status: :pending})

    {:ok, view, _} = live(conn, ~p"/projects/#{first.id}?statuses=pending")
    assert_project_count(view, 2)
    assert_header(view, 2, 2)

    assert has_element?(
             view,
             "#project-tasks-link[aria-describedby='project-tasks-count-description']"
           )

    render_patch(view, ~p"/projects/#{second.id}")
    assert_project_count(view, 0)
    assert_header(view, 0, 0)
    render_patch(view, ~p"/projects/#{second.id}/lists/#{empty.id}")
    assert_header(view, 0, 0)
    render_patch(view, ~p"/projects/#{second.id}/lists/999999999")
    assert_project_count(view, 0)
    refute has_element?(view, "#location-task-count")
    render_patch(view, ~p"/projects/999999999")
    assert view_assigns(view).listing.task_counts == %{}
  end

  test "hydration preserves saved closed and empty selections and explicit routes win", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task_fixture(project, %{status: :pending})
    task_fixture(project, %{status: :done})

    {:ok, fresh, _} = live(conn, ~p"/projects/#{project.id}")
    assert_header(fresh, 1, 1)
    render_hook(fresh, "hydrate_task_table_preferences", %{"statuses" => ["done"]})
    assert_header(fresh, 1, 2)

    {:ok, empty, _} = live(conn, ~p"/projects/#{project.id}")
    render_hook(empty, "hydrate_task_table_preferences", %{"statuses" => []})
    assert_header(empty, 0, 1)

    {:ok, explicit, _} = live(conn, ~p"/projects/#{project.id}?statuses=pending")
    render_hook(explicit, "hydrate_task_table_preferences", %{"statuses" => ["done"]})
    assert_header(explicit, 1, 1)
  end

  test "local creation and persisted status changes refresh counts while invalid edits retain them",
       %{conn: conn} do
    project = project_fixture(%{})
    selected = list_fixture(project)

    {:ok, view, _} =
      live(conn, ~p"/projects/#{project.id}/lists/#{selected.id}/tasks/new?statuses=pending")

    assert_header(view, 0, 0)

    view
    |> form("#task-form", task: %{title: "New counted task", status: "pending"})
    |> render_submit()

    assert_count(view, selected, 1)
    assert_header(view, 1, 1)

    {:ok, [%{task: task}]} = Tasks.list_tasks_for_location(project, selected)
    render_patch(view, ~p"/projects/#{project.id}/lists/#{selected.id}/tasks/#{task.id}")
    view |> form("#task-form", task: %{title: ""}) |> render_submit()
    assert has_element?(view, "#task-modal")
    assert_count(view, selected, 1)
    assert_header(view, 1, 1)

    view
    |> form("#task-form", task: %{title: "New counted task", status: "in_review"})
    |> render_change(%{"_target" => ["task", "status"]})

    assert Tasks.get_task_for_project(project, task.id).status == :in_review
    assert_count(view, selected, 0)
    assert_header(view, 0, 1)
  end

  test "failed status persistence leaves counts at the saved status", %{conn: conn} do
    Ecto.Adapters.SQL.query!(Taskman.Repo, "ALTER TABLE tasks DROP CONSTRAINT tasks_status_check")

    Ecto.Adapters.SQL.query!(
      Taskman.Repo,
      "ALTER TABLE tasks ADD CONSTRAINT tasks_status_check CHECK (status <> 'in_review')"
    )

    project = project_fixture(%{})
    selected = list_fixture(project)
    task = task_fixture(project, selected, %{status: :pending})

    {:ok, view, _} =
      live(
        conn,
        ~p"/projects/#{project.id}/lists/#{selected.id}/tasks/#{task.id}?statuses=pending"
      )

    view
    |> form("#task-form", task: %{status: "in_review"})
    |> render_change(%{"_target" => ["task", "status"]})

    assert has_element?(view, "#task-status-save-status[data-state='failed']")
    assert has_element?(view, "#task-status option[selected][value='in_review']")
    assert Tasks.get_task_for_project(project, task.id).status == :pending
    assert_count(view, selected, 1)
    assert_header(view, 1, 1)
  end

  test "local List creation populates its zero count without changing the header", %{conn: conn} do
    project = project_fixture(%{})
    task_fixture(project, %{status: :pending})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}?statuses=pending")
    view |> element("#add-root-list-#{project.id}") |> render_click()
    view |> form("#list-create-form-root", list: %{name: "Empty counted List"}) |> render_submit()
    [created] = Taskman.Lists.list_lists_for_project(project)
    assert_count(view, created, 0)
    assert_project_count(view, 1)
    assert_header(view, 1, 1)
  end

  test "local moves refresh source, destination and ancestors", %{conn: conn} do
    project = project_fixture(%{})
    source = list_fixture(project)
    source_child = list_fixture(project, source)
    destination = list_fixture(project)
    destination_child = list_fixture(project, destination)
    task = task_fixture(project, source_child, %{status: :pending})

    {:ok, view, _} =
      live(conn, ~p"/projects/#{project.id}?include_children=true&statuses=pending")

    assert_count(view, source, 1)
    assert_count(view, destination, 0)
    assert_header(view, 1, 1)
    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination_child.id}") |> render_click()
    view |> element("#move-task-submit-#{task.id}") |> render_click()
    assert_count(view, source, 0)
    assert_count(view, destination, 1)
    assert_project_count(view, 1)
    assert_header(view, 1, 1)
    view |> element("#toggle-list-#{source.id}") |> render_click()
    view |> element("#toggle-list-#{destination.id}") |> render_click()
    assert_count(view, source_child, 0)
    assert_count(view, destination_child, 1)
  end

  test "validated task notifications refresh unselected Lists and persisted deletions", %{
    conn: conn
  } do
    project = project_fixture(%{})
    selected = list_fixture(project)
    sibling = list_fixture(project)
    child = list_fixture(project, sibling)
    task = task_fixture(project, child, %{status: :pending})

    {:ok, view, _} =
      live(
        conn,
        ~p"/projects/#{project.id}/lists/#{selected.id}?include_children=true&statuses=pending"
      )

    assert_count(view, sibling, 1)
    assert_project_count(view, 1)

    assert {:ok, updated} = Tasks.update_task(project, task, %{status: :in_review})
    notify_task(view, project, updated)
    assert_count(view, sibling, 0)
    assert_project_count(view, 0)
    assert_header(view, 0, 0)

    assert {:ok, pending} = Tasks.update_task(project, updated, %{status: :pending})
    notify_task(view, project, pending)
    assert_count(view, sibling, 1)
    assert_project_count(view, 1)

    Taskman.Repo.delete!(pending)
    notify_task(view, project, pending)
    assert_count(view, sibling, 0)
    assert_project_count(view, 0)
    render_patch(view, ~p"/projects/#{project.id}/lists/#{sibling.id}")
    assert_count(view, sibling, 0)
    assert_header(view, 0, 0)
  end

  test "missing selected Lists preserve valid Project counts and recovery drafts", %{conn: conn} do
    project = project_fixture(%{})
    selected = list_fixture(project)
    sibling = list_fixture(project)
    task_fixture(project, %{status: :pending})
    task_fixture(project, sibling, %{status: :pending})

    {:ok, view, _} =
      live(
        conn,
        ~p"/projects/#{project.id}/lists/#{selected.id}/tasks/new?include_children=true&statuses=pending"
      )

    view
    |> form("#task-form", task: %{title: "Recoverable count draft"})
    |> render_change(%{"_target" => ["task", "title"]})

    Taskman.Repo.delete!(selected)

    send(view.pid, %Event{
      entity: :list,
      operation: :updated,
      project_id: project.id,
      entity_id: selected.id,
      fields: [:name]
    })

    _ = :sys.get_state(view.pid)
    assert_project_count(view, 2)
    assert_count(view, sibling, 1)
    refute has_element?(view, "#location-task-count")
    assert has_element?(view, "#task-title[value='Recoverable count draft']")
  end

  defp drain_sort_queries do
    receive do
      {:sort_query, query} -> [query | drain_sort_queries()]
    after
      0 -> []
    end
  end

  defp notify_task(view, project, task) do
    send(view.pid, %Event{
      entity: :task,
      operation: :updated,
      project_id: project.id,
      entity_id: task.id,
      lock_version: task.lock_version,
      fields: [:status]
    })

    _ = :sys.get_state(view.pid)
  end

  defp assert_count(view, list, value),
    do: assert(has_element?(view, "#list-task-count-#{list.id}-value", Integer.to_string(value)))

  defp assert_project_count(view, value),
    do: assert(has_element?(view, "#project-tasks-count-value", Integer.to_string(value)))

  defp assert_header(view, visible, eligible),
    do: assert(has_element?(view, "#location-task-count-value", "#{visible} / #{eligible}"))

  defp view_assigns(view), do: :sys.get_state(view.pid).socket.assigns
end
