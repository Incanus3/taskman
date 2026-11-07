defmodule TaskmanWeb.ProjectLive.DescriptionEditorTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import TaskmanWeb.DescriptionEditorHelpers
  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks

  setup %{conn: conn} do
    previous = Application.get_env(:taskman, :task_autosave_delay_ms)
    Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)

    on_exit(fn ->
      if previous,
        do: Application.put_env(:taskman, :task_autosave_delay_ms, previous),
        else: Application.delete_env(:taskman, :task_autosave_delay_ms)
    end)

    %{conn: log_in_user(conn, user_fixture())}
  end

  test "creation submits the current Markdown description", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    render_captured_change(view, "validate_task", %{
      "task" => %{"title" => "", "priority" => "high"},
      "location" => "list:999999"
    })

    change(view, "# Notes\n\n**Ready**")
    %{socket: socket} = :sys.get_state(view.pid)
    assert socket.assigns.creation.form[:description].value == "# Notes\n\n**Ready**"
    assert has_element?(view, "#task-title[value='']")
    assert has_element?(view, "#task-location-error")
    assert Tasks.list_tasks_for_project(project) == []
    # The hook updates this hidden input synchronously in a browser. LiveViewTest
    # does not execute hooks, so supply the browser's form value explicitly.
    view
    |> form("#task-form", task: %{title: "Ship"}, location: "project")
    |> render_captured_change(%{"task" => %{"description" => "# Notes\n\n**Ready**"}})

    view
    |> form("#task-form")
    |> render_captured_submit(%{"task" => %{"description" => "# Notes\n\n**Ready**"}})

    [task] = Tasks.list_tasks_for_project(project)
    assert task.description == "# Notes\n\n**Ready**"
    assert task.priority == :high
  end

  test "description edits use field-targeted autosave", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Original"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: ""})
    |> render_captured_change(%{"_target" => ["task", "title"]})

    change(view, "**Notes**")
    assert has_element?(view, "#task-title[value='']")
    assert has_element?(view, "#task-description-save-status[data-state='saving']")
    save_description(view, task)
    assert Tasks.get_task_for_project(project, task.id).description == "**Notes**"
    assert Tasks.get_task_for_project(project, task.id).title == "Original"
    assert has_element?(view, "#task-description-save-status[data-state='saved']")
    assert has_element?(view, "#task-title[value='']")
  end

  test "empty description remains an empty string", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{description: "Before"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    change(view, "")
    save_description(view, task)
    assert Tasks.get_task_for_project(project, task.id).description == ""
  end

  test "old editor messages cannot change a new Task", %{conn: conn} do
    project = project_fixture(%{})
    first = task_fixture(project, %{description: "First"})
    second = task_fixture(project, %{description: "Second"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{first.id}")
    id = editor_id(view)
    render_captured_patch(view, ~p"/projects/#{project.id}/tasks/#{second.id}")
    send(view.pid, {:leaf_changed, %{editor_id: id, markdown: "stale", dirty: true}})
    _ = :sys.get_state(view.pid)
    render_captured(view)
    assert editor_id(view) != id
    assert Tasks.get_task_for_project(project, second.id).description == "Second"
    refute has_element?(view, "#task-description-save-status[data-state='saving']")
  end

  test "clean editor captures preserve the exact stored Markdown", %{conn: conn} do
    project = project_fixture(%{})
    source = "# Notes\n\n-   item\n"
    task = task_fixture(project, %{description: source})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    change(view, "# Notes\n\n- item", false)

    view
    |> form("#task-form", task: %{title: "Other"})
    |> render_captured_change(%{"_target" => ["task", "title"]})

    view |> form("#task-form") |> render_captured_submit()
    assert Tasks.get_task_for_project(project, task.id).description == source
  end

  test "unrelated updates retain the editor draft and initial mount content", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{description: "Original"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    id = editor_id(view)
    change(view, "**Draft**")

    view
    |> form("#task-form", task: %{title: "Changed"})
    |> render_captured_change(%{"_target" => ["task", "title"]})

    assert has_element?(view, "##{id}[data-initial-markdown='Original']")
    save_description(view, task)
    assert Tasks.get_task_for_project(project, task.id).description == "**Draft**"
    assert editor_id(view) == id
    refute_push_event(view, "leaf-command:" <> editor, %{action: "set_content"})
  end

  test "reopening the same Task rejects its previous editor", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{description: "Stored"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    old_id = editor_id(view)
    render_captured_patch(view, ~p"/projects/#{project.id}")
    render_captured_patch(view, ~p"/projects/#{project.id}/tasks/#{task.id}")
    assert editor_id(view) != old_id
    send(view.pid, {:leaf_changed, %{editor_id: old_id, markdown: "Late", dirty: true}})
    render_captured(view)
    assert Tasks.get_task_for_project(project, task.id).description == "Stored"
    refute has_element?(view, "#task-description-save-status[data-state='saving']")
  end

  test "successive creation drafts reject the previous editor", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/new")
    old_id = editor_id(view)
    change(view, "Discarded")
    render_captured_patch(view, ~p"/projects/#{project.id}")
    render_captured_patch(view, ~p"/projects/#{project.id}/tasks/new")
    assert editor_id(view) != old_id
    send(view.pid, {:leaf_changed, %{editor_id: old_id, markdown: "Late", dirty: true}})
    render_captured(view)
    %{socket: socket} = :sys.get_state(view.pid)
    assert socket.assigns.creation.form[:description].value == ""
    assert Tasks.list_tasks_for_project(project) == []
  end

  test "description conflict choices preserve mine and explicitly replace with latest", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{description: "Original"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    change(view, "**Mine**")

    {:ok, external} =
      Tasks.update_task(project, task, %{description: "**Latest**"},
        actor: Taskman.AccountsFixtures.user_fixture()
      )

    render_captured(view)
    assert has_element?(view, "#task-description-conflict")
    assert Tasks.get_task_for_project(project, task.id).description == "**Latest**"
    original_id = editor_id(view)
    view |> element("#use-latest-description") |> render_captured_click()
    replaced_id = editor_id(view)
    assert replaced_id != original_id
    assert editor_initial_value(view) == "**Latest**"

    send(
      view.pid,
      {:leaf_changed, %{editor_id: original_id, markdown: "Stale original", dirty: true}}
    )

    render_captured(view)
    assert Tasks.get_task_for_project(project, task.id).description == "**Latest**"
    change(view, "**Latest**\n", false)
    view |> form("#task-form") |> render_captured_submit()
    assert Tasks.get_task_for_project(project, task.id).description == "**Latest**"
    change(view, "**Keep me**")

    {:ok, _external} =
      Tasks.update_task(project, external, %{description: "Other"},
        actor: Taskman.AccountsFixtures.user_fixture()
      )

    render_captured(view)
    assert has_element?(view, "#task-description-conflict")
    view |> element("#keep-mine-description") |> render_captured_click()
    assert editor_id(view) == replaced_id
    assert Tasks.get_task_for_project(project, task.id).description == "**Keep me**"
    refute has_element?(view, "#task-description-conflict")
    refute_push_event(view, "leaf-command:" <> editor, %{action: "set_content"})
  end

  test "initial Markdown is sanitized for editing without rewriting storage", %{conn: conn} do
    project = project_fixture(%{})
    source = "<script>alert(1)</script><img src=x onerror=alert(2)>[bad](javascript:alert(3))"
    task = task_fixture(project, %{description: source})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    refute has_element?(view, "[data-editor-visual] script")
    refute has_element?(view, "[data-editor-visual] [onerror]")
    refute has_element?(view, "[data-editor-visual] a[href^='javascript:']")
    assert Tasks.get_task_for_project(project, task.id).description == source
  end

  test "undo back to the initial description saves after a real edit", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{description: "Original"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    change(view, "Changed")
    save_description(view, task)
    assert Tasks.get_task_for_project(project, task.id).description == "Changed"
    change(view, "Original", false)
    %{socket: socket} = :sys.get_state(view.pid)
    revision = socket.assigns.editing.autosave.revisions["description"]
    send(view.pid, {:autosave_task_field, task.id, "description", revision})
    render_captured(view)
    assert Tasks.get_task_for_project(project, task.id).description == "Original"
  end

  test "clean canonical replacement mounts exact source and rejects the old editor", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{description: "Original"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")
    original_id = editor_id(view)
    source = "# Notes\n\n-   item\n"

    {:ok, _external} =
      Tasks.update_task(project, task, %{description: source},
        actor: Taskman.AccountsFixtures.user_fixture()
      )

    render_captured(view)
    assert editor_id(view) != original_id
    assert editor_initial_value(view) == source

    send(
      view.pid,
      {:leaf_changed, %{editor_id: original_id, markdown: "Late original", dirty: true}}
    )

    render_captured(view)
    view |> form("#task-form") |> render_captured_submit()
    assert Tasks.get_task_for_project(project, task.id).description == source
    refute has_element?(view, "#task-description-save-status[data-state='saving']")
  end

  test "replacement invalidates captured input tied to the old editor" do
    alias TaskmanWeb.ProjectLive.Tasks.Description

    state = %Description.State{
      generation: 3,
      editor_id: "old-editor",
      context: {:edit, 1, 2},
      initial_value: "Original",
      edited?: true,
      pending_capture: %{ref: "old-capture"}
    }

    socket = %Phoenix.LiveView.Socket{assigns: %{__changed__: %{}, description: state}}
    replaced = Description.replace(socket, "Latest").assigns.description
    assert replaced.editor_id != state.editor_id
    assert replaced.generation > state.generation
    assert replaced.context == state.context
    assert replaced.initial_value == "Latest"
    refute replaced.edited?
    assert replaced.pending_capture == nil
  end

  defp editor_initial_value(view) do
    view
    |> render()
    |> LazyHTML.from_fragment()
    |> LazyHTML.query("[data-editor-id]")
    |> LazyHTML.attribute("data-initial-markdown")
    |> hd()
  end

  defp change(view, markdown, dirty \\ true) do
    view
    |> with_target("[data-description-editor] [phx-hook='Leaf']")
    |> render_captured_hook("content_changed", %{
      "markdown" => markdown,
      "html" => "<p>Client HTML is ignored</p>",
      "dirty" => dirty
    })

    _ = :sys.get_state(view.pid)
    render_captured(view)
  end

  defp editor_id(view) do
    view
    |> render()
    |> LazyHTML.from_fragment()
    |> LazyHTML.query("[data-editor-id]")
    |> LazyHTML.attribute("id")
    |> hd()
  end

  defp save_description(view, task) do
    send(view.pid, {:autosave_task_field, task.id, "description", 1})
    _ = :sys.get_state(view.pid)
    render_captured(view)
  end
end
