defmodule TaskmanWeb.ProjectLive.RelatedTasksTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Repo, Tasks}

  setup %{conn: conn}, do: {:ok, conn: log_in_user(conn, user_fixture())}

  test "detail shows both directions with counts and current linked Task information", %{
    conn: conn
  } do
    project = project_fixture(%{name: "Website"})
    other = project_fixture(%{name: "Launch"})
    list = list_fixture(other, %{name: "Review"})
    task = task_fixture(project, %{title: "Publish site"})
    blocker = task_fixture(other, list, %{title: "Approve copy", priority: :high})
    dependent = task_fixture(project, %{title: "Notify readers", status: :in_progress})
    assert {:ok, _} = Tasks.add_block(other, blocker, task)
    assert {:ok, _} = Tasks.add_block(project, task, dependent)

    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(view, "#related-tasks")
    assert has_element?(view, "#related-blocked-by-count", "1")
    assert has_element?(view, "#related-blocks-count", "1")
    assert has_element?(view, "#related-blocked-by-row-#{blocker.id}", "Approve copy")
    assert has_element?(view, "#related-blocked-by-row-#{blocker.id}", "Review")
    assert has_element?(view, "#related-blocked-by-row-#{blocker.id}", "Launch")
    assert has_element?(view, "#related-blocked-by-row-#{blocker.id}", "High")
    assert has_element?(view, "#related-blocks-row-#{dependent.id}", "In Progress")
    assert has_element?(view, "#task-detail-main > #task-form + #related-tasks")

    assert has_element?(
             view,
             ".task-detail-columns > #task-detail-main + #task-detail-discussion"
           )
  end

  test "empty groups are explicit and linked rows navigate in and across Projects", %{conn: conn} do
    project = project_fixture(%{})
    other = project_fixture(%{})
    task = task_fixture(project, %{})
    local = task_fixture(project, %{})
    foreign_list = list_fixture(other, %{name: "Delivery"})
    foreign = task_fixture(other, foreign_list, %{})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(view, "#related-blocked-by-empty")
    assert has_element?(view, "#related-blocks-empty")
    assert {:ok, _} = Tasks.add_block(project, task, local)
    assert {:ok, _} = Tasks.add_block(project, task, foreign)
    _ = :sys.get_state(view.pid)

    assert has_element?(
             view,
             "#related-blocks-link-#{local.id}[href='/projects/#{project.id}/tasks/#{local.id}']"
           )

    assert has_element?(
             view,
             "#related-blocks-link-#{foreign.id}[href='/projects/#{other.id}/tasks/#{foreign.id}']"
           )

    view |> element("#related-blocks-link-#{local.id}") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{local.id}")
    render_patch(view, ~p"/projects/#{project.id}/tasks/#{task.id}")
    view |> element("#related-blocks-link-#{foreign.id}") |> render_click()
    assert_patch(view, ~p"/projects/#{other.id}/tasks/#{foreign.id}")
  end

  test "Project-first picker supports blank and multi-term search in both directions", %{
    conn: conn
  } do
    project = project_fixture(%{name: "Website"})
    other = project_fixture(%{name: "Launch"})
    list = list_fixture(other, %{name: "Review"})
    task = task_fixture(project, %{title: "Publish"})
    candidate = task_fixture(other, list, %{title: "Approve copy"})
    dependent = task_fixture(other, %{title: "Notify team"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#related-add-blocked-by") |> render_click()
    assert has_element?(view, "#related-picker-project option[value='#{project.id}'][selected]")
    refute has_element?(view, "#related-candidate-#{candidate.id}")

    view
    |> form("#related-picker-project-form", related_project: %{project_id: other.id})
    |> render_change()

    assert has_element?(view, "#related-candidate-#{candidate.id}", "Review")

    view
    |> form("#related-picker-search-form", related_search: %{query: "#{candidate.id} APPROVE"})
    |> render_change()

    assert has_element?(view, "#related-candidate-#{candidate.id}")
    view |> element("#related-candidate-#{candidate.id}") |> render_click()
    assert {:ok, %{blocked_by: [%{id: id}]}} = Tasks.list_blocking(project, task)
    assert id == candidate.id

    view |> element("#related-add-blocks") |> render_click()

    view
    |> form("#related-picker-project-form", related_project: %{project_id: other.id})
    |> render_change()

    view |> element("#related-candidate-#{dependent.id}") |> render_click()
    assert {:ok, %{blocks: [%{id: dependent_id}]}} = Tasks.list_blocking(project, task)
    assert dependent_id == dependent.id

    view |> element("#related-add-blocks") |> render_click()

    view
    |> form("#related-picker-project-form", related_project: %{project_id: other.id})
    |> render_change()

    view |> element("#related-candidate-#{dependent.id}") |> render_click()
    assert has_element?(view, "#related-picker-error[role='alert']")
  end

  test "remove is one click and a stale candidate shows a local error", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{})
    linked = task_fixture(project, %{})
    stale = task_fixture(project, %{})
    assert {:ok, _} = Tasks.add_block(project, task, linked)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#related-remove-blocks-#{linked.id}") |> render_click()
    assert {:ok, %{blocks: []}} = Tasks.list_blocking(project, task)

    view |> element("#related-add-blocks") |> render_click()
    assert has_element?(view, "#related-candidate-#{stale.id}")
    Repo.delete!(stale)
    view |> element("#related-candidate-#{stale.id}") |> render_click()
    assert has_element?(view, "#related-picker-error[role='alert']")
  end
end
