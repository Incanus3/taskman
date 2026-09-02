defmodule TaskmanWeb.ProjectLiveTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "root renders the list-first empty state", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")

    assert has_element?(view, "#project-sidebar")
    assert has_element?(view, "#project-form")
    assert has_element?(view, "#main-panel[data-state='no-selection']")
  end

  test "creates a Project and selects it in the URL", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")

    view
    |> form("#project-form", project: %{name: "Taskman", primary_directory: File.cwd!()})
    |> render_submit()

    assert [project] = Taskman.Projects.list_projects()
    assert_patch(view, ~p"/projects/#{project.id}")
    assert has_element?(view, "#project-#{project.id}[aria-current='page']")
    assert has_element?(view, "#tasks")
  end

  test "unparseable Project ID keeps the sidebar usable", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, "/projects/not-an-id")

    assert has_element?(view, "#project-sidebar")
    assert has_element?(view, "#project-not-found")
    refute has_element?(view, "#add-task")

    view |> element("#select-project-#{project.id}") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}")
    assert has_element?(view, "#tasks")
  end

  test "selected Project shows only its direct Tasks", %{conn: conn} do
    selected = project_fixture(%{})
    other = project_fixture(%{})
    selected_task = task_fixture(selected, %{title: "Visible"})
    other_task = task_fixture(other, %{title: "Hidden"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{selected.id}")

    assert has_element?(view, "#task-#{selected_task.id}")
    refute has_element?(view, "#task-#{other_task.id}")
  end

  test "Project patch navigation resets Task rows by their generated stream IDs", %{conn: conn} do
    project_a = project_fixture(%{})
    task_a = task_fixture(project_a, %{title: "Project A Task"})
    project_b = project_fixture(%{})
    task_b = task_fixture(project_b, %{title: "Project B Task"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project_a.id}")

    assert has_element?(view, "#tasks > #tasks-#{task_a.id}")
    refute has_element?(view, "#tasks > #tasks-#{task_b.id}")

    view |> element("#select-project-#{project_b.id}") |> render_click()

    assert_patch(view, ~p"/projects/#{project_b.id}")
    refute has_element?(view, "#tasks > #tasks-#{task_a.id}")
    refute has_element?(view, "#task-#{task_a.id}")
    assert has_element?(view, "#tasks > #tasks-#{task_b.id}")
    assert has_element?(view, "#task-#{task_b.id}")
  end

  test "opens the canonical Task modal from the row and preserves the list", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Editable", description: "Context"})
    sibling = task_fixture(project, %{title: "Sibling"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#open-task-#{task.id}") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-modal-close[aria-label='Close dialog']")
    assert has_element?(view, "#task-form")
    assert has_element?(view, "#task-title[value='Editable']")

    assert has_element?(
             view,
             "#task-title[phx-hook='TaskmanWeb.TaskForm.TaskTitleFocus']"
           )

    assert has_element?(view, "#task-description")
    assert has_element?(view, "#task-#{sibling.id}")
    refute has_element?(view, "#close-task")
  end

  test "direct canonical Task URL renders the same modal", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Direct"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-form")
    assert has_element?(view, "#task-#{task.id}")
  end

  test "editing initializes the parent picker from the persisted parent", %{conn: conn} do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Roadmap"})
    task = task_fixture(project, %{title: "Launch"}, parent: parent)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(view, "#task-parent-picker")
    refute has_element?(view, "#task-parent-search")
    assert has_element?(view, "#task-parent-trigger", "Roadmap")
    refute has_element?(view, "#task-parent-clear")

    view |> element("#task-parent-trigger") |> render_click()

    assert has_element?(view, "#task-parent-results #task-parent-clear[role='option']")
  end

  test "selecting an edit parent persists it immediately", %{conn: conn} do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Roadmap"})
    task = task_fixture(project, %{title: "Launch"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#task-parent-trigger") |> render_click()
    view |> element("#task-parent-option-#{parent.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).parent_task_id == parent.id
    assert has_element?(view, "#task-parent-trigger", "Roadmap")
    refute has_element?(view, "#task-parent-search")
    assert has_element?(view, "#tasks > #tasks-#{task.id}")
    assert has_element?(view, "#task-detail-layout[data-has-hierarchy='true']")
    assert has_element?(view, "#task-hierarchy-node-#{parent.id}")
    assert has_element?(view, "#task-hierarchy-link-#{task.id}[aria-current='true']")
  end

  test "parent options close from the toggle and close event without changing the draft", %{
    conn: conn
  } do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Roadmap"})
    task = task_fixture(project, %{title: "Launch"}, parent: parent)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#task-parent-trigger") |> render_click()
    assert has_element?(view, "#task-parent-results")

    view |> element("button[aria-label='Close parent Task options']") |> render_click()
    refute has_element?(view, "#task-parent-results")
    assert has_element?(view, "#task-parent-trigger", "Roadmap")

    view |> element("#task-parent-trigger") |> render_click()
    assert has_element?(view, "#task-parent-results")

    render_click(view, "close_task_parent_options", %{})
    refute has_element?(view, "#task-parent-results")
    assert has_element?(view, "#task-parent-trigger", "Roadmap")
    assert Tasks.get_task_for_project(project, task.id).parent_task_id == parent.id
  end

  test "typing in the edit parent search stays out of task autosave", %{conn: conn} do
    project = project_fixture(%{})
    matching_parent = task_fixture(project, %{title: "Roadmap"})
    other_parent = task_fixture(project, %{title: "Backlog"})
    task = task_fixture(project, %{title: "Launch"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#task-parent-trigger") |> render_click()

    view
    |> element("#task-parent-search")
    |> render_change(%{"_target" => ["parent_query"], "parent_query" => "Road"})

    assert has_element?(view, "#task-parent-search[value='Road']")
    assert has_element?(view, "#task-parent-option-#{matching_parent.id}")
    refute has_element?(view, "#task-parent-option-#{other_parent.id}")
  end

  test "keyboard parent selection stays in the picker instead of submitting the task form", %{
    conn: conn
  } do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Roadmap"})
    task = task_fixture(project, %{title: "Launch"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#task-parent-trigger") |> render_click()
    view |> element("#task-parent-search") |> render_keydown(%{"key" => "ArrowDown"})

    assert has_element?(view, "#task-parent-search[aria-activedescendant='task-parent-clear']")

    view |> element("#task-parent-search") |> render_keydown(%{"key" => "ArrowDown"})

    assert has_element?(
             view,
             "#task-parent-search[aria-activedescendant='task-parent-option-#{parent.id}']"
           )

    assert has_element?(view, "#task-parent-option-#{parent.id}[data-active='true']")

    view |> element("#task-parent-search") |> render_keydown(%{"key" => "Enter"})

    assert Tasks.get_task_for_project(project, task.id).parent_task_id == parent.id
    assert has_element?(view, "#task-parent-trigger", "Roadmap")
    refute has_element?(view, "#task-parent-search")
    refute has_element?(view, "#task-parent-results")
    refute has_element?(view, "#new-project-error")
  end

  test "keyboard clearing synchronizes the visible parent query", %{
    conn: conn
  } do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Root Alpha"})
    task = task_fixture(project, %{title: "Launch"}, parent: parent)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#task-parent-trigger") |> render_click()
    view |> element("#task-parent-search") |> render_keydown(%{"key" => "ArrowDown"})
    view |> element("#task-parent-search") |> render_keydown(%{"key" => "Enter"})

    assert Tasks.get_task_for_project(project, task.id).parent_task_id == nil
    refute has_element?(view, "#task-parent-search")
    assert has_element?(view, "#task-parent-trigger", "No parent")
  end

  test "replacing an edit parent persists it immediately", %{conn: conn} do
    project = project_fixture(%{})
    first_parent = task_fixture(project, %{title: "First parent"})
    second_parent = task_fixture(project, %{title: "Second parent"})
    task = task_fixture(project, %{title: "Launch"}, parent: first_parent)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#task-parent-trigger") |> render_click()

    view
    |> element("#task-parent-search")
    |> render_change(%{
      "_target" => ["parent_query"],
      "parent_query" => "Second parent"
    })

    view |> element("#task-parent-option-#{second_parent.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).parent_task_id == second_parent.id
    assert has_element?(view, "#task-parent-trigger", "Second parent")
    assert has_element?(view, "#tasks > #tasks-#{task.id}")
  end

  test "ignores malformed parent search payloads without closing the picker", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Launch"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#task-parent-trigger") |> render_click()
    render_hook(view, "search_task_parents", %{"key" => "a"})

    assert has_element?(view, "#task-parent-search")
    assert has_element?(view, "#task-parent-results")
  end

  test "selecting an already selected edit parent is idempotent", %{conn: conn} do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Roadmap"})
    task = task_fixture(project, %{title: "Launch"}, parent: parent)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#task-parent-trigger") |> render_click()
    view |> element("#task-parent-option-#{parent.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).parent_task_id == parent.id
    assert has_element?(view, "#task-parent-trigger", "Roadmap")
    assert has_element?(view, "#tasks > #tasks-#{task.id}")
  end

  test "clearing an edit parent persists a root Task immediately", %{conn: conn} do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Roadmap"})
    task = task_fixture(project, %{title: "Launch"}, parent: parent)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#task-parent-trigger") |> render_click()
    view |> element("#task-parent-clear") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).parent_task_id == nil
    refute has_element?(view, "#task-parent-search")
    assert has_element?(view, "#task-parent-trigger", "No parent")
    assert has_element?(view, "#tasks > #tasks-#{task.id}")
  end

  test "an open parent search drops a candidate that becomes invalid elsewhere", %{
    conn: conn
  } do
    project = project_fixture(%{})
    original_parent = task_fixture(project, %{title: "Original parent"})
    rejected_parent = task_fixture(project, %{title: "Rejected parent"})
    task = task_fixture(project, %{title: "Launch"}, parent: original_parent)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view |> element("#task-parent-trigger") |> render_click()

    view
    |> element("#task-parent-search")
    |> render_change(%{
      "_target" => ["parent_query"],
      "parent_query" => "Rejected parent"
    })

    assert has_element?(view, "#task-parent-option-#{rejected_parent.id}")

    assert {:ok, _} = Tasks.update_task(project, rejected_parent, %{}, parent: task)

    _ = :sys.get_state(view.pid)

    assert has_element?(view, "#task-parent-search[value='Rejected parent']")
    refute has_element?(view, "#task-parent-option-#{rejected_parent.id}")
    assert has_element?(view, "#task-parent-option-#{original_parent.id}[aria-selected='true']")
    assert Tasks.get_task_for_project(project, task.id).parent_task_id == original_parent.id
  end

  test "closing a parent search handles a candidate invalidated elsewhere", %{conn: conn} do
    project = project_fixture(%{})
    original_parent = task_fixture(project, %{title: "Original parent"})
    rejected_parent = task_fixture(project, %{title: "Rejected parent"})
    task = task_fixture(project, %{title: "Launch"}, parent: original_parent)
    task_path = ~p"/projects/#{project.id}/tasks/#{task.id}"

    {:ok, view, _html} = live(conn, task_path)

    view |> element("#task-parent-trigger") |> render_click()

    view
    |> element("#task-parent-search")
    |> render_change(%{
      "_target" => ["parent_query"],
      "parent_query" => "Rejected parent"
    })

    assert {:ok, _} =
             Task.async(fn -> Tasks.update_task(project, rejected_parent, %{}, parent: task) end)
             |> Task.await()

    _ = :sys.get_state(view.pid)

    refute has_element?(view, "#task-parent-option-#{rejected_parent.id}")
    assert has_element?(view, "#task-parent-option-#{original_parent.id}[aria-selected='true']")

    view |> element("#task-modal-close") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}")

    view |> element("#open-task-#{task.id}") |> render_click()
    assert_patch(view, task_path)

    refute has_element?(view, "#task-parent-search")
    assert has_element?(view, "#task-parent-trigger", "Original parent")
    refute has_element?(view, "#task-parent-results")
  end

  test "found Task renders truthful detail regions without unavailable operations", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Write launch checklist"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(
             view,
             "#taskman-workspace[phx-hook='TaskmanWeb.ProjectLive.TaskDetailLayout']"
           )

    assert has_element?(view, "#task-modal-content[data-size='wide']")
    assert has_element?(view, "#task-detail-layout[data-has-hierarchy='false']")

    assert has_element?(
             view,
             "#task-hierarchy-toggle[aria-controls='task-hierarchy'][aria-expanded='false'][aria-label='Expand task hierarchy']"
           )

    assert has_element?(
             view,
             "#task-hierarchy [role='tree'] [role='treeitem'][aria-current='true']",
             "Write launch checklist"
           )

    assert has_element?(view, "#task-hierarchy-empty", "No parent or child Tasks")

    assert has_element?(
             view,
             "#task-activity-empty",
             "No activity has been recorded for this Task."
           )

    assert has_element?(
             view,
             "#task-sessions-empty",
             "No Agent Sessions are associated with this Task."
           )

    refute has_element?(view, "#task-sessions button")
    refute has_element?(view, "#task-detail-layout [data-session-row]")
  end

  test "shows the selected Task hierarchy context with only required branches expanded", %{
    conn: conn
  } do
    project = project_fixture(%{})
    root = task_fixture(project, %{title: "Root"})
    selected = task_fixture(project, %{title: "Selected"}, parent: root)
    direct_child = task_fixture(project, %{title: "Direct child"}, parent: selected)
    sibling = task_fixture(project, %{title: "Sibling"}, parent: root)
    collapsed_child = task_fixture(project, %{title: "Collapsed child"}, parent: sibling)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{selected.id}")

    assert has_element?(view, "#task-detail-layout[data-has-hierarchy='true']")
    assert has_element?(view, "#task-hierarchy-node-#{root.id}[role='treeitem'][aria-level='1']")

    assert has_element?(
             view,
             "#task-hierarchy-node-#{selected.id}[role='treeitem'][aria-level='2']"
           )

    assert has_element?(view, "#task-hierarchy-link-#{selected.id}[aria-current='true']")
    assert has_element?(view, "#task-hierarchy-node-#{direct_child.id}")
    assert has_element?(view, "#task-hierarchy-node-#{sibling.id}")
    refute has_element?(view, "#task-hierarchy-node-#{collapsed_child.id}")
    refute has_element?(view, "#task-hierarchy-disclosure-#{root.id}")
    refute has_element?(view, "#task-hierarchy-disclosure-#{selected.id}")
    assert has_element?(view, "#task-hierarchy-disclosure-#{sibling.id}[aria-expanded='false']")
  end

  test "a hierarchy disclosure progressively shows and hides its branch", %{conn: conn} do
    project = project_fixture(%{})
    root = task_fixture(project, %{})
    selected = task_fixture(project, %{}, parent: root)
    sibling = task_fixture(project, %{}, parent: root)
    child = task_fixture(project, %{}, parent: sibling)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{selected.id}")

    view |> element("#task-hierarchy-disclosure-#{sibling.id}") |> render_click()
    assert has_element?(view, "#task-hierarchy-disclosure-#{sibling.id}[aria-expanded='true']")
    assert has_element?(view, "#task-hierarchy-node-#{child.id}")

    view |> element("#task-hierarchy-disclosure-#{sibling.id}") |> render_click()
    assert has_element?(view, "#task-hierarchy-disclosure-#{sibling.id}[aria-expanded='false']")
    refute has_element?(view, "#task-hierarchy-node-#{child.id}")
  end

  test "hierarchy links preserve branch expansion within the connected tree", %{conn: conn} do
    project = project_fixture(%{})
    root = task_fixture(project, %{})
    selected = task_fixture(project, %{}, parent: root)
    direct_child = task_fixture(project, %{}, parent: selected)
    sibling = task_fixture(project, %{}, parent: root)
    sibling_child = task_fixture(project, %{}, parent: sibling)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{selected.id}")

    view |> element("#task-hierarchy-disclosure-#{sibling.id}") |> render_click()
    view |> element("#task-hierarchy-link-#{direct_child.id}") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{direct_child.id}")
    assert has_element?(view, "#task-hierarchy-link-#{direct_child.id}[aria-current='true']")
    assert has_element?(view, "#task-hierarchy-disclosure-#{sibling.id}[aria-expanded='true']")
    assert has_element?(view, "#task-hierarchy-node-#{sibling_child.id}")
  end

  test "closing the Task modal clears hierarchy branch expansion", %{conn: conn} do
    project = project_fixture(%{})
    root = task_fixture(project, %{})
    selected = task_fixture(project, %{}, parent: root)
    sibling = task_fixture(project, %{}, parent: root)
    child = task_fixture(project, %{}, parent: sibling)
    task_path = ~p"/projects/#{project.id}/tasks/#{selected.id}"

    {:ok, view, _html} = live(conn, task_path)

    view |> element("#task-hierarchy-disclosure-#{sibling.id}") |> render_click()
    assert has_element?(view, "#task-hierarchy-node-#{child.id}")

    view |> element("#task-modal-close") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}")

    view |> element("#open-task-#{selected.id}") |> render_click()
    assert_patch(view, task_path)
    assert has_element?(view, "#task-hierarchy-disclosure-#{sibling.id}[aria-expanded='false']")
    refute has_element?(view, "#task-hierarchy-node-#{child.id}")
  end

  test "loading another connected tree resets hierarchy branch expansion", %{conn: conn} do
    project = project_fixture(%{})
    root = task_fixture(project, %{})
    selected = task_fixture(project, %{}, parent: root)
    sibling = task_fixture(project, %{}, parent: root)
    child = task_fixture(project, %{}, parent: sibling)
    other_root = task_fixture(project, %{})
    other_selected = task_fixture(project, %{}, parent: other_root)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{selected.id}")

    view |> element("#task-hierarchy-disclosure-#{sibling.id}") |> render_click()
    assert has_element?(view, "#task-hierarchy-node-#{child.id}")

    view |> element("#open-task-#{other_selected.id}") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{other_selected.id}")

    view |> element("#open-task-#{selected.id}") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{selected.id}")
    assert has_element?(view, "#task-hierarchy-disclosure-#{sibling.id}[aria-expanded='false']")
    refute has_element?(view, "#task-hierarchy-node-#{child.id}")
  end

  test "hierarchy navigation preserves List and descendant-inclusion context", %{conn: conn} do
    project = project_fixture(%{})
    current_list = list_fixture(project, nil, %{name: "Current"})
    other_list = list_fixture(project, nil, %{name: "Other"})
    root = task_fixture(project, other_list, %{title: "Outside current List"})
    selected = task_fixture(project, current_list, %{title: "Inside current List"}, parent: root)

    {:ok, view, _html} =
      live(
        conn,
        ~p"/projects/#{project.id}/lists/#{current_list.id}/tasks/#{selected.id}?include_children=true"
      )

    refute has_element?(view, "#task-#{root.id}")

    view |> element("#task-hierarchy-link-#{root.id}") |> render_click()

    assert_patch(
      view,
      ~p"/projects/#{project.id}/lists/#{current_list.id}/tasks/#{root.id}?include_children=true"
    )

    assert has_element?(view, "#task-hierarchy-link-#{root.id}[aria-current='true']")
    refute has_element?(view, "#task-#{root.id}")
  end

  test "invalid Task URLs preserve the selected Project list in a modal not-found state", %{
    conn: conn
  } do
    project = project_fixture(%{})
    visible = task_fixture(project, %{title: "Visible"})
    other_project = project_fixture(%{})
    other = task_fixture(other_project, %{title: "Secret"})

    for task_id <- ["not-an-id", "999999999", Integer.to_string(other.id)] do
      {:ok, view, _html} = live(conn, "/projects/#{project.id}/tasks/#{task_id}")
      assert has_element?(view, "#task-modal-content[aria-labelledby='task-modal-title']")
      assert has_element?(view, "#task-modal-title")
      assert has_element?(view, "#task-not-found")
      assert has_element?(view, "#task-#{visible.id}")
      refute has_element?(view, "#task-form")
      refute has_element?(view, "#task-detail-layout")
      assert has_element?(view, "#task-modal-content[data-size='default']")
      refute has_element?(view, "#task-#{other.id}")
    end
  end

  test "a Task removed between detail and hierarchy lookup preserves the Task-not-found modal", %{
    conn: conn
  } do
    project = project_fixture(%{})
    visible = task_fixture(project, %{title: "Visible"})
    task = task_fixture(project, %{title: "Removed during lookup"})
    test_pid = self()
    handler_id = {__MODULE__, make_ref()}
    telemetry_guard = :atomics.new(1, [])

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, %{query: query, params: params}, _config ->
          if String.starts_with?(query, "SELECT") and
               String.contains?(query, ~s(FROM "tasks")) and
               params == [task.id, project.id] and
               :atomics.compare_exchange(telemetry_guard, 1, 0, 1) == :ok do
            Taskman.Repo.delete!(task)
            send(test_pid, :task_removed_between_detail_and_hierarchy_lookup)
          end
        end,
        nil
      )

    on_exit(fn -> :telemetry.detach(handler_id) end)

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert_receive :task_removed_between_detail_and_hierarchy_lookup
    assert has_element?(view, "#task-modal-content[aria-labelledby='task-modal-title']")
    assert has_element?(view, "#task-not-found")
    assert has_element?(view, "#task-#{visible.id}")
    refute has_element?(view, "#task-form")
    refute has_element?(view, "#task-detail-layout")
    assert has_element?(view, "#task-modal-content[data-size='default']")
  end

  test "autosaves the targeted Task field and refreshes the streamed row", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending, priority: :none})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "After"})
    |> render_change(%{"_target" => ["task", "title"]})

    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, task.id).title == "After"
    assert has_element?(view, "#task-#{task.id}", "After")

    view
    |> form("#task-form", task: %{status: "in_review"})
    |> render_change(%{"_target" => ["task", "status"]})

    assert Tasks.get_task_for_project(project, task.id).status == :in_review
    assert has_element?(view, "#task-status-#{task.id}", "In Review")
  end

  test "immediately persists priority and refreshes the streamed row", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{priority: :none})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{priority: "urgent"})
    |> render_change(%{"_target" => ["task", "priority"]})

    assert Tasks.get_task_for_project(project, task.id).priority == :urgent
    assert has_element?(view, "#task-priority-#{task.id}", "Urgent")
    assert has_element?(view, "#task-priority option[selected][value='urgent']")
  end

  test "immediately persists and clears the due date-time", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{due_at: nil})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{due_at: "2026-08-03T16:00"})
    |> render_change(%{"_target" => ["task", "due_at"]})

    assert Tasks.get_task_for_project(project, task.id).due_at == ~N[2026-08-03 16:00:00]
    assert has_element?(view, "#task-due-at[value='2026-08-03T16:00']")

    view
    |> form("#task-form", task: %{due_at: ""})
    |> render_change(%{"_target" => ["task", "due_at"]})

    assert Tasks.get_task_for_project(project, task.id).due_at == nil
    assert has_element?(view, "#task-due-at[value='']")
  end

  test "an invalid draft does not block another field from saving", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Valid", status: :pending})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: ""})
    |> render_change(%{"_target" => ["task", "title"]})

    assert has_element?(view, "#task-form [data-role='field-error']")
    assert Tasks.get_task_for_project(project, task.id).title == "Valid"

    view
    |> form("#task-form", task: %{status: "in_review"})
    |> render_change(%{"_target" => ["task", "status"]})

    updated = Tasks.get_task_for_project(project, task.id)
    assert updated.title == "Valid"
    assert updated.status == :in_review
    assert has_element?(view, "#task-save-status[data-state='not_saved']")
  end

  test "populated Task list renders column headers before its Task stream", %{conn: conn} do
    project = project_fixture(%{})
    _task = task_fixture(project, %{})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(view, "#task-table-header + #tasks")
    assert has_element?(view, "#task-actions-header", "Actions")
  end

  test "invalid Project input renders inline errors", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")

    view
    |> form("#project-form", project: %{name: "Taskman", primary_directory: "/not/a/taskman/dir"})
    |> render_submit()

    assert has_element?(view, "#project-form [data-role='field-error']")
  end

  test "opens and cancels the new Task modal over the selected list", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Existing"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#add-task") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}/tasks/new")
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-#{task.id}")

    view |> element("#cancel-task") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#task-modal")
  end

  test "normal New Task starts without a parent at the browsed Project location", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-create-location", "Project #{project.name}")
    assert has_element?(view, "#task-parent-picker")
    refute has_element?(view, "#task-parent-search")
    assert has_element?(view, "#task-parent-trigger", "No parent")
  end

  test "a Project-wide selected parent persists without changing the browsed location", %{
    conn: conn
  } do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    parent = task_fixture(project, planning, %{title: "Plan release"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    view |> element("#task-parent-trigger") |> render_click()
    assert has_element?(view, "#task-parent-option-#{parent.id}")

    view |> element("#task-parent-option-#{parent.id}") |> render_click()
    assert has_element?(view, "#task-parent-trigger", "Plan release")

    view
    |> form("#task-form", task: %{title: "Project-root child"})
    |> render_submit()

    assert_patch(view, ~p"/projects/#{project.id}")

    created =
      Enum.find(Tasks.list_tasks_for_project(project), &(&1.title == "Project-root child"))

    assert created.parent_task_id == parent.id
    assert created.list_id == nil
    assert has_element?(view, "#task-#{created.id}")
  end

  test "stale or foreign preselected parent query values stay recoverable without identity leakage",
       %{
         conn: conn
       } do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    foreign_parent = task_fixture(other_project, %{title: "Foreign parent"})

    for parent_task_id <- ["not-an-id", "999999999", Integer.to_string(foreign_parent.id)] do
      {:ok, view, _html} =
        live(
          conn,
          "/projects/#{project.id}/tasks/new?parent_task_id=#{parent_task_id}"
        )

      assert has_element?(view, "#task-modal")
      assert has_element?(view, "#task-form")

      assert has_element?(
               view,
               "#task-parent-trigger[aria-invalid='true'][aria-describedby='task-parent-error']"
             )

      assert has_element?(view, "#task-parent-error", "That parent Task is no longer available.")
      assert has_element?(view, "#task-parent-trigger", "No parent")
      refute has_element?(view, "#task-parent-option-#{foreign_parent.id}")
    end
  end

  test "a foreign preselected parent query recovers through no-parent creation", %{conn: conn} do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    foreign_parent = task_fixture(other_project, %{title: "Foreign parent"})

    {:ok, view, _html} =
      live(
        conn,
        "/projects/#{project.id}/tasks/new?parent_task_id=#{foreign_parent.id}"
      )

    assert has_element?(view, "#task-parent-error", "That parent Task is no longer available.")
    assert has_element?(view, "#task-parent-trigger", "No parent")

    view
    |> form("#task-form", task: %{title: "Recovered root Task"})
    |> render_submit()

    assert_patch(view, ~p"/projects/#{project.id}")

    created =
      Enum.find(Tasks.list_tasks_for_project(project), &(&1.title == "Recovered root Task"))

    assert created.parent_task_id == nil
    assert created.list_id == nil
    assert has_element?(view, "#task-#{created.id}")
  end

  test "a stale selected parent keeps its draft and reports a recoverable error on creation", %{
    conn: conn
  } do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Removed parent"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    view |> element("#task-parent-trigger") |> render_click()
    view |> element("#task-parent-option-#{parent.id}") |> render_click()

    Taskman.Repo.delete!(parent)

    view
    |> form("#task-form", task: %{title: "Child with stale parent"})
    |> render_submit()

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-parent-trigger", "Removed parent")
    assert has_element?(view, "#task-parent-trigger[aria-invalid='true']")
    assert has_element?(view, "#task-parent-error", "That parent Task is no longer available.")
    refute has_element?(view, "#task-parent-results")

    refute Enum.any?(
             Tasks.list_tasks_for_project(project),
             &(&1.title == "Child with stale parent")
           )
  end

  test "canceling a valid new Task draft does not persist it", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    task_params = %{
      title: "Draft only",
      description: "Keep this draft client-side",
      status: "in_review",
      priority: "high",
      due_at: "2026-08-04T09:30"
    }

    view
    |> form("#task-form", task: task_params)
    |> render_change()

    assert Tasks.list_tasks_for_project(project) == []

    view |> element("#cancel-task") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#task-modal")
    assert Tasks.list_tasks_for_project(project) == []
  end

  test "new Task form exposes every editable field and gates creation on validity", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    assert has_element?(view, "#task-title")
    assert has_element?(view, "#task-description")
    assert has_element?(view, "#task-status option[selected][value='pending']")
    assert has_element?(view, "#task-priority option[selected][value='none']")
    assert has_element?(view, "#task-due-at[value='']")
    assert has_element?(view, "#create-task[disabled]")

    valid_params = %{
      title: "Ready to create",
      description: "",
      status: "pending",
      priority: "none",
      due_at: ""
    }

    view
    |> form("#task-form", task: valid_params)
    |> render_change()

    refute has_element?(view, "#create-task[disabled]")

    view
    |> form("#task-form", task: Map.put(valid_params, :title, ""))
    |> render_change()

    assert has_element?(view, "#create-task[disabled]")
    assert has_element?(view, "#task-form [data-role='field-error']")
  end

  test "creates every Task field explicitly and closes the modal", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    task_params = %{
      title: "Ship complete creation",
      description: "All details supplied up front",
      status: "in_progress",
      priority: "urgent",
      due_at: "2026-08-03T16:00"
    }

    view
    |> form("#task-form", task: task_params)
    |> render_change()

    refute has_element?(view, "#create-task[disabled]")

    view
    |> form("#task-form", task: task_params)
    |> render_submit()

    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#task-modal")

    [task] = Tasks.list_tasks_for_project(project)
    assert task.title == "Ship complete creation"
    assert task.description == "All details supplied up front"
    assert task.status == :in_progress
    assert task.priority == :urgent
    assert task.due_at == ~N[2026-08-03 16:00:00]
    assert has_element?(view, "#task-#{task.id}")
    assert has_element?(view, "#task-status-#{task.id}", "In Progress")
    assert has_element?(view, "#task-priority-#{task.id}", "Urgent")
    assert has_element?(view, "#tasks-empty.hidden.only\\:block")
  end

  test "direct invalid submission preserves the complete draft", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    invalid_params = %{
      title: "",
      description: "Keep this draft",
      status: "in_review",
      priority: "high",
      due_at: "2026-08-04T09:30"
    }

    view
    |> form("#task-form", task: invalid_params)
    |> render_submit()

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#create-task[disabled]")
    assert has_element?(view, "#task-form [data-role='field-error']")
    assert has_element?(view, "#task-description", "Keep this draft")
    assert has_element?(view, "#task-status option[selected][value='in_review']")
    assert has_element?(view, "#task-priority option[selected][value='high']")
    assert has_element?(view, "#task-due-at[value='2026-08-04T09:30']")
    assert Tasks.list_tasks_for_project(project) == []
  end

  test "invalid ordinary fields retain the selected parent and captured creation location", %{
    conn: conn
  } do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    parent = task_fixture(project, planning, %{title: "Parent in Planning"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    view |> element("#task-parent-trigger") |> render_click()
    view |> element("#task-parent-option-#{parent.id}") |> render_click()

    view
    |> form("#task-form", task: %{title: "", description: "Preserve this draft"})
    |> render_submit()

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-create-location", "Project #{project.name}")
    assert has_element?(view, "#task-parent-trigger", "Parent in Planning")
    refute has_element?(view, "#task-parent-search")
    assert has_element?(view, "#task-form [data-role='field-error']")
    assert has_element?(view, "#task-description", "Preserve this draft")
    assert Tasks.list_tasks_for_project(project) == [parent]
  end
end
