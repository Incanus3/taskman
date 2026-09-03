defmodule TaskmanWeb.ProjectLive.WorkspaceUpdatesTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Lists
  alias Taskman.Projects

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  @workspace_topic "workspace:changes"

  test "subscribes once to workspace changes for index and Project routes", %{conn: conn} do
    project_a = project_fixture(%{})
    project_b = project_fixture(%{})

    {:ok, view, _html} = live(conn, ~p"/")

    assert subscription_count(view, @workspace_topic) == 1
    assert subscription_count(view, project_task_topic(project_a)) == 0

    render_patch(view, ~p"/projects/#{project_a.id}")
    assert subscription_count(view, @workspace_topic) == 1
    assert subscription_count(view, project_task_topic(project_a)) == 1

    render_patch(view, ~p"/projects/#{project_a.id}?include_children=true")
    assert subscription_count(view, @workspace_topic) == 1
    assert subscription_count(view, project_task_topic(project_a)) == 1

    render_patch(view, ~p"/projects/#{project_b.id}")
    assert subscription_count(view, @workspace_topic) == 1
    assert subscription_count(view, project_task_topic(project_a)) == 0
    assert subscription_count(view, project_task_topic(project_b)) == 1

    render_patch(view, ~p"/")
    assert subscription_count(view, @workspace_topic) == 1
    assert subscription_count(view, project_task_topic(project_b)) == 0
  end

  test "external Project creation refreshes index and selected Project sidebars", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/")

    created =
      externally(fn ->
        Projects.create_project(%{name: "External", primary_directory: File.cwd!()})
      end)

    assert {:ok, created} = created

    sync_view(view)
    assert has_element?(view, "#project-#{created.id}", "External")

    render_patch(view, ~p"/projects/#{project.id}")

    other =
      externally(fn ->
        Projects.create_project(%{name: "Later", primary_directory: File.cwd!()})
      end)

    assert {:ok, other} = other

    sync_view(view)
    assert has_element?(view, "#project-#{other.id}", "Later")
    assert has_element?(view, "#project-#{project.id}[aria-current='page']")
  end

  test "external root and child List creation refreshes the owning Project branch", %{conn: conn} do
    project = project_fixture(%{})
    foreign = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    root = externally(fn -> Lists.create_list(project, nil, %{name: "Planning"}) end)
    assert {:ok, root} = root
    foreign_root = externally(fn -> Lists.create_list(foreign, nil, %{name: "Foreign"}) end)
    assert {:ok, foreign_root} = foreign_root

    sync_view(view)
    refute has_element?(view, "#list-#{root.id}")

    view |> element("#toggle-project-#{project.id}") |> render_click()
    assert has_element?(view, "#list-#{root.id}", "Planning")
    refute has_element?(view, "#list-#{foreign_root.id}")

    child = externally(fn -> Lists.create_list(project, root, %{name: "Launch"}) end)
    assert {:ok, child} = child

    sync_view(view)
    view |> element("#toggle-list-#{root.id}") |> render_click()
    assert has_element?(view, "#list-#{child.id}", "Launch")
    assert has_element?(view, "#toggle-list-#{root.id}[aria-expanded='true']")
  end

  test "external List rename refreshes labels, selected paths, locations, and location order", %{
    conn: conn
  } do
    project = project_fixture(%{})
    first = list_fixture(project, nil, %{name: "Zulu"})
    second = list_fixture(project, nil, %{name: "Bravo"})
    first_task = task_fixture(project, first, %{title: "First"})
    second_task = task_fixture(project, second, %{title: "Second"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    view |> element("#toggle-project-#{project.id}") |> render_click()
    view |> element("#sort-task-location") |> render_click()
    assert has_element?(view, "#task-location-header[aria-sort='ascending']")

    assert {:ok, renamed} =
             externally(fn -> Lists.rename_list(project, second, %{name: "Alpha"}) end)

    sync_view(view)
    assert has_element?(view, "#list-#{renamed.id}", "Alpha")

    assert has_element?(
             view,
             "#task-location-cell-#{second_task.id}[aria-label='Location: Alpha']"
           )

    assert has_element?(view, "#location-heading", project.name)
    assert has_element?(view, "#tasks > #tasks-#{second_task.id} + #tasks-#{first_task.id}")
  end

  test "a List event for another Project refreshes only workspace navigation", %{conn: conn} do
    selected_project = project_fixture(%{})
    selected_root = list_fixture(selected_project, nil, %{name: "Selected"})
    selected_task = task_fixture(selected_project, selected_root, %{title: "Selected task"})
    target_project = project_fixture(%{})
    target_root = list_fixture(target_project, nil, %{name: "Target"})

    {:ok, view, _html} =
      live(conn, ~p"/projects/#{selected_project.id}/lists/#{selected_root.id}")

    assert has_element?(view, "#task-#{selected_task.id}")
    view |> element("#toggle-project-#{target_project.id}") |> render_click()

    assert {:ok, renamed} =
             externally(fn ->
               Lists.rename_list(target_project, target_root, %{name: "Other"})
             end)

    sync_view(view)
    assert has_element?(view, "#list-#{renamed.id}", "Other")
    assert has_element?(view, "#location-path", "Selected")
    assert has_element?(view, "#task-#{selected_task.id}")
    assert view_assigns(view).selected_project.id == selected_project.id
  end

  test "duplicate and delayed List events converge on canonical navigation", %{conn: conn} do
    project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Before"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert {:ok, latest} =
             externally(fn -> Lists.rename_list(project, task_list, %{name: "Latest"}) end)

    sync_view(view)

    event = list_event(project.id, latest.id, :updated, [:name])
    send(view.pid, event)
    send(view.pid, event)
    send(view.pid, list_event(project.id, latest.id, :updated, [:name]))
    sync_view(view)

    view |> element("#toggle-project-#{project.id}") |> render_click()
    assert has_element?(view, "#list-#{latest.id}", "Latest")
    refute has_element?(view, "#list-#{latest.id}", "Before")
  end

  test "workspace events preserve Project and List form drafts and active form IDs", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    {:ok, index, _html} = live(conn, ~p"/")

    index
    |> form("#project-form", project: %{name: "Draft Project", primary_directory: File.cwd!()})
    |> render_change(%{"_target" => ["project", "name"]})

    assert {:ok, _created} =
             externally(fn ->
               Projects.create_project(%{name: "Elsewhere", primary_directory: File.cwd!()})
             end)

    sync_view(index)
    assert has_element?(index, "#project-form input[name='project[name]'][value='Draft Project']")

    render_patch(index, ~p"/projects/#{project.id}")
    index |> element("#add-list-project-#{project.id}") |> render_click()

    index
    |> form("#list-create-form-root", list: %{name: "Draft root"})
    |> render_change(%{"_target" => ["list", "name"]})

    assert {:ok, _external_root} =
             externally(fn -> Lists.create_list(project, nil, %{name: "External root"}) end)

    sync_view(index)

    assert has_element?(
             index,
             "#list-create-form-root input[name='list[name]'][value='Draft root']"
           )

    index |> element("#toggle-project-#{project.id}") |> render_click()
    index |> element("#add-child-list-#{root.id}") |> render_click()

    index
    |> form("#list-create-form-#{root.id}", list: %{name: "Draft child"})
    |> render_change(%{"_target" => ["list", "name"]})

    assert {:ok, _external_child} =
             externally(fn -> Lists.create_list(project, root, %{name: "External child"}) end)

    sync_view(index)

    assert has_element?(
             index,
             "#list-create-form-#{root.id} input[name='list[name]'][value='Draft child']"
           )
  end

  test "workspace List events preserve rename and Task modal state without navigation", %{
    conn: conn
  } do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    selected = list_fixture(project, root, %{name: "Launch"})
    task = task_fixture(project, selected, %{title: "Modal task"})
    task_path = ~p"/projects/#{project.id}/lists/#{selected.id}/tasks/#{task.id}"

    {:ok, view, _html} = live(conn, task_path)

    view |> element("#rename-list-#{root.id}") |> render_click()

    view
    |> form("#list-rename-form-#{root.id}", list: %{name: "Mine"})
    |> render_change(%{"_target" => ["list", "name"]})

    assert {:ok, renamed} =
             externally(fn -> Lists.rename_list(project, root, %{name: "Latest"}) end)

    sync_view(view)
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-title[value='Modal task']")

    assert has_element?(
             view,
             "#list-rename-form-#{renamed.id} input[name='list[name]'][value='Mine']"
           )

    assert has_element?(view, "#location-path", "Latest / Launch")
    refute_patched(view, task_path)
  end

  test "external List rename refreshes active move destinations and retains selection", %{
    conn: conn
  } do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Before"})
    child = list_fixture(project, root, %{name: "Launch"})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{child.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()

    assert has_element?(
             view,
             "#move-task-option-list-#{child.id}[aria-label='Before / Launch'][aria-selected='true']"
           )

    assert {:ok, _renamed} =
             externally(fn -> Lists.rename_list(project, root, %{name: "Latest"}) end)

    sync_view(view)
    assert has_element?(view, "#move-task-#{task.id}")

    assert has_element?(
             view,
             "#move-task-search-#{task.id}[aria-expanded='true'][value='Latest / Launch']"
           )

    assert has_element?(
             view,
             "#move-task-option-list-#{child.id}[aria-label='Latest / Launch'][aria-selected='true']"
           )
  end

  test "external List rename refreshes an open parent picker location label", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Before"})
    child = list_fixture(project, root, %{name: "Launch"})
    parent = task_fixture(project, child, %{title: "Parent"})
    selected = task_fixture(project, child, %{title: "Selected"}, parent: parent)
    task_path = ~p"/projects/#{project.id}/lists/#{child.id}/tasks/#{selected.id}"

    {:ok, view, _html} = live(conn, task_path)

    view |> element("#task-parent-trigger") |> render_click()

    view
    |> element("#task-parent-search")
    |> render_change(%{
      "_target" => ["parent_query"],
      "parent_query" => "Parent"
    })

    assert has_element?(
             view,
             "#task-parent-option-#{parent.id}[aria-label='Parent · Task ##{parent.id} · Before / Launch']"
           )

    assert {:ok, _renamed} =
             externally(fn -> Lists.rename_list(project, root, %{name: "Latest"}) end)

    sync_view(view)
    assert has_element?(view, "#task-parent-search[value='Parent'][aria-expanded='true']")

    assert has_element?(
             view,
             "#task-parent-option-#{parent.id}[aria-label='Parent · Task ##{parent.id} · Latest / Launch']"
           )
  end

  test "external List rename reloads open Task hierarchy location paths", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Before"})
    parent = task_fixture(project, root, %{title: "Parent"})
    selected = task_fixture(project, root, %{title: "Selected"}, parent: parent)
    task_path = ~p"/projects/#{project.id}/lists/#{root.id}/tasks/#{selected.id}"

    {:ok, view, _html} = live(conn, task_path)
    assert %{task_hierarchy: %{hierarchy: %{root: hierarchy_root}}} = view_assigns(view)
    assert Enum.map(hierarchy_root.location_path, & &1.name) == ["Before"]

    assert {:ok, _renamed} =
             externally(fn -> Lists.rename_list(project, root, %{name: "Latest"}) end)

    sync_view(view)

    assigns = view_assigns(view)
    assert %{task_hierarchy: %{hierarchy: %{root: hierarchy_root}}} = assigns
    assert Enum.map(hierarchy_root.location_path, & &1.name) == ["Latest"]

    selected_node = find_hierarchy_node(hierarchy_root, selected.id)
    assert Enum.map(selected_node.location_path, & &1.name) == ["Latest"]
    assert has_element?(view, "#task-modal")
    refute_patched(view, task_path)
  end

  test "external List rename preserves a Task-create modal draft and location", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Before"})
    task_path = ~p"/projects/#{project.id}/lists/#{root.id}/tasks/new?include_children=true"

    {:ok, view, _html} = live(conn, task_path)

    view
    |> form("#task-form", task: %{title: "Draft task"})
    |> render_change(%{"_target" => ["task", "title"]})

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-title[value='Draft task']")
    assert has_element?(view, "#task-create-location", "List Before")

    assert {:ok, _renamed} =
             externally(fn -> Lists.rename_list(project, root, %{name: "Latest"}) end)

    sync_view(view)
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-title[value='Draft task']")
    assert has_element?(view, "#task-create-location", "List Latest")
    assert view_assigns(view).task_create_location.name == "Latest"
    refute_patched(view, task_path)
  end

  defp externally(fun) do
    fun
    |> Task.async()
    |> Task.await()
  end

  defp sync_view(view), do: _ = :sys.get_state(view.pid)

  defp view_assigns(view) do
    %{socket: socket} = :sys.get_state(view.pid)
    socket.assigns
  end

  defp find_hierarchy_node(%{task: %{id: task_id}} = node, task_id), do: node

  defp find_hierarchy_node(%{children: children}, task_id) do
    Enum.find_value(children, &find_hierarchy_node(&1, task_id))
  end

  defp project_task_topic(project), do: "projects:#{project.id}:tasks"

  defp subscription_count(view, topic) do
    Taskman.PubSub
    |> Registry.lookup(topic)
    |> Enum.count(fn {subscriber, _metadata} -> subscriber == view.pid end)
  end

  defp list_event(project_id, list_id, operation, fields) do
    %Event{
      entity: :list,
      operation: operation,
      project_id: project_id,
      entity_id: list_id,
      lock_version: nil,
      fields: fields
    }
  end
end
