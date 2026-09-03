defmodule TaskmanWeb.ProjectLive.TaskUpdatesTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "maintains one live Project task-topic subscription across selection transitions", %{
    conn: conn
  } do
    project_a = project_fixture(%{})
    project_b = project_fixture(%{})
    task_a = task_fixture(project_a, %{title: "Project A before"})
    task_b = task_fixture(project_b, %{title: "Project B before"})
    topic_a = project_task_topic(project_a)
    topic_b = project_task_topic(project_b)

    {:ok, view, _html} = live(conn, ~p"/")
    assert subscription_count(view, topic_a) == 0
    assert subscription_count(view, topic_b) == 0

    render_patch(view, ~p"/projects/#{project_a.id}")
    assert subscription_count(view, topic_a) == 1
    assert subscription_count(view, topic_b) == 0

    render_patch(view, ~p"/projects/#{project_a.id}?include_children=true")
    assert subscription_count(view, topic_a) == 1

    {{:ok, updated_a}, event_a} =
      assert_single_task_event_delivery(view, fn ->
        externally(fn -> Tasks.update_task(project_a, task_a, %{title: "Project A after"}) end)
      end)

    assert %Event{
             entity: :task,
             operation: :updated,
             project_id: project_a_id,
             entity_id: task_a_id,
             fields: [:title]
           } = event_a

    assert project_a_id == project_a.id
    assert task_a_id == task_a.id

    sync_view(view)
    assert has_element?(view, "#task-#{updated_a.id}", "Project A after")

    render_patch(view, ~p"/projects/#{project_b.id}")
    assert subscription_count(view, topic_a) == 0
    assert subscription_count(view, topic_b) == 1

    assert {:ok, _updated_a} =
             refute_task_event_delivery(view, fn ->
               externally(fn ->
                 Tasks.update_task(project_a, updated_a, %{title: "Project A ignored"})
               end)
             end)

    {{:ok, updated_b}, event_b} =
      assert_single_task_event_delivery(view, fn ->
        externally(fn -> Tasks.update_task(project_b, task_b, %{title: "Project B after"}) end)
      end)

    assert %Event{
             entity: :task,
             operation: :updated,
             project_id: project_b_id,
             entity_id: task_b_id,
             fields: [:title]
           } = event_b

    assert project_b_id == project_b.id
    assert task_b_id == task_b.id

    sync_view(view)
    assert has_element?(view, "#task-#{updated_b.id}", "Project B after")

    render_patch(view, ~p"/")
    assert subscription_count(view, topic_a) == 0
    assert subscription_count(view, topic_b) == 0

    assert {:ok, _updated_b} =
             refute_task_event_delivery(view, fn ->
               externally(fn ->
                 Tasks.update_task(project_b, updated_b, %{title: "Project B ignored"})
               end)
             end)
  end

  test "ignores foreign or malformed Task events", %{conn: conn} do
    project_a = project_fixture(%{})
    project_b = project_fixture(%{})
    task_a = task_fixture(project_a, %{title: "Project A task"})
    task_b = task_fixture(project_b, %{title: "Project B task"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project_b.id}")

    send(view.pid, task_event(project_a.id, task_a.id, :updated, [:title]))
    sync_view(view)
    assert view_assigns(view).selected_project.id == project_b.id
    assert has_element?(view, "#task-#{task_b.id}")

    send(view.pid, %Event{
      entity: :task,
      operation: :updated,
      project_id: "bad",
      entity_id: 0,
      fields: [:title]
    })

    sync_view(view)
    assert view_assigns(view).selected_project.id == project_b.id
    assert has_element?(view, "#task-#{task_b.id}")
  end

  test "rebuilds the selected descendant Task projection from canonical persistence", %{
    conn: conn
  } do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    launch = list_fixture(project, planning, %{name: "Launch"})
    archive = list_fixture(project, nil, %{name: "Archive"})
    pending = task_fixture(project, launch, %{title: "Zulu", status: :pending, priority: :low})
    done = task_fixture(project, launch, %{title: "Bravo", status: :done, priority: :medium})
    hidden = task_fixture(project, launch, %{title: "Hidden", status: :will_not_do})
    source = task_fixture(project, archive, %{title: "Move in", status: :done})

    {:ok, view, _html} =
      live(conn, ~p"/projects/#{project.id}/lists/#{planning.id}?include_children=true")

    assert has_element?(view, "#task-#{pending.id}")
    assert has_element?(view, "#task-#{done.id}")
    refute has_element?(view, "#task-#{hidden.id}")

    view |> element("#task-status-filter-button") |> render_click()

    view
    |> form("#task-status-filter-form", status_filter: %{statuses: ["done"]})
    |> render_change()

    refute has_element?(view, "#task-#{pending.id}")
    assert has_element?(view, "#task-#{done.id}")
    assert has_element?(view, "#tasks > #tasks-#{done.id}")

    assert {:ok, visible_after_status} =
             externally(fn -> Tasks.update_task(project, pending, %{status: :done}) end)

    sync_view(view)
    assert has_element?(view, "#task-#{visible_after_status.id}")

    assert {:ok, visible_hidden} =
             externally(fn -> Tasks.update_task(project, hidden, %{status: :done}) end)

    sync_view(view)
    assert has_element?(view, "#task-#{visible_hidden.id}")

    view |> element("#sort-task-priority") |> render_click()
    assert has_element?(view, "#task-priority-header[aria-sort='descending']")

    assert {:ok, promoted} =
             externally(fn ->
               Tasks.update_task(project, visible_after_status, %{priority: :urgent})
             end)

    sync_view(view)
    assert has_element?(view, "#tasks > #tasks-#{promoted.id} + #tasks-#{done.id}")

    assert {:ok, moved_in} = externally(fn -> Tasks.move_task(project, source, launch) end)
    sync_view(view)
    assert has_element?(view, "#task-#{moved_in.id}")

    assert {:ok, _moved_out} = externally(fn -> Tasks.move_task(project, promoted, archive) end)
    sync_view(view)
    refute has_element?(view, "#task-#{promoted.id}")

    assert {:ok, _filtered_out} =
             externally(fn -> Tasks.update_task(project, done, %{status: :will_not_do}) end)

    assert {:ok, _moved_in_filtered_out} =
             externally(fn -> Tasks.update_task(project, moved_in, %{status: :will_not_do}) end)

    assert {:ok, _hidden_filtered_out} =
             externally(fn ->
               Tasks.update_task(project, visible_hidden, %{status: :will_not_do})
             end)

    sync_view(view)
    assert has_element?(view, "#tasks-empty", "No tasks match the selected statuses")
  end

  test "reconciles an open Task detail and hierarchy without navigating", %{conn: conn} do
    project = project_fixture(%{})
    first_parent = task_fixture(project, %{title: "First parent"})
    second_parent = task_fixture(project, %{title: "Second parent"})

    task =
      task_fixture(project, %{title: "Before", description: "Old", status: :pending},
        parent: first_parent
      )

    task_path = ~p"/projects/#{project.id}/tasks/#{task.id}"
    {:ok, view, _html} = live(conn, task_path)

    assert {:ok, external_description} =
             externally(fn -> Tasks.update_task(project, task, %{description: "External"}) end)

    sync_view(view)
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-description", "External")
    refute_patched(view, task_path)

    view
    |> form("#task-form", task: %{title: "Mine", description: external_description.description})
    |> render_change(%{"_target" => ["task", "title"]})

    assert {:ok, external_parent} =
             externally(fn ->
               Tasks.update_task(project, external_description, %{}, parent: second_parent)
             end)

    sync_view(view)
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-title[value='Mine']")
    assert has_element?(view, "#task-parent-trigger", "Second parent")
    assert has_element?(view, "#task-hierarchy-node-#{second_parent.id}")
    assert has_element?(view, "#task-hierarchy-link-#{external_parent.id}[aria-current='true']")
    refute_patched(view, task_path)
  end

  test "keeps an open detail and create draft while matching Task events refresh the table", %{
    conn: conn
  } do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    parent = task_fixture(project, planning, %{title: "Parent"})
    selected = task_fixture(project, planning, %{title: "Selected"})
    detail_path = ~p"/projects/#{project.id}/lists/#{planning.id}/tasks/#{selected.id}"

    {:ok, detail, _html} = live(conn, detail_path)

    assert {:ok, _moved} = externally(fn -> Tasks.move_task(project, selected, nil) end)
    sync_view(detail)

    assert has_element?(detail, "#task-modal")
    assert has_element?(detail, "#task-title[value='Selected']")
    refute has_element?(detail, "#task-#{selected.id}")
    refute_patched(detail, detail_path)

    create_path =
      ~p"/projects/#{project.id}/lists/#{planning.id}/tasks/new?parent_task_id=#{parent.id}"

    {:ok, create, _html} = live(conn, create_path)

    create
    |> form("#task-form", task: %{title: "Draft"})
    |> render_change(%{"_target" => ["task", "title"]})

    created = externally(fn -> Tasks.create_task(project, planning, %{title: "External"}) end)
    assert {:ok, external_task} = created
    sync_view(create)

    assert has_element?(create, "#task-modal")
    assert has_element?(create, "#task-title[value='Draft']")
    assert has_element?(create, "#task-parent-trigger", "Parent")
    assert has_element?(create, "#task-create-location", "Planning")
    assert has_element?(create, "#task-title[phx-hook='TaskmanWeb.Tasks.Form.TaskTitleFocus']")
    assert has_element?(create, "#task-#{external_task.id}")
    refute_patched(create, create_path)
  end

  test "refreshes an active move destination without clearing its valid selection", %{conn: conn} do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{planning.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()

    assert {:ok, _updated} =
             externally(fn -> Tasks.update_task(project, task, %{title: "Changed elsewhere"}) end)

    sync_view(view)
    assert has_element?(view, "#move-task-#{task.id}")

    assert has_element?(
             view,
             "#move-task-search-#{task.id}[value='Planning'][aria-expanded='true']"
           )

    assert has_element?(view, "#move-task-option-list-#{planning.id}[aria-selected='true']")
  end

  test "refreshes the open hierarchy while retaining an expanded non-required branch", %{
    conn: conn
  } do
    project = project_fixture(%{})
    root = task_fixture(project, %{title: "Root"})
    selected = task_fixture(project, %{title: "Selected"}, parent: root)
    sibling = task_fixture(project, %{title: "Sibling"}, parent: root)
    _existing_child = task_fixture(project, %{title: "Existing child"}, parent: sibling)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{selected.id}")

    view |> element("#task-hierarchy-disclosure-#{sibling.id}") |> render_click()
    assert has_element?(view, "#task-hierarchy-disclosure-#{sibling.id}[aria-expanded='true']")

    assert {:ok, created} =
             externally(fn ->
               Tasks.create_task(project, nil, %{title: "Created child"}, parent: sibling)
             end)

    sync_view(view)
    assert has_element?(view, "#task-hierarchy-disclosure-#{sibling.id}[aria-expanded='true']")
    assert has_element?(view, "#task-hierarchy-node-#{created.id}")
  end

  test "duplicate and out-of-order Task events converge on the current persisted Task", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert {:ok, first} =
             externally(fn -> Tasks.update_task(project, task, %{title: "First"}) end)

    assert {:ok, latest} =
             externally(fn -> Tasks.update_task(project, first, %{title: "Latest"}) end)

    send(view.pid, task_event(project.id, first.id, :updated, [:title]))
    send(view.pid, task_event(project.id, first.id, :updated, [:title]))
    sync_view(view)

    assert latest.title == "Latest"
    assert has_element?(view, "#task-#{task.id}", "Latest")
    refute has_element?(view, "#task-#{task.id}", "First")
  end

  defp externally(fun) do
    fun
    |> Task.async()
    |> Task.await()
  end

  defp task_event(project_id, task_id, operation, fields) do
    %Event{
      entity: :task,
      operation: operation,
      project_id: project_id,
      entity_id: task_id,
      lock_version: nil,
      fields: fields
    }
  end

  defp project_task_topic(project), do: "projects:#{project.id}:tasks"

  defp subscription_count(view, topic) do
    Taskman.PubSub
    |> Registry.lookup(topic)
    |> Enum.count(fn {subscriber, _metadata} -> subscriber == view.pid end)
  end

  defp assert_single_task_event_delivery(view, fun) do
    view_pid = view.pid
    :erlang.trace(view_pid, true, [:receive])

    try do
      result = fun.()
      assert_receive {:trace, ^view_pid, :receive, %Event{} = event}
      refute_receive {:trace, ^view_pid, :receive, ^event}, 50
      {result, event}
    after
      :erlang.trace(view_pid, false, [:receive])
    end
  end

  defp refute_task_event_delivery(view, fun) do
    view_pid = view.pid
    :erlang.trace(view_pid, true, [:receive])

    try do
      result = fun.()
      refute_receive {:trace, ^view_pid, :receive, %Event{}}, 50
      result
    after
      :erlang.trace(view_pid, false, [:receive])
    end
  end

  defp sync_view(view), do: _ = :sys.get_state(view.pid)

  defp view_assigns(view) do
    %{socket: socket} = :sys.get_state(view.pid)
    socket.assigns
  end
end
