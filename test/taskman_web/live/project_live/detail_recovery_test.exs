defmodule TaskmanWeb.ProjectLive.DetailRecoveryTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks
  alias TaskmanWeb.ProjectLive
  alias TaskmanWeb.ProjectLive.Paths
  alias TaskmanWeb.ProjectLive.Reconciliation
  alias TaskmanWeb.ProjectLive.Recovery
  alias TaskmanWeb.ProjectLive.Tasks.ParentPicker

  setup %{conn: conn} do
    previous = Application.get_env(:taskman, :task_autosave_delay_ms)
    Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)

    on_exit(fn ->
      case previous do
        nil -> Application.delete_env(:taskman, :task_autosave_delay_ms)
        value -> Application.put_env(:taskman, :task_autosave_delay_ms, value)
      end
    end)

    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "restarts reconciled dirty fields at the Task's fresh location with independent lifecycle feedback",
       %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine", description: "Persisted description"})
    |> render_change(%{"_target" => ["task", "title"]})

    view
    |> form("#task-form", task: %{title: "Mine", description: "Mine too"})
    |> render_change(%{"_target" => ["task", "description"]})

    old_title_revision = view_assigns(view).editing.autosave.revisions["title"]
    old_description_revision = view_assigns(view).editing.autosave.revisions["description"]
    assert {:ok, moved} = Tasks.move_task(project, task, destination)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert_patch(view, ~p"/projects/#{project.id}/lists/#{destination.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-form #task-title[value='Mine']")
    assert has_element?(view, "#task-form #task-description", "Mine too")
    assert has_element?(view, "#task-title-save-status[data-state='saving']", "Saving…")
    assert has_element?(view, "#task-description-save-status[data-state='saving']", "Saving…")
    refute has_element?(view, "#task-recovery")
    assert Tasks.get_task_for_project(project, task.id).title == "Persisted"
    assert Tasks.get_task_for_project(project, task.id).description == ""

    fresh_title_revision = view_assigns(view).editing.autosave.revisions["title"]
    fresh_description_revision = view_assigns(view).editing.autosave.revisions["description"]
    assert fresh_title_revision > max(old_title_revision, old_description_revision)
    assert fresh_description_revision > max(old_title_revision, old_description_revision)

    send(view.pid, {:autosave_task_field, task.id, "title", old_title_revision})
    render(view)

    assert Tasks.get_task_for_project(project, task.id).title == "Persisted"
    assert has_element?(view, "#task-form #task-title[value='Mine']")

    send(view.pid, {:autosave_task_field, task.id, "title", fresh_title_revision})
    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, moved.id).title == "Mine"
    assert Tasks.get_task_for_project(project, moved.id).description == ""
    assert has_element?(view, "#task-title-save-status[data-state='saved']", "Saved")
    assert has_element?(view, "#task-description-save-status[data-state='saving']", "Saving…")

    send(view.pid, {:autosave_task_field, task.id, "description", fresh_description_revision})
    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, moved.id).description == "Mine too"
    assert has_element?(view, "#task-description-save-status[data-state='saved']", "Saved")
  end

  test "recovery retains invalid field feedback while a valid sibling restarts and saves", %{
    conn: conn
  } do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, lost, %{title: "Persisted", description: "Old"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "", description: "Old"})
    |> render_change(%{"_target" => ["task", "title"]})

    view
    |> form("#task-form", task: %{title: "", description: "Mine"})
    |> render_change(%{"_target" => ["task", "description"]})

    old_description_revision = view_assigns(view).editing.autosave.revisions["description"]
    assert {:ok, _moved} = Tasks.move_task(project, task, destination)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    fresh_description_revision = view_assigns(view).editing.autosave.revisions["description"]
    assert fresh_description_revision > old_description_revision
    assert has_element?(view, "#task-title-save-status[data-state='not_saved']", "Not saved")
    assert has_element?(view, "#task-description-save-status[data-state='saving']", "Saving…")
    assert Tasks.get_task_for_project(project, task.id).title == "Persisted"
    assert Tasks.get_task_for_project(project, task.id).description == "Old"

    send(view.pid, {:autosave_task_field, task.id, "description", old_description_revision})
    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, task.id).description == "Old"
    assert has_element?(view, "#task-title-save-status[data-state='not_saved']", "Not saved")

    send(view.pid, {:autosave_task_field, task.id, "description", fresh_description_revision})
    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, task.id).description == "Mine"
    assert has_element?(view, "#task-description-save-status[data-state='saved']", "Saved")
    assert has_element?(view, "#task-title-save-status[data-state='not_saved']", "Not saved")
  end

  test "recovery retains a conflict while a valid sibling restarts and ignores its stale tuple",
       %{
         conn: conn
       } do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, lost, %{title: "Persisted", description: "Old"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine", description: "Old"})
    |> render_change(%{"_target" => ["task", "title"]})

    view
    |> form("#task-form", task: %{title: "Mine", description: "Mine too"})
    |> render_change(%{"_target" => ["task", "description"]})

    old_description_revision = view_assigns(view).editing.autosave.revisions["description"]
    assert {:ok, latest} = Tasks.update_task(project, task, %{title: "Latest"})
    assert {:ok, _moved} = Tasks.move_task(project, latest, destination)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    fresh_description_revision = view_assigns(view).editing.autosave.revisions["description"]
    assert fresh_description_revision > old_description_revision
    assert has_element?(view, "#task-title-conflict", "Latest")
    refute has_element?(view, "#task-title-save-status[data-state]")
    assert has_element?(view, "#task-description-save-status[data-state='saving']", "Saving…")
    assert Tasks.get_task_for_project(project, task.id).title == "Latest"
    assert Tasks.get_task_for_project(project, task.id).description == "Old"

    send(view.pid, {:autosave_task_field, task.id, "description", old_description_revision})
    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, task.id).description == "Old"
    assert has_element?(view, "#task-title-conflict", "Latest")
    assert has_element?(view, "#task-description-save-status[data-state='saving']", "Saving…")

    send(view.pid, {:autosave_task_field, task.id, "description", fresh_description_revision})
    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, task.id).description == "Mine too"
    assert has_element?(view, "#task-description-save-status[data-state='saved']", "Saved")
    assert has_element?(view, "#task-title-conflict", "Latest")
  end

  test "keeps direct and included-descendant backdrops independent of status filters", %{
    conn: conn
  } do
    project = project_fixture(%{})
    parent = list_fixture(project, nil, %{name: "Parent"})
    child = list_fixture(project, parent, %{name: "Child"})
    grandchild = list_fixture(project, child, %{name: "Grandchild"})

    for {backdrop, task_list, include_children?} <- [
          {parent, parent, false},
          {parent, grandchild, true},
          {nil, nil, false},
          {nil, child, true}
        ] do
      task =
        if task_list do
          task_fixture(project, task_list, %{title: "Persisted", status: :pending})
        else
          task_fixture(project, %{title: "Persisted", status: :pending})
        end

      path = Paths.task_detail_path(project, backdrop, task, include_children?)
      path = if include_children?, do: path <> "?include_children=true", else: path
      {:ok, view, _} = live(conn, path)

      view |> element("#task-status-filter-button") |> render_click()

      view
      |> form("#task-status-filter-form", status_filter: %{statuses: ["done"]})
      |> render_change()

      view
      |> form("#task-form", task: %{title: "Mine"})
      |> render_change(%{"_target" => ["task", "title"]})

      old_revision = view_assigns(view).editing.autosave.revisions["title"]
      continued = Recovery.enter(live_socket(view), view_assigns(view).workspace)

      assert continued.redirected == nil
      assert continued.assigns.recovery.snapshot == nil
      assert continued.assigns.editing.autosave.form[:title].value == "Mine"
      assert continued.assigns.workspace.include_children? == include_children?
      assert Tasks.get_task_for_project(project, task.id).title == "Persisted"

      document = socket_document(continued)
      refute Enum.empty?(LazyHTML.query(document, "#task-form #task-title[value='Mine']"))
      assert Enum.empty?(LazyHTML.query(document, "#task-recovery"))

      assert {:noreply, after_stale} =
               Reconciliation.handle_info(
                 {:autosave_task_field, task.id, "title", old_revision},
                 continued
               )

      assert after_stale.assigns.editing.autosave.form[:title].value == "Mine"
      assert Tasks.get_task_for_project(project, task.id).title == "Persisted"
    end
  end

  test "excluded descendants and unrelated locations relocate while preserving child visibility",
       %{conn: conn} do
    project = project_fixture(%{})
    parent = list_fixture(project, nil, %{name: "Parent"})
    child = list_fixture(project, parent, %{name: "Child"})
    unrelated = list_fixture(project, nil, %{name: "Unrelated"})

    for {actual, include_children?} <- [{child, false}, {unrelated, true}] do
      task = task_fixture(project, actual, %{title: "Persisted"})
      suffix = if include_children?, do: "?include_children=true", else: ""

      {:ok, view, _} =
        live(
          conn,
          "/projects/#{project.id}/lists/#{parent.id}/tasks/#{task.id}#{suffix}"
        )

      view
      |> form("#task-form", task: %{title: "Mine"})
      |> render_change(%{"_target" => ["task", "title"]})

      pending = Recovery.enter(live_socket(view), view_assigns(view).workspace)

      assert {:live, :patch, %{to: path, kind: :replace}} = pending.redirected

      expected = Paths.task_detail_path(project, actual, task, include_children?)
      assert path == expected

      params = %{
        "project_id" => "#{project.id}",
        "list_id" => "#{actual.id}",
        "task_id" => "#{task.id}"
      }

      params = if include_children?, do: Map.put(params, "include_children", "true"), else: params

      assert {:noreply, restored} =
               ProjectLive.handle_params(params, nil, Map.put(pending, :redirected, nil))

      assert restored.assigns.recovery.snapshot == nil
      assert restored.assigns.editing.autosave.form[:title].value == "Mine"
      assert restored.assigns.workspace.include_children? == include_children?
      assert Tasks.get_task_for_project(project, task.id).title == "Persisted"

      document = socket_document(restored)
      refute Enum.empty?(LazyHTML.query(document, "#task-form #task-title[value='Mine']"))
      assert Enum.empty?(LazyHTML.query(document, "#task-recovery"))

      old_revision = view_assigns(view).editing.autosave.revisions["title"]

      assert {:noreply, after_stale} =
               Reconciliation.handle_info(
                 {:autosave_task_field, task.id, "title", old_revision},
                 restored
               )

      assert after_stale.assigns.editing.autosave.form[:title].value == "Mine"
      assert Tasks.get_task_for_project(project, task.id).title == "Persisted"
    end
  end

  test "a second Task move during route completion converges by replacement patch without writing",
       %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    first = list_fixture(project, nil, %{name: "First"})
    second = list_fixture(project, nil, %{name: "Second"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert {:ok, moved} = Tasks.move_task(project, task, first)
    Taskman.Repo.delete!(lost)

    assert {:noreply, pending} =
             Reconciliation.handle_info(list_event(project.id, lost.id), live_socket(view))

    assert {:ok, _moved_again} = Tasks.move_task(project, moved, second)

    old_params = %{
      "project_id" => "#{project.id}",
      "list_id" => "#{first.id}",
      "task_id" => "#{task.id}"
    }

    assert {:noreply, converging} =
             ProjectLive.handle_params(old_params, nil, Map.put(pending, :redirected, nil))

    assert converging.assigns.recovery.snapshot.id == 1
    assert converging.assigns.recovery.pending.list_id == second.id
    assert {:live, :patch, %{to: path, kind: :replace}} = converging.redirected
    assert path == ~p"/projects/#{project.id}/lists/#{second.id}/tasks/#{task.id}"

    new_params = %{old_params | "list_id" => "#{second.id}"}

    assert {:noreply, restored} =
             ProjectLive.handle_params(new_params, nil, Map.put(converging, :redirected, nil))

    assert restored.assigns.recovery.snapshot == nil
    assert restored.assigns.editing.autosave.form[:title].value == "Mine"
    assert Tasks.get_task_for_project(project, task.id).title == "Persisted"
  end

  test "missing Task retains the detail snapshot with the exact recovery error", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> element("#move-task-detail-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert has_element?(view, "#task-recovery-title", "This task is no longer available")
    refute has_element?(view, "#task-recovery-resume")

    assert has_element?(view, "#task-recovery #task-title[value='Mine']")
  end

  test "Reopen retries fresh authority and continues when the Task resolves again", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> element("#move-task-detail-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert has_element?(view, "#task-recovery #task-title[value='Mine']")
    assert view_assigns(view).recovery.snapshot.task_move.query == "Destination"
    assert view_assigns(view).recovery.snapshot.task_move.destination == "list:#{destination.id}"

    restored_task =
      task
      |> Map.put(:list_id, nil)
      |> Ecto.put_meta(state: :built)
      |> Taskman.Repo.insert!()

    render_hook(view, "resume_task_recovery", %{
      "recovery_id" => Integer.to_string(view_assigns(view).recovery.snapshot.id)
    })

    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-form #task-title[value='Mine']")
    assert has_element?(view, "#move-task-#{task.id}")
    assert has_element?(view, "#move-task-search-#{task.id}[value='Destination']")
    assert view_assigns(view).task_move.destination == "list:#{destination.id}"
    refute has_element?(view, "#task-recovery")
    assert Tasks.get_task_for_project(project, restored_task.id).title == "Persisted"
  end

  test "actual List loss during pending detail completion retains the snapshot", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert {:ok, moved} = Tasks.move_task(project, task, destination)
    Taskman.Repo.delete!(lost)

    assert {:noreply, socket} =
             Reconciliation.handle_info(list_event(project.id, lost.id), live_socket(view))

    pending_socket = socket

    assert {:ok, _root_task} = Tasks.move_task(project, moved, nil)
    Taskman.Repo.delete!(destination)

    params = %{
      "project_id" => Integer.to_string(project.id),
      "list_id" => Integer.to_string(destination.id),
      "task_id" => Integer.to_string(task.id)
    }

    assert {:noreply, completed} =
             ProjectLive.handle_params(params, nil, Map.put(pending_socket, :redirected, nil))

    assert completed.assigns.recovery.snapshot.id == 1
    assert completed.assigns.recovery.pending.list_id == nil
    assert {:live, :patch, %{to: path, kind: :replace}} = completed.redirected
    assert path == ~p"/projects/#{project.id}/tasks/#{task.id}"

    root_socket = Map.put(completed, :redirected, nil)

    assert {:noreply, restored} =
             ProjectLive.handle_params(
               %{"project_id" => "#{project.id}", "task_id" => "#{task.id}"},
               nil,
               root_socket
             )

    assert restored.assigns.recovery.snapshot == nil
    assert restored.assigns.editing.autosave.form[:title].value == "Mine"
    assert Tasks.get_task_for_project(project, task.id).title == "Persisted"
  end

  test "a mismatching route automatically reconverges to the fresh detail authority", %{
    conn: conn
  } do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert {:ok, _moved} = Tasks.move_task(project, task, destination)
    Taskman.Repo.delete!(lost)

    assert {:noreply, pending_socket} =
             Reconciliation.handle_info(list_event(project.id, lost.id), live_socket(view))

    index_socket =
      pending_socket
      |> Phoenix.Component.assign(:live_action, :index)
      |> Map.put(:redirected, nil)

    assert {:noreply, interrupted} = ProjectLive.handle_params(%{}, nil, index_socket)
    assert interrupted.assigns.recovery.snapshot.id == 1
    assert interrupted.assigns.recovery.pending.list_id == destination.id
    assert interrupted.assigns.recovery.error == nil
    assert Recovery.view(interrupted) == nil
    assert {:live, :patch, %{to: path, kind: :replace}} = interrupted.redirected
    assert path == ~p"/projects/#{project.id}/lists/#{destination.id}/tasks/#{task.id}"

    params = %{
      "project_id" => "#{project.id}",
      "list_id" => "#{destination.id}",
      "task_id" => "#{task.id}"
    }

    action_socket =
      interrupted
      |> Phoenix.Component.assign(:live_action, :show_task)
      |> Map.put(:redirected, nil)

    assert {:noreply, later} = ProjectLive.handle_params(params, nil, action_socket)
    assert later.assigns.recovery.snapshot == nil
    assert later.assigns.editing.autosave.form[:title].value == "Mine"
    document = socket_document(later)
    refute Enum.empty?(LazyHTML.query(document, "#task-form #task-title[value='Mine']"))
    assert Enum.empty?(LazyHTML.query(document, "#task-recovery"))
    assert Tasks.get_task_for_project(project, task.id).title == "Persisted"
  end

  test "Reopen continues after missing Project authority resolves without writing", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    old_revision = view_assigns(view).editing.autosave.revisions["title"]
    previous_workspace = view_assigns(view).workspace
    Taskman.Repo.delete!(project)
    recovered = Recovery.enter(live_socket(view), previous_workspace)

    assert Recovery.view(recovered).error ==
             "This Project is no longer available. Copy your input or discard it."

    restored_project = project |> Ecto.put_meta(state: :built) |> Taskman.Repo.insert!()

    restored_task =
      task
      |> Map.put(:list_id, nil)
      |> Ecto.put_meta(state: :built)
      |> Taskman.Repo.insert!()

    assert {:noreply, pending} =
             Recovery.handle_event(
               "resume_task_recovery",
               %{"recovery_id" => "1"},
               recovered
             )

    assert {:live, :patch, %{to: path, kind: :replace}} = pending.redirected
    assert path == ~p"/projects/#{restored_project.id}/tasks/#{restored_task.id}"

    pending =
      pending
      |> Phoenix.Component.assign(:live_action, :show_task)
      |> Map.put(:redirected, nil)

    assert {:noreply, restored} =
             ProjectLive.handle_params(
               %{"project_id" => "#{restored_project.id}", "task_id" => "#{restored_task.id}"},
               nil,
               pending
             )

    document = socket_document(restored)
    refute Enum.empty?(LazyHTML.query(document, "#task-form #task-title[value='Mine']"))
    assert Enum.empty?(LazyHTML.query(document, "#task-recovery"))

    assert {:noreply, after_stale} =
             Reconciliation.handle_info(
               {:autosave_task_field, restored_task.id, "title", old_revision},
               restored
             )

    assert after_stale.assigns.editing.autosave.form[:title].value == "Mine"
    assert Tasks.get_task_for_project(restored_project, restored_task.id).title == "Persisted"
  end

  test "Reopen continues after missing current List authority resolves without writing", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Temporarily unavailable"})
    task = task_fixture(project, task_list, %{title: "Persisted"})

    {:ok, view, _} =
      live(conn, ~p"/projects/#{project.id}/lists/#{task_list.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    old_revision = view_assigns(view).editing.autosave.revisions["title"]
    previous_workspace = view_assigns(view).workspace
    delete_list_without_fk_check(task_list)
    recovered = Recovery.enter(live_socket(view), previous_workspace)

    assert Recovery.view(recovered).error == "That destination is no longer available."

    restored_list = task_list |> Ecto.put_meta(state: :built) |> Taskman.Repo.insert!()

    assert {:noreply, restored} =
             Recovery.handle_event(
               "resume_task_recovery",
               %{"recovery_id" => "1"},
               recovered
             )

    assert restored.redirected == nil
    document = socket_document(restored)
    refute Enum.empty?(LazyHTML.query(document, "#task-form #task-title[value='Mine']"))
    assert Enum.empty?(LazyHTML.query(document, "#task-recovery"))

    assert {:noreply, after_stale} =
             Reconciliation.handle_info(
               {:autosave_task_field, task.id, "title", old_revision},
               restored
             )

    assert after_stale.assigns.workspace.selected_list.id == restored_list.id
    assert after_stale.assigns.editing.autosave.form[:title].value == "Mine"
    assert Tasks.get_task_for_project(project, task.id).title == "Persisted"
  end

  test "source Project loss retains detail input with the Project recovery error", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert {:ok, _root_task} = Tasks.move_task(project, task, nil)
    Taskman.Repo.delete!(lost)
    previous_workspace = view_assigns(view).workspace
    Taskman.Repo.delete!(project)

    recovered = Recovery.enter(live_socket(view), previous_workspace)

    assert Recovery.view(recovered).error ==
             "This Project is no longer available. Copy your input or discard it."

    assert recovered.assigns.recovery.snapshot.editing.autosave.form[:title].value == "Mine"
  end

  test "mismatched detail recovery identity is inert", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)
    captured = view_assigns(view).recovery

    render_hook(view, "resume_task_recovery", %{"recovery_id" => "2"})

    assert view_assigns(view).recovery == captured
    assert has_element?(view, "#task-recovery #task-title[value='Mine']")
  end

  test "rebuilds a captured detail move and invalidates its missing destination without moving",
       %{
         conn: conn
       } do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    actual = list_fixture(project, nil, %{name: "Actual"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> element("#move-task-detail-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()

    assert {:ok, _moved} = Tasks.move_task(project, task, actual)
    Taskman.Repo.delete!(destination)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert_patch(view, ~p"/projects/#{project.id}/lists/#{actual.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-form #task-title[value='Mine']")
    assert has_element?(view, "#move-task-#{task.id}")
    assert has_element?(view, "#move-task-search-#{task.id}[value='Destination']")

    assert view_assigns(view).task_move.error ==
             "That destination is no longer available. Choose another destination."

    assert has_element?(
             view,
             "#move-task-error-#{task.id}",
             "That destination is no longer available. Choose another destination."
           )

    assert has_element?(view, "#move-task-submit-#{task.id}[disabled]")
    assert Tasks.get_task_for_project(project, task.id).list_id == actual.id
  end

  test "fresh ordinary-field changes become conflicts when detail is reopened", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    actual = list_fixture(project, nil, %{name: "Actual"})
    task = task_fixture(project, lost, %{title: "Before"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert {:ok, latest} = Tasks.update_task(project, task, %{title: "Latest"})
    assert {:ok, _moved} = Tasks.move_task(project, latest, actual)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)
    assert has_element?(view, "#task-title[value='Mine']")
    assert has_element?(view, "#task-title-conflict", "Latest")
    assert view_assigns(view).editing.autosave.save_state == :conflicted
    assert Tasks.get_task_for_project(project, task.id).title == "Latest"
  end

  test "restored detail move cannot bypass the save-before-move conflict gate", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    actual = list_fixture(project, nil, %{name: "Actual"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, lost, %{title: "Before"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "Mine"})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> element("#move-task-detail-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()

    assert {:ok, latest} = Tasks.update_task(project, task, %{title: "Latest"})
    assert {:ok, _moved} = Tasks.move_task(project, latest, actual)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)
    assert_patch(view, ~p"/projects/#{project.id}/lists/#{actual.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-title-conflict", "Latest")
    assert has_element?(view, "#move-task-search-#{task.id}[value='Destination']")

    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).list_id == actual.id
    assert has_element?(view, "#task-title-conflict", "Latest")

    assert has_element?(
             view,
             "#move-task-error-#{task.id}",
             "Save the Task before moving it."
           )

    view |> element("#use-latest-title") |> render_click()
    view |> element("#move-task-submit-#{task.id}") |> render_click()

    moved = Tasks.get_task_for_project(project, task.id)
    assert moved.title == "Latest"
    assert moved.list_id == destination.id
    refute has_element?(view, "#move-task-#{task.id}")
  end

  test "captured parent conflict refreshes against the latest persisted parent", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    actual = list_fixture(project, nil, %{name: "Actual"})
    initial = task_fixture(project, actual, %{title: "Initial parent"})
    mine = task_fixture(project, actual, %{title: "My parent"})
    latest_parent = task_fixture(project, actual, %{title: "Latest parent"})
    task = task_fixture(project, lost, %{title: "Task"}, parent: initial)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    picker =
      ParentPicker.empty()
      |> ParentPicker.open_edit(project, task)
      |> ParentPicker.select_draft(project, mine.id)

    assert {:ok, latest_task} = Tasks.update_task(project, task, %{}, parent: latest_parent)

    assert {:conflict, conflicted_picker, ^latest_task} =
             ParentPicker.save_edit(picker, project, task)

    replace_assign(view, :task_parent_picker, conflicted_picker)

    assert {:ok, _moved} = Tasks.move_task(project, latest_task, actual)
    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)
    restored = view_assigns(view).task_parent_picker
    assert restored.parent_conflicted?
    assert restored.selected_parent.id == mine.id
    assert restored.conflict_parent.id == latest_parent.id
    assert Tasks.get_task_for_project(project, task.id).parent_task_id == latest_parent.id
  end

  defp list_event(project_id, list_id) do
    %Event{
      entity: :list,
      operation: :updated,
      project_id: project_id,
      entity_id: list_id,
      lock_version: nil,
      fields: [:name]
    }
  end

  defp view_assigns(view) do
    %{socket: socket} = :sys.get_state(view.pid)
    socket.assigns
  end

  defp live_socket(view) do
    %{socket: socket} = :sys.get_state(view.pid)
    socket
  end

  defp socket_document(socket) do
    socket.assigns
    |> ProjectLive.render()
    |> rendered_to_string()
    |> LazyHTML.from_fragment()
  end

  defp delete_list_without_fk_check(task_list) do
    Ecto.Adapters.SQL.query!(Taskman.Repo, "SET LOCAL session_replication_role = replica", [])

    try do
      Taskman.Repo.delete!(task_list)
    after
      Ecto.Adapters.SQL.query!(Taskman.Repo, "SET LOCAL session_replication_role = origin", [])
    end
  end

  defp replace_assign(view, key, value) do
    :sys.replace_state(view.pid, fn %{socket: socket} = state ->
      %{state | socket: Phoenix.Component.assign(socket, key, value)}
    end)
  end
end
