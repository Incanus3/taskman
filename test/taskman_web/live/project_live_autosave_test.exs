defmodule TaskmanWeb.ProjectLiveAutosaveTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Repo, Tasks}
  alias TaskmanWeb.{ProjectLive, TaskAutosave, TaskParentPicker}

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

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

  test "title autosave refreshes the hierarchy projection", %{conn: conn} do
    Application.put_env(:taskman, :task_autosave_delay_ms, 0)
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    task = task_fixture(project, %{title: "Before"}, parent: parent)
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(view, "#task-hierarchy-link-#{task.id}", "Before")

    view
    |> form("#task-form", task: %{title: "After"})
    |> render_change(%{"_target" => ["task", "title"]})

    _ = :sys.get_state(view.pid)

    assert has_element?(view, "#task-hierarchy-link-#{task.id}", "After")
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

  test "saving an edit parent preserves a dirty ordinary autosave draft", %{conn: conn} do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Roadmap"})
    task = task_fixture(project, %{title: "Before"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Dirty title"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert Tasks.get_task_for_project(project, task.id).title == "Before"

    view |> element("#task-parent-trigger") |> render_click()
    view |> element("#task-parent-option-#{parent.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).parent_task_id == parent.id
    assert has_element?(view, "#task-title[value='Dirty title']")
    assert has_element?(view, "#task-save-status[data-state='saving']")

    view |> element("#task-modal-close") |> render_click()

    updated = Tasks.get_task_for_project(project, task.id)
    assert updated.title == "Dirty title"
    assert updated.parent_task_id == parent.id
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

  test "flushes the current Task and ignores its delayed save after switching Tasks", %{
    conn: conn
  } do
    project = project_fixture(%{})
    first = task_fixture(project, %{title: "First persisted"})
    second = task_fixture(project, %{title: "Second persisted"})

    first_path = ~p"/projects/#{project.id}/tasks/#{first.id}"
    second_path = ~p"/projects/#{project.id}/tasks/#{second.id}"
    {:ok, view, _html} = live(conn, first_path)

    view
    |> form("#task-form", task: %{title: "First flushed"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert Tasks.get_task_for_project(project, first.id).title == "First persisted"

    render_patch(view, second_path)

    assert Tasks.get_task_for_project(project, first.id).title == "First flushed"
    assert has_element?(view, "#task-title[value='Second persisted']")
    assert has_element?(view, "#task-save-status[data-state='idle']")

    send(view.pid, {:autosave_task_field, first.id, "title", 1})
    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, second.id).title == "Second persisted"
    assert has_element?(view, "#task-title[value='Second persisted']")
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

  test "route flushing a cross-Project Task keeps the Task-not-found state", %{conn: _conn} do
    selected_project = project_fixture(%{})
    task_project = project_fixture(%{})
    task = task_fixture(task_project, %{title: "Before"})

    assert {:schedule, autosave, ^task, _delay, _message} =
             TaskAutosave.change(
               TaskAutosave.load(TaskAutosave.empty(), task, saved?: false),
               task_project,
               task,
               %{"title" => "Unsaved"},
               "title"
             )

    socket = %Phoenix.LiveView.Socket{
      private: %{
        live_temp: %{},
        lifecycle: Phoenix.LiveView.Lifecycle.build([])
      }
    }

    {:ok, socket} = ProjectLive.mount(%{}, %{}, socket)

    socket =
      %{socket | assigns: Map.merge(socket.assigns, %{live_action: :show_task})}
      |> Phoenix.Component.assign(:selected_project, selected_project)
      |> Phoenix.Component.assign(:selected_task, task)
      |> Phoenix.Component.assign(:task_autosave, autosave)

    params = %{
      "project_id" => Integer.to_string(selected_project.id),
      "task_id" => Integer.to_string(task.id)
    }

    assert {:noreply, socket} = ProjectLive.handle_params(params, nil, socket)
    assert socket.assigns.selected_task == nil
    assert socket.assigns.task_not_found?
    assert socket.assigns.task_autosave.form == nil

    socket = %{socket | assigns: Map.put(socket.assigns, :flash, %{})}
    assert rendered_to_string(ProjectLive.render(socket.assigns)) =~ ~s(id="task-not-found")
  end

  test "closes a dirty Task detail back to its List context and preserves descendants", %{
    conn: conn
  } do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, list, %{title: "Before"})

    task_path =
      ~p"/projects/#{project.id}/lists/#{list.id}/tasks/#{task.id}?include_children=true"

    {:ok, view, _html} = live(conn, task_path)

    view
    |> form("#task-form", task: %{title: "After"})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> element("#task-modal-close") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/lists/#{list.id}?include_children=true")
    assert Tasks.get_task_for_project(project, task.id).title == "After"
  end

  test "blocks a detail move when a dirty title cannot autosave and keeps both errors visible", %{
    conn: conn
  } do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Before"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: ""})
    |> render_change(%{"_target" => ["task", "title"]})

    assert has_element?(view, "#task-form [data-role='field-error']")

    view |> element("#move-task-detail-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()
    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).list_id == nil
    assert has_element?(view, "#task-form [data-role='field-error']")
    assert has_element?(view, "#move-task-error-#{task.id}[role='alert']")
  end

  test "persists a valid dirty detail draft before moving the Task", %{conn: conn} do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Before"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "After"})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> element("#move-task-detail-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()
    view |> element("#move-task-submit-#{task.id}") |> render_click()

    moved_task = Tasks.get_task_for_project(project, task.id)
    assert moved_task.title == "After"
    assert moved_task.list_id == destination.id
  end

  test "a same-title autosave race preserves the draft, blocks its old timer, and resolves in place",
       %{
         conn: conn
       } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    task_path = ~p"/projects/#{project.id}/tasks/#{task.id}"
    {:ok, view, _html} = live(conn, task_path)

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert {:ok, latest} = Tasks.update_task(project, task, %{title: "Latest"})

    send(view.pid, {:autosave_task_field, task.id, "title", 1})
    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, task.id).title == "Latest"
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-title[value='Mine']")
    assert has_element?(view, "#task-save-status[data-state='conflicted']")
    assert has_element?(view, "#task-title-conflict[role='alert']")
    assert has_element?(view, "#use-latest-title")
    assert has_element?(view, "#keep-mine-title")

    view |> element("#use-latest-title") |> render_click()

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-title[value='Latest']")
    refute has_element?(view, "#task-title-conflict")
    refute_patched(view, task_path)
    assert latest.title == "Latest"
  end

  test "parent selection and clearing stay local while a parent conflict is unresolved" do
    project = project_fixture(%{})
    initial_parent = task_fixture(project, %{title: "Initial"})
    mine = task_fixture(project, %{title: "Mine"})
    other = task_fixture(project, %{title: "Other"})
    latest_parent = task_fixture(project, %{title: "Latest"})
    task = task_fixture(project, %{title: "Task"}, parent: initial_parent)

    picker =
      TaskParentPicker.empty()
      |> TaskParentPicker.open_edit(project, task)
      |> TaskParentPicker.select_draft(project, mine.id)

    assert {:ok, latest} = Tasks.update_task(project, task, %{}, parent: latest_parent)

    assert {:conflict, conflicted_picker, ^latest} =
             TaskParentPicker.save_edit(picker, project, task)

    socket = %Phoenix.LiveView.Socket{
      private: %{
        live_temp: %{},
        lifecycle: Phoenix.LiveView.Lifecycle.build([])
      }
    }

    {:ok, socket} = ProjectLive.mount(%{}, %{}, socket)

    socket =
      %{socket | assigns: Map.merge(socket.assigns, %{live_action: :show_task})}
      |> Phoenix.Component.assign(:selected_project, project)
      |> Phoenix.Component.assign(:selected_task, latest)
      |> Phoenix.Component.assign(:task_parent_picker, conflicted_picker)

    assert {:noreply, socket} =
             ProjectLive.handle_event(
               "select_task_parent",
               %{"parent-id" => Integer.to_string(other.id)},
               socket
             )

    assert socket.assigns.task_parent_picker.selected_parent.id == other.id
    assert socket.assigns.task_parent_picker.parent_conflicted?
    assert Tasks.get_task_for_project(project, task.id).parent_task_id == latest_parent.id

    assert {:noreply, socket} = ProjectLive.handle_event("clear_task_parent", %{}, socket)

    assert socket.assigns.task_parent_picker.selected_parent == nil
    assert socket.assigns.task_parent_picker.parent_conflicted?
    assert Tasks.get_task_for_project(project, task.id).parent_task_id == latest_parent.id

    assert {:noreply, _socket} =
             ProjectLive.handle_event(
               "resolve_task_parent_conflict",
               %{"field" => "parent_task_id", "resolution" => "keep_mine"},
               socket
             )

    assert Tasks.get_task_for_project(project, task.id).parent_task_id == nil
  end
end
