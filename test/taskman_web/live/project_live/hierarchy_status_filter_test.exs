defmodule TaskmanWeb.ProjectLive.HierarchyStatusFilterTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks

  setup %{conn: conn} do
    project = project_fixture(%{})
    root = task_fixture(project, %{status: :done})
    selected = task_fixture(project, %{status: :done}, parent: root)
    branch = task_fixture(project, %{status: :done}, parent: root)
    matching = task_fixture(project, %{status: :pending}, parent: branch)
    unrelated = task_fixture(project, %{status: :done}, parent: root)

    {:ok, view, _} =
      conn
      |> log_in_user(user_fixture())
      |> live(~p"/projects/#{project.id}/tasks/#{selected.id}")

    %{
      view: view,
      project: project,
      root: root,
      selected: selected,
      branch: branch,
      matching: matching,
      unrelated: unrelated
    }
  end

  test "hierarchy choices apply immediately and independently, with projected disclosures", c do
    c.view |> element("#task-status-filter-button") |> render_click()

    c.view
    |> form("#task-status-filter-form", status_filter: %{statuses: ["done"]})
    |> render_change()

    c.view |> element("#task-hierarchy-status-filter-button") |> render_click()
    assert has_element?(c.view, "#task-hierarchy-status-filter-option-done[checked]")
    filter(c.view, ["pending"])

    refute has_element?(c.view, "#task-hierarchy-node-#{c.unrelated.id}")
    assert has_element?(c.view, "#task-hierarchy-link-#{c.selected.id}[aria-current='true']")

    assert has_element?(
             c.view,
             "#task-hierarchy-disclosure-#{c.branch.id}[aria-expanded='false']"
           )

    refute has_element?(c.view, "#task-hierarchy-node-#{c.matching.id}")
    assert has_element?(c.view, "#task-status-filter-option-done[checked]")
    refute has_element?(c.view, "#task-status-filter-option-pending[checked]")

    c.view |> element("#task-hierarchy-disclosure-#{c.branch.id}") |> render_click()
    assert has_element?(c.view, "#task-hierarchy-node-#{c.matching.id}")
    filter(c.view, ["done"])
    assert has_element?(c.view, "#task-hierarchy-node-#{c.branch.id}")
    refute has_element?(c.view, "#task-hierarchy-disclosure-#{c.branch.id}")
    filter(c.view, ["pending"])
    assert has_element?(c.view, "#task-hierarchy-disclosure-#{c.branch.id}[aria-expanded='true']")
    assert has_element?(c.view, "#task-hierarchy-node-#{c.matching.id}")
  end

  test "navigation and live refresh retain choices, while close and reopen reset them", c do
    c.view |> element("#task-hierarchy-status-filter-button") |> render_click()
    filter(c.view, ["pending"])
    c.view |> element("#task-hierarchy-disclosure-#{c.branch.id}") |> render_click()
    c.view |> element("#task-hierarchy-link-#{c.matching.id}") |> render_click()
    assert_patch(c.view, ~p"/projects/#{c.project.id}/tasks/#{c.matching.id}")
    refute has_element?(c.view, "#task-hierarchy-node-#{c.selected.id}")
    refute has_element?(c.view, "#task-hierarchy-status-filter-option-done[checked]")

    {:ok, _} = Tasks.update_task(c.project, c.unrelated, %{status: :pending})
    assert has_element?(c.view, "#task-hierarchy-node-#{c.unrelated.id}")
    refute has_element?(c.view, "#task-hierarchy-status-filter-option-done[checked]")
    assert has_element?(c.view, "#task-hierarchy-expanded-marker-#{c.branch.id}")

    other = task_fixture(c.project, %{})
    other_done = task_fixture(c.project, %{status: :done}, parent: other)
    render_patch(c.view, ~p"/projects/#{c.project.id}/tasks/#{other.id}")
    refute has_element?(c.view, "#task-hierarchy-node-#{other_done.id}")
    refute has_element?(c.view, "#task-hierarchy-status-filter-option-done[checked]")

    c.view |> element("#task-modal-close") |> render_click()
    assert_patch(c.view, ~p"/projects/#{c.project.id}")
    c.view |> element("#open-task-#{other.id}") |> render_click()
    assert_patch(c.view, ~p"/projects/#{c.project.id}/tasks/#{other.id}")
    c.view |> element("#task-hierarchy-status-filter-button") |> render_click()
    assert has_element?(c.view, "#task-hierarchy-status-filter-option-done[checked]")
    assert has_element?(c.view, "#task-hierarchy-node-#{other_done.id}")
  end

  test "live parentage changes recompute eligible ancestry without resetting choices", c do
    c.view |> element("#task-hierarchy-status-filter-button") |> render_click()
    filter(c.view, ["pending"])
    {:ok, _} = Tasks.update_task(c.project, c.matching, %{}, parent: c.unrelated)

    refute has_element?(c.view, "#task-hierarchy-node-#{c.branch.id}")

    assert has_element?(
             c.view,
             "#task-hierarchy-disclosure-#{c.unrelated.id}[aria-expanded='false']"
           )

    refute has_element?(c.view, "#task-hierarchy-status-filter-option-done[checked]")
    c.view |> element("#task-hierarchy-disclosure-#{c.unrelated.id}") |> render_click()

    assert has_element?(
             c.view,
             "#task-hierarchy-node-#{c.unrelated.id} #task-hierarchy-node-#{c.matching.id}"
           )
  end

  test "unchecking every status leaves only the inclusive selected path", c do
    c.view |> element("#task-hierarchy-status-filter-button") |> render_click()
    filter(c.view, [""])

    assert has_element?(
             c.view,
             "#task-hierarchy-node-#{c.root.id} #task-hierarchy-node-#{c.selected.id}"
           )

    refute has_element?(c.view, "#task-hierarchy-node-#{c.branch.id}")
    refute has_element?(c.view, "#task-hierarchy-node-#{c.unrelated.id}")
  end

  defp filter(view, statuses) do
    view
    |> form("#task-hierarchy-status-filter-form", hierarchy_status_filter: %{statuses: statuses})
    |> render_change()
  end
end
