defmodule TaskmanWeb.ProjectLive.HierarchySortTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks

  setup %{conn: conn} do
    project = project_fixture(%{})
    root = task_fixture(project, %{title: "Root"})
    first = task_fixture(project, %{title: "Zulu", status: :pending}, parent: root)
    second = task_fixture(project, %{title: "Alpha", status: :done}, parent: root)

    {:ok, view, _} =
      conn |> log_in_user(user_fixture()) |> live(~p"/projects/#{project.id}/tasks/#{root.id}")

    %{view: view, project: project, root: root, first: first, second: second}
  end

  test "sorting applies immediately, reverses on reselection, and is independent of the listing",
       c do
    assert_before(c.view, c.root, c.second, c.first)
    c.view |> element("#task-hierarchy-sort-button") |> render_click()
    assert has_element?(c.view, "#task-hierarchy-sort-option-status[aria-pressed='true']")
    choose(c.view, :title)
    assert_before(c.view, c.root, c.second, c.first)

    assert has_element?(
             c.view,
             "#task-hierarchy-sort-button[aria-label='Sort hierarchy: Name, ascending']"
           )

    c.view |> element("#task-hierarchy-sort-button") |> render_click()
    choose(c.view, :title)
    assert_before(c.view, c.root, c.first, c.second)
    c.view |> element("#sort-task-id") |> render_click()
    assert_before(c.view, c.root, c.first, c.second)
    c.view |> element("#task-hierarchy-status-filter-button") |> render_click()

    c.view
    |> form("#task-hierarchy-status-filter-form",
      hierarchy_status_filter: %{statuses: ["pending"]}
    )
    |> render_change()

    assert has_element?(c.view, "#task-hierarchy-node-#{c.first.id}")
    refute has_element?(c.view, "#task-hierarchy-node-#{c.second.id}")

    assert has_element?(
             c.view,
             "#task-hierarchy-sort-button[aria-label='Sort hierarchy: Name, descending']"
           )
  end

  test "navigation and refresh retain sorting while closing the dialog resets it", c do
    c.view |> element("#task-hierarchy-sort-button") |> render_click()
    choose(c.view, :id)
    assert_before(c.view, c.root, c.first, c.second)
    c.view |> element("#task-hierarchy-link-#{c.first.id}") |> render_click()
    assert_patch(c.view, ~p"/projects/#{c.project.id}/tasks/#{c.first.id}")
    {:ok, _} = Tasks.update_task(c.project, c.second, %{title: "Updated"})
    assert_before(c.view, c.root, c.first, c.second)

    assert has_element?(
             c.view,
             "#task-hierarchy-sort-button[aria-label='Sort hierarchy: ID, ascending']"
           )

    render_click(c.view, "sort_hierarchy", %{"field" => "invalid"})
    assert_before(c.view, c.root, c.first, c.second)
    c.view |> element("#task-modal-close") |> render_click()
    assert_patch(c.view, ~p"/projects/#{c.project.id}")
    c.view |> element("#open-task-#{c.root.id}") |> render_click()
    assert_patch(c.view, ~p"/projects/#{c.project.id}/tasks/#{c.root.id}")
    assert_before(c.view, c.root, c.second, c.first)
  end

  test "opening copies listing sorting once and reopening copies the latest choice", c do
    c.view |> element("#task-modal-close") |> render_click()
    c.view |> element("#sort-task-title") |> render_click()
    c.view |> element("#sort-task-title") |> render_click()
    c.view |> element("#open-task-#{c.root.id}") |> render_click()

    assert has_element?(
             c.view,
             "#task-hierarchy-sort-button[aria-label='Sort hierarchy: Name, descending']"
           )

    assert_before(c.view, c.root, c.first, c.second)

    c.view |> element("#sort-task-id") |> render_click()
    c.view |> element("#task-hierarchy-link-#{c.first.id}") |> render_click()

    assert has_element?(
             c.view,
             "#task-hierarchy-sort-button[aria-label='Sort hierarchy: Name, descending']"
           )

    c.view |> element("#task-modal-close") |> render_click()
    c.view |> element("#open-task-#{c.root.id}") |> render_click()

    assert has_element?(
             c.view,
             "#task-hierarchy-sort-button[aria-label='Sort hierarchy: ID, ascending']"
           )

    assert_before(c.view, c.root, c.first, c.second)
  end

  test "unsupported listing location sort falls back to status", c do
    c.view |> element("#task-modal-close") |> render_click()
    render_click(c.view, "sort_tasks", %{"field" => "location"})
    c.view |> element("#open-task-#{c.root.id}") |> render_click()

    assert has_element?(
             c.view,
             "#task-hierarchy-sort-button[aria-label='Sort hierarchy: Status, descending']"
           )

    assert_before(c.view, c.root, c.second, c.first)
  end

  test "saving status refreshes sidebar metadata and order while retaining the filter", c do
    render_patch(c.view, ~p"/projects/#{c.project.id}/tasks/#{c.second.id}")
    c.view |> element("#task-hierarchy-status-filter-button") |> render_click()

    c.view
    |> form("#task-hierarchy-status-filter-form",
      hierarchy_status_filter: %{statuses: ["pending"]}
    )
    |> render_change()

    assert_before(c.view, c.root, c.second, c.first)

    c.view
    |> form("#task-form", task: %{status: "icebox"})
    |> render_change(%{"_target" => ["task", "status"]})

    assert Tasks.get_task_for_project(c.project, c.second.id).status == :icebox
    assert has_element?(c.view, "#task-hierarchy-status-#{c.second.id}", "Icebox")
    assert_before(c.view, c.root, c.first, c.second)
    refute has_element?(c.view, "#task-hierarchy-status-filter-option-icebox[checked]")
    assert has_element?(c.view, "#task-hierarchy-status-filter-option-pending[checked]")
  end

  test "saving priority refreshes sidebar metadata and priority ordering", c do
    render_patch(c.view, ~p"/projects/#{c.project.id}/tasks/#{c.first.id}")
    c.view |> element("#task-hierarchy-sort-button") |> render_click()
    choose(c.view, :priority)
    assert_before(c.view, c.root, c.second, c.first)

    c.view
    |> form("#task-form", task: %{priority: "urgent"})
    |> render_change(%{"_target" => ["task", "priority"]})

    assert Tasks.get_task_for_project(c.project, c.first.id).priority == :urgent
    assert has_element?(c.view, "#task-hierarchy-priority-#{c.first.id}", "Urgent")
    assert_before(c.view, c.root, c.first, c.second)

    assert has_element?(
             c.view,
             "#task-hierarchy-sort-button[aria-label='Sort hierarchy: Priority, descending']"
           )
  end

  test "a warned Done draft updates the hierarchy only after confirmation", c do
    blocker = task_fixture(c.project, %{title: "Unresolved blocker"})
    {:ok, _} = Tasks.add_block(c.project, blocker, c.first)
    render_patch(c.view, ~p"/projects/#{c.project.id}/tasks/#{c.first.id}")

    c.view
    |> form("#task-form", task: %{status: "done"})
    |> render_change(%{"_target" => ["task", "status"]})

    assert has_element?(c.view, "#task-done-warning")
    assert has_element?(c.view, "#task-hierarchy-status-#{c.first.id}", "Pending")
    c.view |> element("#confirm-task-done") |> render_click()
    assert Tasks.get_task_for_project(c.project, c.first.id).status == :done
    assert has_element?(c.view, "#task-hierarchy-status-#{c.first.id}", "Done")
  end

  defp choose(view, field),
    do: view |> element("#task-hierarchy-sort-option-#{field}") |> render_click()

  defp assert_before(view, root, first, second) do
    assert has_element?(
             view,
             "#task-hierarchy-group-#{root.id} > #task-hierarchy-node-#{first.id} + #task-hierarchy-node-#{second.id}"
           )
  end
end
