defmodule TaskmanWeb.ProjectLive.DoneWarningTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import TaskmanWeb.DescriptionEditorHelpers
  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks

  setup %{conn: conn}, do: {:ok, conn: log_in_user(conn, user_fixture())}

  for state <- [:in_analysis, :not_planned, :will_not_do] do
    test "Done warns for a #{state} blocker until confirmed", %{conn: conn} do
      project = project_fixture(%{})
      task = task_fixture(project)

      assert {:ok, blocker} =
               create_task(project, nil, %{title: "Closed blocker", status: unquote(state)})

      assert {:ok, _} = add_block(project, blocker, task)

      {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

      view
      |> form("#task-form", task: %{status: "done"})
      |> render_captured_change(%{"_target" => ["task", "status"]})

      assert has_element?(view, "#task-done-warning")
      assert has_element?(view, "#task-done-blocker-#{blocker.id}")
      assert Tasks.get_task_for_project(project, task.id).status == :pending
      view |> element("#confirm-task-done") |> render_captured_click()
      assert Tasks.get_task_for_project(project, task.id).status == :done
    end
  end

  test "Done reviews blockers and descendants until explicit confirmation or cancellation", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :in_progress})
    blocker = task_fixture(project, %{title: "Review release", priority: :urgent})
    child = task_fixture(project, %{title: "A child"}, parent: task)
    boundary = task_fixture(project, %{status: :done}, parent: task)
    hidden = task_fixture(project, %{}, parent: boundary)

    assert {:ok, _} = add_block(project, blocker, task)

    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    assert Tasks.get_task_for_project(project, task.id).status == :in_progress
    assert has_element?(view, "#task-status option[value='done'][selected]")
    assert has_element?(view, "#task-done-warning #task-done-blocker-#{blocker.id}")
    assert has_element?(view, "#task-done-warning #task-done-blocker-#{child.id}")

    assert has_element?(
             view,
             "#task-done-warning h3",
             "Review unresolved blockers and descendants"
           )

    assert has_element?(view, "#task-done-warning li:nth-child(1)#task-done-blocker-#{child.id}")

    assert has_element?(
             view,
             "#task-done-warning li:nth-child(2)#task-done-blocker-#{blocker.id}"
           )

    refute has_element?(view, "#task-done-warning #task-done-blocker-#{boundary.id}")
    refute has_element?(view, "#task-done-warning #task-done-blocker-#{hidden.id}")
    assert has_element?(view, "#task-done-warning", "Review release")
    assert has_element?(view, "#task-done-warning", "urgent")

    view |> element("#keep-current-task-status") |> render_captured_click()
    refute has_element?(view, "#task-done-warning")
    assert has_element?(view, "#task-status option[value='in_progress'][selected]")
    assert Tasks.get_task_for_project(project, task.id).status == :in_progress

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    view |> element("#confirm-task-done") |> render_captured_click()
    assert Tasks.get_task_for_project(project, task.id).status == :done
    refute has_element?(view, "#task-done-warning")
    assert Tasks.get_task_for_project(project, child.id).status == :pending
    assert Tasks.get_task_for_project(project, boundary.id).status == :done
    assert Tasks.get_task_for_project(project, hidden.id).status == :pending
  end

  test "a smaller blocker set confirms while a newly unconfirmed blocker refreshes the warning",
       %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{})
    first = task_fixture(project, %{title: "First review"})
    second = task_fixture(project, %{title: "Second review"})

    assert {:ok, _} = add_block(project, first, task)
    assert {:ok, _} = add_block(project, second, task)

    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    assert has_element?(view, "#task-done-blocker-#{first.id}")
    assert has_element?(view, "#task-done-blocker-#{second.id}")

    assert {:ok, _} = update_task(project, second, %{status: :done})

    view |> element("#confirm-task-done") |> render_captured_click()
    assert Tasks.get_task_for_project(project, task.id).status == :done

    done_task = Tasks.get_task_for_project(project, task.id)

    assert {:ok, _} = update_task(project, done_task, %{status: :pending})

    assert Tasks.get_task_for_project(project, task.id).status == :pending
    _ = :sys.get_state(view.pid)

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    third = task_fixture(project, %{title: "Third review"})

    assert {:ok, _} = add_block(project, third, task)

    view |> element("#confirm-task-done") |> render_captured_click()
    assert Tasks.get_task_for_project(project, task.id).status == :pending
    assert has_element?(view, "#task-done-blocker-#{third.id}")
  end

  test "newly exposed descendants refresh the warning and require another confirmation", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :in_progress})
    child = task_fixture(project, %{title: "A child"}, parent: task)
    boundary = task_fixture(project, %{title: "B boundary", status: :done}, parent: task)
    grandchild = task_fixture(project, %{title: "C grandchild"}, parent: boundary)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    assert has_element?(view, "#task-done-warning #task-done-blocker-#{child.id}")
    refute has_element?(view, "#task-done-warning #task-done-blocker-#{boundary.id}")
    refute has_element?(view, "#task-done-warning #task-done-blocker-#{grandchild.id}")

    assert {:ok, _} = update_task(project, boundary, %{status: :pending})

    _ = :sys.get_state(view.pid)
    view |> element("#confirm-task-done") |> render_captured_click()

    assert Tasks.get_task_for_project(project, task.id).status == :in_progress
    assert has_element?(view, "#task-status option[value='done'][selected]")
    assert has_element?(view, "#task-done-warning #task-done-blocker-#{child.id}")
    assert has_element?(view, "#task-done-warning #task-done-blocker-#{boundary.id}")
    assert has_element?(view, "#task-done-warning #task-done-blocker-#{grandchild.id}")

    view |> element("#confirm-task-done") |> render_captured_click()

    assert Tasks.get_task_for_project(project, task.id).status == :done
    refute has_element?(view, "#task-done-warning")
    assert Tasks.get_task_for_project(project, child.id).status == :pending
    assert Tasks.get_task_for_project(project, boundary.id).status == :pending
    assert Tasks.get_task_for_project(project, grandchild.id).status == :pending
  end

  test "another status and closing detail discard pending Done while ordinary fields still save",
       %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :in_progress})
    blocker = task_fixture(project, %{})

    assert {:ok, _} = add_block(project, blocker, task)

    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    view
    |> form("#task-form", task: %{priority: "high"})
    |> render_captured_change(%{"_target" => ["task", "priority"]})

    assert Tasks.get_task_for_project(project, task.id).priority == :high
    assert has_element?(view, "#task-done-warning")

    view
    |> form("#task-form", task: %{status: "in_review"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    refute has_element?(view, "#task-done-warning")
    assert Tasks.get_task_for_project(project, task.id).status == :in_review

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    view |> element("#task-modal-close") |> render_captured_click()
    assert_patch(view, ~p"/projects/#{project.id}")
    assert Tasks.get_task_for_project(project, task.id).status == :in_review
  end

  test "Done confirmation does not bypass an ordinary status conflict", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :pending})
    blocker = task_fixture(project, %{})

    assert {:ok, _} = add_block(project, blocker, task)

    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    assert has_element?(view, "#task-done-warning")

    assert {:ok, _} = update_task(project, task, %{status: :in_review})

    _ = :sys.get_state(view.pid)
    assert has_element?(view, "#task-status-conflict")

    view |> element("#confirm-task-done") |> render_captured_click()
    assert Tasks.get_task_for_project(project, task.id).status == :in_review
    assert has_element?(view, "#task-status-conflict")

    view |> element("#keep-mine-status") |> render_captured_click()
    refute has_element?(view, "#task-status-conflict")
    assert has_element?(view, "#task-done-warning")
    view |> element("#confirm-task-done") |> render_captured_click()
    assert Tasks.get_task_for_project(project, task.id).status == :done
  end

  test "using the latest status discards the pending Done warning", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :pending})
    blocker = task_fixture(project, %{})

    assert {:ok, _} = add_block(project, blocker, task)

    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    assert has_element?(view, "#task-done-warning")

    assert {:ok, latest} = update_task(project, task, %{status: :in_review})

    _ = :sys.get_state(view.pid)
    assert has_element?(view, "#task-status-conflict")

    view |> element("#use-latest-status") |> render_captured_click()

    assert Tasks.get_task_for_project(project, task.id).lock_version == latest.lock_version
    assert has_element?(view, "#task-status option[value='in_review'][selected]")
    refute has_element?(view, "#task-status-conflict")
    refute has_element?(view, "#task-done-warning")
    refute has_element?(view, "#confirm-task-done")
    refute has_element?(view, "#keep-current-task-status")
  end

  test "an ordinary Done retry clears the warning after blockers resolve", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :in_progress})
    blocker = task_fixture(project, %{})

    assert {:ok, _} = add_block(project, blocker, task)

    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    assert has_element?(view, "#task-done-warning")
    assert Tasks.get_task_for_project(project, task.id).status == :in_progress

    assert {:ok, _} = update_task(project, blocker, %{status: :done})

    _ = :sys.get_state(view.pid)

    view
    |> form("#task-form", task: %{status: "done"})
    |> render_captured_change(%{"_target" => ["task", "status"]})

    assert Tasks.get_task_for_project(project, task.id).status == :done
    assert has_element?(view, "#task-status option[value='done'][selected]")
    refute has_element?(view, "#task-done-warning")
    refute has_element?(view, "#confirm-task-done")
    refute has_element?(view, "#keep-current-task-status")
  end
end
