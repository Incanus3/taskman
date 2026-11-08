defmodule TaskmanWeb.ProjectLive.DescriptionCaptureTest do
  use TaskmanWeb.ConnCase, async: false
  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures
  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks
  alias TaskmanWeb.ProjectLive.Tasks.Autosave

  setup %{conn: conn} do
    previous = Application.get_env(:taskman, :task_autosave_delay_ms)
    Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)

    on_exit(fn ->
      if previous,
        do: Application.put_env(:taskman, :task_autosave_delay_ms, previous),
        else: Application.delete_env(:taskman, :task_autosave_delay_ms)
    end)

    project = project_fixture(%{})
    task = task_fixture(project, %{description: "Original"})

    {:ok, view, _} =
      live(log_in_user(conn, user_fixture()), ~p"/projects/#{project.id}/tasks/#{task.id}")

    %{view: view, project: project, task: task}
  end

  test "departure waits for matching metadata and capture, rejects stale and duplicate replies",
       %{view: view, project: project, task: task} do
    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: ""
    })

    pending = state(view).description.pending_capture
    assert pending
    assert has_element?(view, "#task-modal-content")

    send(
      view.pid,
      {:leaf_flushed,
       %{editor_id: state(view).description.editor_id, ref: "stale", markdown: "Lost"}}
    )

    render(view)
    assert has_element?(view, "#task-modal-content")
    reply(view, pending.ref, "Final character!", true)
    assert_patch(view, ~p"/projects/#{project.id}")
    assert Tasks.get_task_for_project(project, task.id).description == "Final character!"

    send(
      view.pid,
      {:leaf_flushed,
       %{editor_id: "task-description-editor-1", ref: pending.ref, markdown: "Duplicate"}}
    )

    render(view)
    assert Tasks.get_task_for_project(project, task.id).description == "Final character!"
  end

  test "untouched normalized capture does not rewrite canonical source", %{
    view: view,
    project: project,
    task: task
  } do
    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: ""
    })

    pending = state(view).description.pending_capture
    assert pending
    reply(view, pending.ref, "Original\n", false)
    assert_patch(view, ~p"/projects/#{project.id}")
    assert Tasks.get_task_for_project(project, task.id).description == task.description
  end

  test "queued autosave cannot acknowledge older input during final capture", %{
    view: view,
    project: project,
    task: task
  } do
    change(view, "A", true)
    revision = state(view).editing.autosave.revisions["description"]

    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: ""
    })

    pending = state(view).description.pending_capture
    assert pending
    send(view.pid, {:autosave_task_field, task.id, "description", revision})
    render(view)
    assert Tasks.get_task_for_project(project, task.id).description == "Original"
    reply(view, pending.ref, "B", true)
    assert_patch(view, ~p"/projects/#{project.id}")
    assert Tasks.get_task_for_project(project, task.id).description == "B"
  end

  test "timeout retains editor and destination until explicit retry", %{
    view: view,
    project: project
  } do
    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: ""
    })

    pending = state(view).description.pending_capture
    assert pending
    send(view.pid, {:description_capture_timeout, pending.ref})
    render(view)
    assert has_element?(view, "#task-modal-content")
    assert has_element?(view, "#retry-description-capture")
    view |> element("#retry-description-capture") |> render_click()
    fresh = state(view).description.pending_capture
    assert fresh.ref != pending.ref
    reply(view, pending.ref, "Stale", true)
    assert has_element?(view, "#task-modal-content")
    reply(view, fresh.ref, "Retry!", true)
    assert_patch(view, ~p"/projects/#{project.id}")
  end

  test "pure browser capture retains baseline without writes or scheduled revisions", %{
    project: project,
    task: task
  } do
    autosave =
      Autosave.load(Autosave.empty(), task,
        saved?: false,
        actor: Taskman.AccountsFixtures.user_fixture()
      )

    captured = Autosave.capture_description(autosave, task, "Final character!")
    assert captured.draft["description"] == "Final character!"
    assert captured.baseline.description == task.description
    assert MapSet.member?(captured.dirty_fields, "description")
    refute Map.has_key?(captured.revisions, "description")
    assert captured.save_state == :not_saved
    assert Tasks.get_task_for_project(project, task.id).description == task.description
  end

  test "route application retains the old editor until browser capture", %{
    view: view,
    project: project,
    task: task
  } do
    other = task_fixture(project, %{description: "Other"})
    old_id = state(view).description.editor_id
    render_patch(view, ~p"/projects/#{project.id}/tasks/#{other.id}")
    assert state(view).editing.selected_task.id == task.id
    assert state(view).description.editor_id == old_id
    pending = state(view).description.pending_capture
    assert pending
    reply(view, pending.ref, "Before switch!", true)
    assert state(view).editing.selected_task.id == other.id
    assert Tasks.get_task_for_project(project, task.id).description == "Before switch!"
  end

  test "route capture timeout restores origin and retry retains the requested destination", %{
    view: view,
    project: project,
    task: task
  } do
    destination = ~p"/projects/#{project.id}?include_children=true"
    origin = ~p"/projects/#{project.id}/tasks/#{task.id}"
    change(view, "Earlier delivered draft", true)
    original_id = state(view).description.editor_id
    render_patch(view, destination)
    assert_patch(view, destination)
    pending = state(view).description.pending_capture
    send(view.pid, {:description_capture_timeout, pending.ref})
    render(view)
    assert_patch(view, origin)
    assert state(view).description.pending_capture.timed_out?
    assert state(view).description.editor_id == original_id
    assert Tasks.get_task_for_project(project, task.id).description == "Original"

    assert has_element?(
             view,
             "#task-comment-departure-guard[data-description-capture-pending='true']"
           )

    view |> element("#retry-description-capture") |> render_click()
    retried = state(view).description.pending_capture
    refute retried.ref == pending.ref
    reply(view, retried.ref, "Final character!", true)
    assert_patch(view, destination)
    assert Tasks.get_task_for_project(project, task.id).description == "Final character!"
  end

  test "creation submit captures browser text over the submitted hidden value", %{
    conn: conn,
    project: project
  } do
    {:ok, view, _} =
      live(log_in_user(conn, user_fixture()), ~p"/projects/#{project.id}/tasks/new")

    view
    |> form("#task-form", task: %{title: "Created"}, location: "project")
    |> render_submit(%{"task" => %{"description" => "Stale"}})

    pending = state(view).description.pending_capture
    assert pending
    refute Enum.any?(Tasks.list_tasks_for_project(project), &(&1.title == "Created"))
    reply(view, pending.ref, "Final browser character!", true)

    assert Enum.find(Tasks.list_tasks_for_project(project), &(&1.title == "Created")).description ==
             "Final browser character!"
  end

  test "incoming replacement classifies the captured final character as a conflict", %{
    view: view,
    project: project,
    task: task
  } do
    {:ok, _} =
      Tasks.update_task(project, task, %{description: "External"},
        actor: Taskman.AccountsFixtures.user_fixture()
      )

    render(view)
    pending = state(view).description.pending_capture
    assert pending
    assert state(view).description.initial_value == "Original"
    reply(view, pending.ref, "Final local!", true)
    assert has_element?(view, "#task-description-conflict")
    assert state(view).editing.autosave.form[:description].value == "Final local!"
    assert Tasks.get_task_for_project(project, task.id).description == "External"
  end

  test "late acknowledgement for A retains B as pending against the saved baseline", %{
    view: view,
    project: project,
    task: task
  } do
    change(view, "A", true)
    revision = state(view).editing.autosave.revisions["description"]
    send(view.pid, {:autosave_task_field, task.id, "description", revision})
    render(view)
    saved = Tasks.get_task_for_project(project, task.id)
    change(view, "B", true)

    send(view.pid, %Event{
      entity: :task,
      operation: :updated,
      project_id: project.id,
      entity_id: task.id,
      lock_version: saved.lock_version,
      fields: [:description]
    })

    render(view)
    assert state(view).description.pending_capture == nil
    assert Tasks.get_task_for_project(project, task.id).description == "A"
    assert state(view).editing.autosave.baseline.description == "A"
    assert state(view).editing.autosave.form[:description].value == "B"
    assert has_element?(view, "#task-description-save-status[data-state='saving']")
    refute has_element?(view, "#task-description-conflict")
  end

  test "location loss captures final browser input before recovery clears editing", %{
    conn: conn,
    project: project
  } do
    lost = Taskman.ListsFixtures.list_fixture(project, nil, %{name: "Lost"})
    actual = Taskman.ListsFixtures.list_fixture(project, nil, %{name: "Actual"})
    task = task_fixture(project, lost, %{description: "Original"})

    {:ok, view, _} =
      live(
        log_in_user(conn, user_fixture()),
        ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}"
      )

    change(view, "A", true)
    revision = state(view).editing.autosave.revisions["description"]

    {:ok, _} =
      Tasks.move_task(project, task, actual, actor: Taskman.AccountsFixtures.user_fixture())

    Taskman.Repo.delete!(lost)

    send(view.pid, %Event{
      entity: :list,
      operation: :updated,
      project_id: project.id,
      entity_id: lost.id,
      fields: [:name]
    })

    render(view)
    assert state(view).editing.selected_task.id == task.id
    assert state(view).recovery.snapshot == nil
    pending = state(view).description.pending_capture
    assert pending
    send(view.pid, {:autosave_task_field, task.id, "description", revision})
    render(view)
    assert Tasks.get_task_for_project(project, task.id).description == "Original"
    reply(view, pending.ref, "Final browser recovery!", true)
    assert_patch(view, ~p"/projects/#{project.id}/lists/#{actual.id}/tasks/#{task.id}")
    assert state(view).editing.autosave.form[:description].value == "Final browser recovery!"
    assert Tasks.get_task_for_project(project, task.id).description == "Original"
    refute state(view).description.pending_capture
  end

  test "a reply cannot complete without matching metadata or after timeout", %{
    view: view,
    project: project
  } do
    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: ""
    })

    pending = state(view).description.pending_capture
    id = state(view).description.editor_id

    send(
      view.pid,
      {:leaf_flushed, %{editor_id: "replaced-editor", ref: pending.ref, markdown: "Final!"}}
    )

    render(view)
    assert state(view).description.pending_capture.reply == nil
    send(view.pid, {:leaf_flushed, %{editor_id: id, ref: pending.ref, markdown: "Final!"}})
    render(view)
    assert has_element?(view, "#task-modal-content")
    send(view.pid, {:description_capture_timeout, pending.ref})
    render(view)
    change(view, "Final!", true)
    assert has_element?(view, "#task-modal-content")
    assert has_element?(view, "#retry-description-capture")
  end

  test "location loss during voluntary capture recovers the captured draft first", %{
    conn: conn,
    project: project
  } do
    lost = Taskman.ListsFixtures.list_fixture(project, nil, %{name: "Lost"})
    actual = Taskman.ListsFixtures.list_fixture(project, nil, %{name: "Actual"})
    task = task_fixture(project, lost, %{description: "Original"})

    {:ok, view, _} =
      live(
        log_in_user(conn, user_fixture()),
        ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}"
      )

    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: ""
    })

    pending = state(view).description.pending_capture

    {:ok, _} =
      Tasks.move_task(project, task, actual, actor: Taskman.AccountsFixtures.user_fixture())

    Taskman.Repo.delete!(lost)

    send(view.pid, %Event{
      entity: :list,
      operation: :updated,
      project_id: project.id,
      entity_id: lost.id,
      fields: [:name]
    })

    render(view)
    assert state(view).description.pending_capture.ref == pending.ref
    reply(view, pending.ref, "Final during disappearance!", true)
    assert_patch(view, ~p"/projects/#{project.id}/lists/#{actual.id}/tasks/#{task.id}")
    assert state(view).editing.autosave.form[:description].value == "Final during disappearance!"
    assert Tasks.get_task_for_project(project, task.id).description == "Original"
  end

  test "task notifications without a selected Project leave the workspace available", %{
    conn: conn,
    project: project,
    task: task
  } do
    {:ok, root, _} = live(log_in_user(conn, user_fixture()), ~p"/")

    send(root.pid, %Event{
      entity: :task,
      operation: :updated,
      project_id: project.id,
      entity_id: task.id,
      fields: [:description]
    })

    render(root)
    assert state(root).workspace.selected_project == nil
    assert state(root).description.editor_id == nil
  end

  test "creation submit joins an already pending authoritative refresh", %{
    conn: conn,
    project: project
  } do
    lost = Taskman.ListsFixtures.list_fixture(project, nil, %{name: "Lost"})

    {:ok, view, _} =
      live(
        log_in_user(conn, user_fixture()),
        ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/new"
      )

    Taskman.Repo.delete!(lost)

    send(view.pid, %Event{
      entity: :list,
      operation: :updated,
      project_id: project.id,
      entity_id: lost.id,
      fields: [:name]
    })

    render(view)
    pending = state(view).description.pending_capture
    assert pending
    view |> form("#task-form", task: %{title: "Joined"}, location: "project") |> render_submit()
    assert state(view).description.pending_capture.ref == pending.ref
    reply(view, pending.ref, "Joined final!", true)

    assert Enum.find(Tasks.list_tasks_for_project(project), &(&1.title == "Joined")).description ==
             "Joined final!"
  end

  test "capturing already saved browser text does not write it again", %{
    view: view,
    project: project,
    task: task
  } do
    change(view, "Saved A", true)
    revision = state(view).editing.autosave.revisions["description"]
    send(view.pid, {:autosave_task_field, task.id, "description", revision})
    render(view)
    saved = Tasks.get_task_for_project(project, task.id)

    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: ""
    })

    pending = state(view).description.pending_capture
    reply(view, pending.ref, "Saved A", true)
    assert_patch(view, ~p"/projects/#{project.id}")
    assert Tasks.get_task_for_project(project, task.id).lock_version == saved.lock_version
  end

  test "saved editor text remains clean against a newer canonical replacement", %{
    view: view,
    project: project,
    task: task
  } do
    change(view, "Saved A", true)
    revision = state(view).editing.autosave.revisions["description"]
    send(view.pid, {:autosave_task_field, task.id, "description", revision})
    render(view)
    saved = Tasks.get_task_for_project(project, task.id)

    {:ok, _} =
      Tasks.update_task(project, saved, %{description: "External B"},
        actor: Taskman.AccountsFixtures.user_fixture()
      )

    render(view)
    pending = state(view).description.pending_capture
    assert pending
    reply(view, pending.ref, "Saved A", true)
    refute has_element?(view, "#task-description-conflict")
    assert state(view).description.initial_value == "External B"
    assert state(view).editing.autosave.form[:description].value == "External B"
  end

  test "cancelling comment departure keeps captured description eligible for autosave", %{
    view: view,
    project: project,
    task: task
  } do
    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: "Unposted"
    })

    pending = state(view).description.pending_capture
    reply(view, pending.ref, "Captured before confirmation!", true)
    assert has_element?(view, "#task-comment-departure")
    view |> element("#task-comment-departure-go-back") |> render_click()
    revision = state(view).editing.autosave.revisions["description"]
    assert is_integer(revision)
    send(view.pid, {:autosave_task_field, task.id, "description", revision})
    render(view)

    assert Tasks.get_task_for_project(project, task.id).description ==
             "Captured before confirmation!"
  end

  test "forged creation submit cannot write while recovery retains a missing Task", %{
    conn: conn,
    project: project
  } do
    lost = Taskman.ListsFixtures.list_fixture(project, nil, %{name: "Lost"})
    task = task_fixture(project, lost, %{description: "Original"})

    {:ok, view, _} =
      live(
        log_in_user(conn, user_fixture()),
        ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}"
      )

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)

    send(view.pid, %Event{
      entity: :list,
      operation: :updated,
      project_id: project.id,
      entity_id: lost.id,
      fields: [:name]
    })

    render(view)
    pending = state(view).description.pending_capture
    reply(view, pending.ref, "Retained!", true)
    assert has_element?(view, "#task-recovery")
    render_hook(view, "save_task", %{task: %{title: "Forged"}, location: "project"})
    refute Enum.any?(Tasks.list_tasks_for_project(project), &(&1.title == "Forged"))
    assert has_element?(view, "#task-recovery")
  end

  test "pending and expired browser capture cannot show the previous value as saved", %{
    view: view,
    project: project,
    task: task
  } do
    change(view, "A", true)
    revision = state(view).editing.autosave.revisions["description"]
    send(view.pid, {:autosave_task_field, task.id, "description", revision})
    render(view)
    assert has_element?(view, "#task-description-save-status[data-state='saved']")

    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: ""
    })

    pending = state(view).description.pending_capture
    change(view, "B", true)
    refute has_element?(view, "#task-description-save-status[data-state='saved']")
    send(view.pid, {:description_capture_timeout, pending.ref})
    render(view)
    assert has_element?(view, "#task-description-save-status[data-state='not_saved']")
    assert Tasks.get_task_for_project(project, task.id).description == "A"
  end

  test "relationship refresh captures browser input before discovering location loss", %{
    conn: conn,
    project: project
  } do
    lost = Taskman.ListsFixtures.list_fixture(project, nil, %{name: "Lost"})
    task = task_fixture(project, lost, %{description: "Original"})

    {:ok, view, _} =
      live(
        log_in_user(conn, user_fixture()),
        ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}"
      )

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)

    send(view.pid, %Event{
      entity: :relationship,
      operation: :invalidated,
      project_id: project.id,
      entity_id: 1,
      task_ids: [task.id],
      fields: []
    })

    render(view)
    pending = state(view).description.pending_capture
    assert pending
    assert state(view).recovery.snapshot == nil
    reply(view, pending.ref, "Final relationship refresh!", true)
    assert has_element?(view, "#task-recovery")

    assert state(view).recovery.snapshot.editing.autosave.form[:description].value ==
             "Final relationship refresh!"
  end

  test "a clean replacement during capture retains the requested departure", %{
    view: view,
    project: project,
    task: task
  } do
    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: ""
    })

    pending = state(view).description.pending_capture

    {:ok, _} =
      Tasks.update_task(project, task, %{description: "New canonical"},
        actor: Taskman.AccountsFixtures.user_fixture()
      )

    render(view)
    reply(view, pending.ref, "Original", false)
    assert_patch(view, ~p"/projects/#{project.id}")
    assert Tasks.get_task_for_project(project, task.id).description == "New canonical"
  end

  test "route fallback retains and schedules final description after comment cancellation", %{
    view: view,
    project: project,
    task: task
  } do
    other = task_fixture(project)
    origin = ~p"/projects/#{project.id}/tasks/#{task.id}"
    view |> form("#task-comment-form", comment: %{text: "Unposted"}) |> render_change()
    render_patch(view, ~p"/projects/#{project.id}/tasks/#{other.id}")
    pending = state(view).description.pending_capture
    assert match?({:route, _, _}, pending.purpose)
    reply(view, pending.ref, "Browser final!", true)
    assert_patch(view, origin)
    assert has_element?(view, "#task-comment-departure")
    assert state(view).editing.autosave.form[:description].value == "Browser final!"
    view |> element("#task-comment-departure-go-back") |> render_click()
    revision = state(view).editing.autosave.revisions["description"]
    assert is_integer(revision)
    send(view.pid, {:autosave_task_field, task.id, "description", revision})
    render(view)
    assert Tasks.get_task_for_project(project, task.id).description == "Browser final!"
  end

  for initial <- ["", "  Original comment  "] do
    test "retry preserves a newer validated comment after starting with #{inspect(initial)}", %{
      view: view,
      project: project
    } do
      initial = unquote(initial)

      render_hook(view, "request_task_departure", %{
        destination: ~p"/projects/#{project.id}",
        draft: initial
      })

      pending = state(view).description.pending_capture
      send(view.pid, {:description_capture_timeout, pending.ref})
      render(view)
      current = "  Newer comment!\nExact spacing.  "
      view |> form("#task-comment-form", comment: %{text: current}) |> render_change()
      view |> element("#retry-description-capture") |> render_click()
      fresh = state(view).description.pending_capture
      reply(view, fresh.ref, "Final description!", true)
      assert has_element?(view, "#task-comment-departure")
      assert state(view).comments.draft == current
      assert has_element?(view, "#task-comment-text", "Newer comment!")
      assert state(view).comment_departure.destination == ~p"/projects/#{project.id}"
    end
  end

  test "departure waits for current correlated comment metadata and ignores duplicates", %{
    view: view,
    project: project
  } do
    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: ""
    })

    pending = state(view).description.pending_capture
    id = state(view).description.editor_id
    change(view, "Browser final!", true)

    send(
      view.pid,
      {:leaf_flushed, %{editor_id: id, ref: pending.ref, markdown: "Browser final!"}}
    )

    render(view)
    assert has_element?(view, "#task-modal-content")
    assert state(view).description.pending_capture

    render_hook(view, "capture_task_departure_comment", %{
      editor_id: "replaced",
      ref: pending.ref,
      draft: "Wrong editor"
    })

    render_hook(view, "capture_task_departure_comment", %{
      editor_id: id,
      ref: "stale",
      draft: "Wrong ref"
    })

    assert state(view).description.pending_capture

    render_hook(view, "capture_task_departure_comment", %{
      editor_id: id,
      ref: pending.ref,
      draft: "  Current browser text!  "
    })

    assert has_element?(view, "#task-comment-departure")
    assert state(view).comments.draft == "  Current browser text!  "

    render_hook(view, "capture_task_departure_comment", %{
      editor_id: id,
      ref: pending.ref,
      draft: "Duplicate old text"
    })

    assert state(view).comments.draft == "  Current browser text!  "
  end

  for initial <- ["", "Original comment"] do
    test "retry captures unsent browser comment after starting with #{inspect(initial)}", %{
      view: view,
      project: project
    } do
      render_hook(view, "request_task_departure", %{
        destination: ~p"/projects/#{project.id}",
        draft: unquote(initial)
      })

      expired = state(view).description.pending_capture
      id = state(view).description.editor_id
      send(view.pid, {:description_capture_timeout, expired.ref})
      render(view)
      view |> element("#retry-description-capture") |> render_click()
      fresh = state(view).description.pending_capture

      render_hook(view, "capture_task_departure_comment", %{
        editor_id: id,
        ref: expired.ref,
        draft: "Expired browser text"
      })

      assert state(view).comments.draft != "Expired browser text"
      current = "  Unsent browser comment!\nPreserve it.  "

      render_hook(view, "capture_task_departure_comment", %{
        editor_id: id,
        ref: fresh.ref,
        draft: current
      })

      reply(view, fresh.ref, "Captured description!", true)
      assert has_element?(view, "#task-comment-departure")
      assert state(view).comments.draft == current
      assert state(view).comment_departure.destination == ~p"/projects/#{project.id}"
    end
  end

  test "a duplicate snapshot cannot replace a later validated comment during capture", %{
    view: view,
    project: project
  } do
    render_hook(view, "request_task_departure", %{
      destination: ~p"/projects/#{project.id}",
      draft: "Initial"
    })

    pending = state(view).description.pending_capture
    id = state(view).description.editor_id
    snapshot = %{editor_id: id, ref: pending.ref, draft: "Snapshot"}
    render_hook(view, "capture_task_departure_comment", snapshot)

    view
    |> form("#task-comment-form", comment: %{text: "Later validated comment"})
    |> render_change()

    render_hook(view, "capture_task_departure_comment", snapshot)
    assert state(view).comments.draft == "Later validated comment"
    reply(view, pending.ref, "Final!", true)
    assert has_element?(view, "#task-comment-departure")
    assert state(view).comments.draft == "Later validated comment"
  end

  test "a repeated request during confirmation cannot reopen it after Go back", %{
    view: view,
    project: project
  } do
    destination = ~p"/projects/#{project.id}"

    render_hook(view, "request_task_departure", %{
      destination: destination,
      draft: "Retained comment"
    })

    original = state(view).description.pending_capture
    id = state(view).description.editor_id
    reply(view, original.ref, "Retained description!", true)
    assert has_element?(view, "#task-comment-departure")
    render_hook(view, "request_task_departure", %{destination: destination})
    assert state(view).description.pending_capture == nil
    view |> element("#task-comment-departure-go-back") |> render_click()

    render_hook(view, "capture_task_departure_comment", %{
      editor_id: id,
      ref: original.ref,
      draft: "Stale comment"
    })

    send(
      view.pid,
      {:leaf_flushed, %{editor_id: id, ref: original.ref, markdown: "Retained description!"}}
    )

    render(view)
    refute has_element?(view, "#task-comment-departure")
    assert state(view).comments.draft == "Retained comment"
    render_hook(view, "request_task_departure", %{destination: destination})
    fresh = state(view).description.pending_capture
    assert fresh.ref != original.ref
    reply(view, fresh.ref, "Retained description!", true)
    assert has_element?(view, "#task-comment-departure")
  end

  test "activity-only refresh captures final editor text before reconciling a canonical description",
       %{
         view: view,
         project: project,
         task: task
       } do
    Taskman.Repo.update!(Ecto.Changeset.change(task, description: "External description"))

    send(view.pid, %Event{
      entity: :activity,
      operation: :invalidated,
      project_id: project.id,
      entity_id: task.id,
      task_ids: [task.id],
      fields: []
    })

    render(view)
    pending = state(view).description.pending_capture
    assert pending
    reply(view, pending.ref, "Final browser description!", true)
    assert has_element?(view, "#task-description-conflict")
    assert state(view).editing.autosave.form[:description].value == "Final browser description!"
    assert Tasks.get_task_for_project(project, task.id).description == "External description"
    assert has_element?(view, "#task-comment-form")
  end

  defp state(view), do: :sys.get_state(view.pid).socket.assigns

  defp change(view, markdown, dirty) do
    send(
      view.pid,
      {:leaf_changed,
       %{editor_id: state(view).description.editor_id, markdown: markdown, dirty: dirty}}
    )

    render(view)
  end

  defp reply(view, ref, markdown, dirty) do
    render_hook(view, "capture_task_departure_comment", %{
      editor_id: state(view).description.editor_id,
      ref: ref,
      draft: state(view).comments.draft
    })

    change(view, markdown, dirty)

    send(
      view.pid,
      {:leaf_flushed,
       %{editor_id: state(view).description.editor_id, ref: ref, markdown: markdown}}
    )

    render(view)
  end
end
