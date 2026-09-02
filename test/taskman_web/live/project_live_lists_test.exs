defmodule TaskmanWeb.ProjectLiveListsTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Lists

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "direct List route renders only direct Tasks for the selected List", %{conn: conn} do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, list, %{name: "Launch"})
    direct_task = task_fixture(project, list, %{title: "Direct task"})
    nested_task = task_fixture(project, child, %{title: "Nested task"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{list.id}")

    assert has_element?(view, "#location-heading", "Planning")
    assert has_element?(view, "#task-#{direct_task.id}")
    refute has_element?(view, "#task-#{nested_task.id}")

    assert has_element?(
             view,
             "#include-child-lists[aria-pressed='false'][data-state='off'] #include-child-lists-thumb.translate-x-0"
           )
  end

  test "List breadcrumbs are the main heading and Project directories stay out of main headers",
       %{
         conn: conn
       } do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})

    {:ok, list_view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{child.id}")

    assert has_element?(
             list_view,
             "#location-heading > #location-path",
             "Planning / Launch"
           )

    assert has_element?(list_view, "#location-path [aria-current='page']", "Launch")

    refute has_element?(
             list_view,
             "#main-panel section > header",
             project.primary_directory
           )

    {:ok, project_view, _html} = live(conn, ~p"/projects/#{project.id}")

    refute has_element?(
             project_view,
             "#main-panel section > header",
             project.primary_directory
           )
  end

  test "List descendant inclusion is URL-backed and toggles back to direct Tasks", %{conn: conn} do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, list, %{name: "Launch"})
    _direct_task = task_fixture(project, list, %{title: "Direct task"})
    nested_task = task_fixture(project, child, %{title: "Nested task"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{list.id}")

    view |> element("#include-child-lists") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/lists/#{list.id}?include_children=true")

    assert has_element?(
             view,
             "#include-child-lists[aria-pressed='true'][data-state='on'] #include-child-lists-thumb.translate-x-4"
           )

    assert has_element?(view, "#task-#{nested_task.id}")

    view |> element("#include-child-lists") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/lists/#{list.id}")

    assert has_element?(
             view,
             "#include-child-lists[aria-pressed='false'][data-state='off'] #include-child-lists-thumb.translate-x-0"
           )

    refute has_element?(view, "#task-#{nested_task.id}")
  end

  test "Project descendant inclusion includes direct Project and List Tasks", %{conn: conn} do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    direct_task = task_fixture(project, %{title: "Direct Project task"})
    list_task = task_fixture(project, list, %{title: "List task"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    assert has_element?(view, "#location-heading", project.name)
    assert has_element?(view, "#include-child-lists[aria-pressed='true']")
    assert has_element?(view, "#task-#{direct_task.id}")
    assert has_element?(view, "#task-#{list_task.id}")
  end

  test "shows a Location column only for descendant Task views", %{conn: conn} do
    project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, task_list, %{title: "List task"})
    _direct_task = task_fixture(project, %{title: "Direct task"})

    {:ok, descendant_view, _html} =
      live(conn, ~p"/projects/#{project.id}?include_children=true")

    assert has_element?(descendant_view, "#task-number-header", "#")
    assert has_element?(descendant_view, "#task-location-header", "Location")

    assert has_element?(
             descendant_view,
             "#task-location-cell-#{task.id}[aria-label='Location: Planning']"
           )

    {:ok, direct_view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(direct_view, "#task-number-header", "#")
    refute has_element?(direct_view, "#task-location-header")
    refute has_element?(direct_view, "#task-location-cell-#{task.id}")
  end

  test "empty descendant views describe the full selected location", %{conn: conn} do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    _child = list_fixture(project, list, %{name: "Launch"})

    {:ok, view, _html} =
      live(conn, ~p"/projects/#{project.id}/lists/#{list.id}?include_children=true")

    assert has_element?(
             view,
             "#tasks-empty",
             "No tasks in this List or its child Lists yet"
           )
  end

  test "unrecognized descendant query is treated as false and canonicalized by generated links",
       %{
         conn: conn
       } do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})

    {:ok, view, _html} =
      live(conn, "/projects/#{project.id}/lists/#{list.id}?include_children=1")

    assert has_element?(view, "#include-child-lists[aria-pressed='false']")

    view |> element("#add-task") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/lists/#{list.id}/tasks/new")
  end

  test "malformed and cross-Project List routes keep a valid Project sidebar usable", %{
    conn: conn
  } do
    project = project_fixture(%{})
    foreign_project = project_fixture(%{})
    foreign_list = list_fixture(foreign_project, nil, %{name: "Foreign"})

    route_shapes = [
      fn list_id -> "/projects/#{project.id}/lists/#{list_id}" end,
      fn list_id -> "/projects/#{project.id}/lists/#{list_id}/tasks/new" end,
      fn list_id -> "/projects/#{project.id}/lists/#{list_id}/tasks/999999" end
    ]

    for route <- route_shapes, list_id <- ["not-an-id", Integer.to_string(foreign_list.id)] do
      {:ok, view, _html} = live(conn, route.(list_id))

      assert has_element?(view, "#project-sidebar")
      assert has_element?(view, "#location-not-found")
      assert has_element?(view, "#project-#{project.id}")
      refute has_element?(view, "#add-task")
      refute has_element?(view, "#task-modal")
    end
  end

  test "new Task route and cancellation preserve the selected List and query", %{conn: conn} do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})

    {:ok, view, _html} =
      live(conn, ~p"/projects/#{project.id}/lists/#{list.id}/tasks/new?include_children=true")

    assert has_element?(view, "#location-heading", "Planning")
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-create-location", "List Planning")
    assert has_element?(view, "#task-parent-picker")
    refute has_element?(view, "#task-parent-search")
    assert has_element?(view, "#task-parent-trigger", "No parent")

    view |> element("#cancel-task") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/lists/#{list.id}?include_children=true")
    refute has_element?(view, "#task-modal")
  end

  test "Add subtask captures the source List while preserving the Project browse route", %{
    conn: conn
  } do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    source = task_fixture(project, planning, %{title: "Plan release"})
    replacement = task_fixture(project, %{title: "Project replacement"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    view |> element("#add-subtask-#{source.id}") |> render_click()

    assert_patch(
      view,
      ~p"/projects/#{project.id}/tasks/new?include_children=true&parent_task_id=#{source.id}"
    )

    assert has_element?(view, "#task-#{source.id}")
    assert has_element?(view, "#task-create-location", "List Planning")
    assert has_element?(view, "#task-parent-trigger", "Plan release")

    view |> element("#task-parent-trigger") |> render_click()

    view
    |> element("#task-parent-search")
    |> render_change(%{
      "_target" => ["parent_query"],
      "parent_query" => "Project replacement"
    })

    view |> element("#task-parent-option-#{replacement.id}") |> render_click()

    assert has_element?(view, "#task-create-location", "List Planning")
    assert has_element?(view, "#task-parent-trigger", "Project replacement")

    view |> element("#task-parent-trigger") |> render_click()
    view |> element("#task-parent-clear") |> render_click()

    assert has_element?(view, "#task-create-location", "List Planning")
    assert has_element?(view, "#task-parent-trigger", "No parent")

    view
    |> form("#task-form", task: %{title: "Captured List child"})
    |> render_submit()

    assert_patch(view, ~p"/projects/#{project.id}?include_children=true")

    created =
      Enum.find(Tasks.list_tasks_for_project(project), &(&1.title == "Captured List child"))

    assert created.list_id == planning.id
    assert created.parent_task_id == nil
    assert has_element?(view, "#task-#{created.id}")
  end

  test "Add subtask captures a Project-root source location", %{conn: conn} do
    project = project_fixture(%{})
    source = task_fixture(project, %{title: "Project root parent"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#add-subtask-#{source.id}") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/tasks/new?parent_task_id=#{source.id}")
    assert has_element?(view, "#task-#{source.id}")
    assert has_element?(view, "#task-create-location", "Project #{project.name}")
    assert has_element?(view, "#task-parent-trigger", "Project root parent")

    view
    |> form("#task-form", task: %{title: "Project-root child"})
    |> render_submit()

    assert_patch(view, ~p"/projects/#{project.id}")

    created =
      Enum.find(Tasks.list_tasks_for_project(project), &(&1.title == "Project-root child"))

    assert created.list_id == nil
    assert created.parent_task_id == source.id
    assert has_element?(view, "#task-#{created.id}")
  end

  test "creating a List-owned subtask does not refresh it into a direct Project view", %{
    conn: conn
  } do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    source = task_fixture(project, planning, %{title: "Plan release"})

    {:ok, view, _html} =
      live(conn, ~p"/projects/#{project.id}/tasks/new?parent_task_id=#{source.id}")

    view
    |> form("#task-form", task: %{title: "Hidden List child"})
    |> render_submit()

    assert_patch(view, ~p"/projects/#{project.id}")

    created =
      Enum.find(Tasks.list_tasks_for_project(project), &(&1.title == "Hidden List child"))

    assert created.list_id == planning.id
    assert created.parent_task_id == source.id
    refute has_element?(view, "#task-#{created.id}")
  end

  test "saving a new Task creates it in the selected List and returns to its URL context", %{
    conn: conn
  } do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{list.id}/tasks/new")

    task_params = %{
      title: "List-owned Task",
      description: "Created in Planning",
      status: "pending",
      priority: "none",
      due_at: ""
    }

    view
    |> form("#task-form", task: task_params)
    |> render_change()

    view
    |> form("#task-form", task: task_params)
    |> render_submit()

    assert {:ok, [listed]} = Tasks.list_tasks_for_location(project, list)
    assert listed.task.title == "List-owned Task"
    assert listed.task.list_id == list.id
    assert_patch(view, ~p"/projects/#{project.id}/lists/#{list.id}")
    assert has_element?(view, "#task-#{listed.task.id}")
  end

  test "Task detail route works over a List context and close preserves descendant state", %{
    conn: conn
  } do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, list, %{name: "Launch"})
    task = task_fixture(project, child, %{title: "Launch task"})

    {:ok, view, _html} =
      live(
        conn,
        ~p"/projects/#{project.id}/lists/#{list.id}/tasks/#{task.id}?include_children=true"
      )

    assert has_element?(view, "#location-heading", "Planning")
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-form")
    assert has_element?(view, "#task-#{task.id}")
    assert has_element?(view, "#include-child-lists[aria-pressed='true']")

    view |> element("#task-modal-close") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/lists/#{list.id}?include_children=true")
    refute has_element?(view, "#task-modal")
  end

  test "opening a Task from a List row preserves the List context and query", %{conn: conn} do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, list, %{title: "List task"})

    {:ok, view, _html} =
      live(conn, ~p"/projects/#{project.id}/lists/#{list.id}?include_children=true")

    view |> element("#open-task-#{task.id}") |> render_click()

    assert_patch(
      view,
      ~p"/projects/#{project.id}/lists/#{list.id}/tasks/#{task.id}?include_children=true"
    )

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-title[value='List task']")
  end

  test "Task detail lookup is Project-scoped even when the Task is outside the List result", %{
    conn: conn
  } do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, nil, %{title: "Direct Project task"})

    {:ok, view, _html} =
      live(conn, ~p"/projects/#{project.id}/lists/#{list.id}/tasks/#{task.id}")

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-form")
    assert has_element?(view, "#task-title[value='Direct Project task']")
  end

  test "navigation expansion is transient and does not patch the selected Project URL", %{
    conn: conn
  } do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    _child = list_fixture(project, root, %{name: "Launch"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    refute has_element?(view, "#list-#{root.id}")

    view |> element("#toggle-project-#{project.id}") |> render_click()

    assert has_element?(view, "#list-#{root.id}")
    assert has_element?(view, "#project-#{project.id}[aria-current='page']")
  end

  test "selected List ancestors remain visible in the flattened navigation", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    leaf = list_fixture(project, child, %{name: "Copy"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{leaf.id}")

    assert has_element?(view, "#project-#{project.id}")
    assert has_element?(view, "#list-#{root.id}")
    assert has_element?(view, "#list-#{child.id}")
    assert has_element?(view, "#list-#{leaf.id}[aria-current='page']")
  end

  test "creates a root List without changing the selected Project", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#add-list-project-#{project.id}") |> render_click()
    assert has_element?(view, "#list-create-form-root")

    view
    |> form("#list-create-form-root", list: %{name: "Planning"})
    |> render_submit()

    [task_list] = Lists.list_lists_for_project(project)
    assert task_list.name == "Planning"
    assert has_element?(view, "#project-#{project.id}[aria-current='page']")
    assert has_element?(view, "#list-#{task_list.id}")
    refute has_element?(view, "#list-create-form-root")
  end

  test "creates a child List, expands its parent, and keeps Project selection", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#toggle-project-#{project.id}") |> render_click()
    view |> element("#add-child-list-#{root.id}") |> render_click()
    assert has_element?(view, "#list-create-form-#{root.id}")

    view
    |> form("#list-create-form-#{root.id}", list: %{name: "Launch"})
    |> render_submit()

    [^root, child] = Lists.list_lists_for_project(project)
    assert child.parent_list_id == root.id
    assert has_element?(view, "#project-#{project.id}[aria-current='page']")
    assert has_element?(view, "#list-#{child.id}")
    assert has_element?(view, "#toggle-list-#{root.id}[aria-expanded='true']")
  end

  test "List validation errors keep the active popover open", %{conn: conn} do
    project = project_fixture(%{})
    _existing = list_fixture(project, nil, %{name: "Planning"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#add-list-project-#{project.id}") |> render_click()

    for name <- ["   ", String.duplicate("x", 256), "planning"] do
      view
      |> form("#list-create-form-root", list: %{name: name})
      |> render_submit()

      assert has_element?(view, "#list-create-form-root")
      assert has_element?(view, "#list-create-form-root [data-role='field-error']")
    end

    assert Lists.list_lists_for_project(project) |> Enum.map(& &1.name) == ["Planning"]
  end

  test "duplicate child List validation keeps the active popover open", %{conn: conn} do
    project = project_fixture(%{})
    parent = list_fixture(project, nil, %{name: "Planning"})
    _existing = list_fixture(project, parent, %{name: "Review"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#toggle-project-#{project.id}") |> render_click()
    view |> element("#add-child-list-#{parent.id}") |> render_click()

    view
    |> form("#list-create-form-#{parent.id}", list: %{name: "review"})
    |> render_submit()

    assert has_element?(view, "#list-create-form-#{parent.id}")

    assert has_element?(
             view,
             "#list-create-form-#{parent.id} [data-role='field-error']"
           )
  end

  test "renaming a List preserves selection and refreshes visible paths", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    leaf = list_fixture(project, child, %{name: "Copy"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{leaf.id}")

    view |> element("#rename-list-#{root.id}") |> render_click()
    assert has_element?(view, "#list-rename-form-#{root.id}")

    view
    |> form("#list-rename-form-#{root.id}", list: %{name: "Roadmap"})
    |> render_submit()

    assert has_element?(view, "#list-#{leaf.id}[aria-current='page']")
    assert has_element?(view, "#location-heading", "Copy")
    assert has_element?(view, "#location-path", "Roadmap / Launch / Copy")
    assert has_element?(view, "#list-#{root.id}", "Roadmap")
    refute has_element?(view, "#list-rename-form-#{root.id}")
  end

  test "renaming a List with a duplicate sibling name keeps its form and selection", %{
    conn: conn
  } do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    _duplicate = list_fixture(project, nil, %{name: "Roadmap"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{root.id}")

    view |> element("#rename-list-#{root.id}") |> render_click()

    view
    |> form("#list-rename-form-#{root.id}", list: %{name: "roadmap"})
    |> render_submit()

    assert has_element?(view, "#list-rename-form-#{root.id}")
    assert has_element?(view, "#list-rename-form-#{root.id} [data-role='field-error']")
    assert has_element?(view, "#list-#{root.id}[aria-current='page']")
  end

  test "a root List action uses its owning Project and preserves another selection", %{
    conn: conn
  } do
    selected_project = project_fixture(%{})
    target_project = project_fixture(%{})

    {:ok, view, _html} = live(conn, ~p"/projects/#{selected_project.id}")

    view |> element("#add-list-project-#{target_project.id}") |> render_click()

    assert has_element?(view, "#project-#{target_project.id} #list-create-form-root")
    assert has_element?(view, "#project-#{selected_project.id}[aria-current='page']")

    assert Enum.count(
             LazyHTML.query(LazyHTML.from_fragment(render(view)), "#list-create-form-root")
           ) ==
             1

    view
    |> form("#list-create-form-root", list: %{name: "Target root"})
    |> render_submit()

    assert [%{project_id: project_id}] = Lists.list_lists_for_project(target_project)
    assert project_id == target_project.id
    assert Lists.list_lists_for_project(selected_project) == []
    assert has_element?(view, "#project-#{selected_project.id}[aria-current='page']")
  end

  test "malformed, stale, and cross-Project navigation IDs do not change expansion state", %{
    conn: conn
  } do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    other_root = list_fixture(other_project, nil, %{name: "Other"})
    other_child = list_fixture(other_project, other_root, %{name: "Nested"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    render_click(view, "toggle_navigation_node", %{
      "kind" => "project",
      "id" => "not-an-id",
      "project-id" => Integer.to_string(project.id)
    })

    refute has_element?(view, "#list-#{root.id}")

    render_click(view, "toggle_navigation_node", %{
      "kind" => "project",
      "id" => "999999999",
      "project-id" => "999999999"
    })

    refute has_element?(view, "#list-#{other_root.id}")

    render_click(view, "toggle_navigation_node", %{
      "kind" => "list",
      "id" => Integer.to_string(other_root.id),
      "project-id" => Integer.to_string(project.id)
    })

    refute has_element?(view, "#list-#{root.id}")
    refute has_element?(view, "#list-#{other_root.id}")

    view |> element("#select-project-#{other_project.id}") |> render_click()
    view |> element("#toggle-project-#{other_project.id}") |> render_click()
    assert has_element?(view, "#list-#{other_root.id}")
    refute has_element?(view, "#list-#{other_child.id}")

    render_click(view, "open_list_form", %{
      "kind" => "new",
      "parent-id" => Integer.to_string(other_root.id),
      "project-id" => Integer.to_string(project.id)
    })

    refute has_element?(view, "#list-create-form-root")
    refute has_element?(view, "#list-create-form-#{other_root.id}")
  end

  test "renaming a non-selected Project List preserves the selected location path", %{conn: conn} do
    selected_project = project_fixture(%{})
    selected_root = list_fixture(selected_project, nil, %{name: "Selected"})
    selected_child = list_fixture(selected_project, selected_root, %{name: "Context"})
    target_project = project_fixture(%{})
    target_root = list_fixture(target_project, nil, %{name: "Target"})

    {:ok, view, _html} =
      live(conn, ~p"/projects/#{selected_project.id}/lists/#{selected_child.id}")

    view |> element("#toggle-project-#{target_project.id}") |> render_click()
    view |> element("#rename-list-#{target_root.id}") |> render_click()

    view
    |> form("#list-rename-form-#{target_root.id}", list: %{name: "Renamed target"})
    |> render_submit()

    assert has_element?(view, "#location-heading", "Context")
    assert has_element?(view, "#location-path", "Selected / Context")
    assert has_element?(view, "#list-#{selected_child.id}[aria-current='page']")
    assert has_element?(view, "#list-#{target_root.id}", "Renamed target")
  end
end
