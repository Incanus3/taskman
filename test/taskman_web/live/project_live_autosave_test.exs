defmodule TaskmanWeb.ProjectLiveAutosaveTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Repo, Tasks}

  setup do
    previous = Application.get_env(:taskman, :task_autosave_delay_ms)
    Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)

    on_exit(fn ->
      case previous do
        nil -> Application.delete_env(:taskman, :task_autosave_delay_ms)
        value -> Application.put_env(:taskman, :task_autosave_delay_ms, value)
      end
    end)

    :ok
  end

  test "zero delay enqueues autosave during the change event", %{conn: conn} do
    Application.put_env(:taskman, :task_autosave_delay_ms, 0)
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    :erlang.trace(view.pid, true, [:send])

    view
    |> form("#task-form", task: %{title: "After"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert_receive {:trace, pid, :send, {:autosave_task_field, task_id, "title", 1}, pid}
    assert pid == view.pid
    assert task_id == task.id

    _ = :sys.get_state(view.pid)
    assert Tasks.get_task_for_project(project, task.id).title == "After"
  end

  test "debounces text, ignores stale messages, and flushes the final value on close", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "First"})
    |> render_change(%{"_target" => ["task", "title"]})

    view
    |> form("#task-form", task: %{title: "Final"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert Tasks.get_task_for_project(project, task.id).title == "Before"

    send(view.pid, {:autosave_task_field, task.id, "title", 1})
    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, task.id).title == "Before"

    view |> element("#task-modal-close") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}")
    assert Tasks.get_task_for_project(project, task.id).title == "Final"
  end

  test "submitting an edit flushes a valid dirty draft without leaving the canonical modal", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    task_path = ~p"/projects/#{project.id}/tasks/#{task.id}"
    {:ok, view, _html} = live(conn, task_path)

    view
    |> form("#task-form", task: %{title: "Submitted draft"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert Tasks.get_task_for_project(project, task.id).title == "Before"

    view
    |> form("#task-form", task: %{title: "Submitted draft"})
    |> render_submit()

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-form[phx-submit='submit_task_edit']")

    assert has_element?(
             view,
             "#submit-task-edit[type='submit'].sr-only[aria-hidden='true'][tabindex='-1']"
           )

    refute has_element?(view, "#task-form button[type='submit']", "Save")
    assert Tasks.get_task_for_project(project, task.id).title == "Submitted draft"
  end

  test "submitting an invalid edit keeps its draft while flushing other valid dirty fields", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", description: "Old description"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Before", description: "New description"})
    |> render_change(%{"_target" => ["task", "description"]})

    view
    |> form("#task-form", task: %{title: "", description: "New description"})
    |> render_change(%{"_target" => ["task", "title"]})

    view
    |> form("#task-form", task: %{title: "", description: "New description"})
    |> render_submit()

    updated = Tasks.get_task_for_project(project, task.id)
    assert updated.title == "Before"
    assert updated.description == "New description"
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-title[value='']")
    assert has_element?(view, "#task-save-status[data-state='not_saved']")
  end

  test "does not reuse an autosave token after reopening the same Task", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "First lifecycle"})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> element("#task-modal-close") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}")
    assert Tasks.get_task_for_project(project, task.id).title == "First lifecycle"

    view |> element("#open-task-#{task.id}") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Second lifecycle"})
    |> render_change(%{"_target" => ["task", "title"]})

    send(view.pid, {:autosave_task_field, task.id, "title", 1})
    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, task.id).title == "First lifecycle"
  end

  test "flushes valid dirty text and discards an invalid dirty title on close", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", description: "Old description"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Before", description: "New description"})
    |> render_change(%{"_target" => ["task", "description"]})

    view
    |> form("#task-form", task: %{title: "", description: "New description"})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> element("#task-modal-close") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}")

    updated = Tasks.get_task_for_project(project, task.id)
    assert updated.title == "Before"
    assert updated.description == "New description"
    refute has_element?(view, "#task-modal")
  end

  test "keeps the Task modal and draft when route flushing cannot persist", %{conn: conn} do
    Ecto.Adapters.SQL.query!(Repo, "ALTER TABLE tasks DROP CONSTRAINT tasks_status_check")

    Ecto.Adapters.SQL.query!(
      Repo,
      "ALTER TABLE tasks ADD CONSTRAINT tasks_status_check CHECK (status <> 'in_review')"
    )

    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})
    task_path = ~p"/projects/#{project.id}/tasks/#{task.id}"
    {:ok, view, _html} = live(conn, task_path)

    view
    |> form("#task-form", task: %{title: "Valid alongside failure", status: "pending"})
    |> render_change(%{"_target" => ["task", "title"]})

    view
    |> form("#task-form", task: %{title: "Valid alongside failure", status: "in_review"})
    |> render_change(%{"_target" => ["task", "status"]})

    assert has_element?(view, "#task-save-status[data-state='failed']")
    assert has_element?(view, "#task-status option[selected][value='in_review']")
    assert Tasks.get_task_for_project(project, task.id).status == :pending

    view
    |> form("#task-form", task: %{title: "Valid alongside failure", status: "in_review"})
    |> render_submit()

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-save-status[data-state='failed']")
    assert has_element?(view, "#task-status option[selected][value='in_review']")

    updated = Tasks.get_task_for_project(project, task.id)
    assert updated.title == "Valid alongside failure"
    assert updated.status == :pending

    render_patch(view, ~p"/projects/#{project.id}")
    assert_patch(view, ~p"/projects/#{project.id}")
    assert_patch(view, task_path)

    render_patch(view, task_path)
    assert_patch(view, task_path)
    refute_patched(view, task_path)
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-save-status[data-state='failed']")
    assert has_element?(view, "#task-status option[selected][value='in_review']")
    assert Tasks.get_task_for_project(project, task.id).status == :pending
  end
end
