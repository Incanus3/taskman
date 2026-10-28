defmodule TaskmanWeb.ProjectLive.TaskDueFormatTest do
  use TaskmanWeb.ConnCase, async: true
  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures
  alias Taskman.Tasks

  setup %{conn: conn}, do: {:ok, conn: log_in_user(conn, user_fixture())}

  test "validates and creates the chosen day-first local datetime", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    params = %{
      title: "Day-first work",
      status: "pending",
      priority: "none",
      due_at: "29.02.2028 17:45"
    }

    view |> form("#task-form", task: params) |> render_change()
    assert has_element?(view, "#create-task:not([disabled])")
    view |> form("#task-form", task: params) |> render_submit()
    assert [task] = Tasks.list_tasks_for_project(project)
    assert task.due_at == ~N[2028-02-29 17:45:00]
  end

  test "invalid manual text remains visible without saving and can be corrected", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{due_at: ~N[2026-10-28 17:45:00]})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-due-at[value='28.10.2026 17:45']")

    for raw <- ["31.02.2026 17:45", "28.10."] do
      view
      |> form("#task-form", task: %{due_at: raw})
      |> render_change(%{"_target" => ["task", "due_at"]})

      assert has_element?(view, "#task-due-at[value='#{raw}']")
      assert has_element?(view, "#task-due-at-save-status[data-state='not_saved']")
      assert Tasks.get_task_for_project(project, task.id).due_at == ~N[2026-10-28 17:45:00]
    end

    view
    |> form("#task-form", task: %{due_at: "06.05.0999 09:30:42"})
    |> render_change(%{"_target" => ["task", "due_at"]})

    assert Tasks.get_task_for_project(project, task.id).due_at == ~N[0999-05-06 09:30:42]
    assert has_element?(view, "#task-due-at[value='06.05.0999 09:30:42']")

    view
    |> form("#task-form", task: %{due_at: " \t "})
    |> render_change(%{"_target" => ["task", "due_at"]})

    assert Tasks.get_task_for_project(project, task.id).due_at == nil
    assert has_element?(view, "#task-due-at[value='']")
  end

  test "invalid day-first text remains available through conflict and retained recovery", %{
    conn: conn
  } do
    project = project_fixture(%{})
    list = list_fixture(project)
    task = task_fixture(project, list, %{due_at: ~N[2026-10-28 17:45:00]})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{list.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{due_at: "28.10."})
    |> render_change(%{"_target" => ["task", "due_at"]})

    {:ok, _} = Tasks.update_task(project, task, %{due_at: ~N[2028-02-29 09:30:00]})
    assert has_element?(view, "#task-due_at-conflict", "29.02.2028 09:30")
    assert has_element?(view, "#task-due-at[value='28.10.']")
    view |> element("#use-latest-due_at") |> render_click()
    assert has_element?(view, "#task-due-at[value='29.02.2028 09:30']")

    view
    |> form("#task-form", task: %{due_at: "28.10."})
    |> render_change(%{"_target" => ["task", "due_at"]})

    Taskman.Repo.delete!(Tasks.get_task_for_project(project, task.id))
    Taskman.Repo.delete!(list)
    :ok = Taskman.ChangeNotifications.publish_list(list, :updated, [:name])
    assert has_element?(view, "#task-recovery #task-due-at[readonly][value='28.10.']")
  end
end
