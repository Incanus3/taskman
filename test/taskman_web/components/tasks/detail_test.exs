defmodule TaskmanWeb.Tasks.DetailTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Phoenix.LiveView.JS
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.{HierarchyNode, Task}
  alias TaskmanWeb.ProjectLive.Tasks.{Activity, Autosave, Comments, Hierarchy, Move, ParentPicker}
  alias TaskmanWeb.Tasks.Detail

  test "header identifies the Task and wires its supplied Close action" do
    task = %Task{id: 41, project_id: 7, title: "Launch", status: :pending, priority: :none}

    document =
      render_component(&Detail.header/1, %{
        task: task,
        close_id: "task-modal-close",
        on_close: JS.push("request_task_departure")
      })
      |> LazyHTML.from_fragment()

    assert document
           |> LazyHTML.query("#task-dialog-header #task-modal-title")
           |> LazyHTML.text()
           |> String.trim() == "Task #41"

    assert Enum.count(LazyHTML.query(document, "#task-modal-title")) == 1

    [actions] =
      document
      |> LazyHTML.query("#task-dialog-header #task-modal-close[aria-label='Close dialog']")
      |> LazyHTML.attribute("phx-click")

    assert actions =~ "request_task_departure"
  end

  test "renders the selected Task's complete navigable List path before the current Task" do
    project = %Project{id: 7, name: "Atlas"}
    planning = %TaskList{id: 11, project_id: project.id, name: "Planning"}
    launch = %TaskList{id: 12, project_id: project.id, name: "Launch"}
    task = task(41)

    task_hierarchy =
      Hierarchy.load(
        Hierarchy.empty(),
        %Tasks.Hierarchy{
          selected_task_id: task.id,
          root: hierarchy_node(task.id, [], [planning, launch])
        }
      )

    html =
      render_component(&Detail.detail/1, %{
        task: task,
        project: project,
        description_editor: %{editor_id: "description-editor", initial_value: ""},
        task_autosave: Autosave.load(Autosave.empty(), task, saved?: true),
        parent_picker: ParentPicker.empty(),
        cancel: "/projects/7/lists/12",
        task_hierarchy: task_hierarchy,
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        browse_path: fn
          nil -> "/projects/7?include_children=true"
          task_list -> "/projects/7/lists/#{task_list.id}?include_children=true"
        end,
        task_move: Move.empty()
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-location-breadcrumbs[aria-label='Task location'][phx-hook='TaskmanWeb.Tasks.Detail.TaskLocationBreadcrumbs']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-location-list-11[href='/projects/7/lists/11?include_children=true'][data-optional-segment]"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-location-list-12[href='/projects/7/lists/12?include_children=true'][data-containing-location]"
             )
           )

    assert document
           |> LazyHTML.query("#task-location-current[aria-current='page']")
           |> LazyHTML.text()
           |> String.trim() == "Task #41"

    refute Enum.empty?(LazyHTML.query(document, "#task-location-ellipsis[hidden]"))
  end

  test "uses the navigable Project name as the root Task location" do
    project = %Project{id: 7, name: "Atlas"}
    task = task(41)

    html =
      render_component(&Detail.detail/1, %{
        task: task,
        project: project,
        description_editor: %{editor_id: "description-editor", initial_value: ""},
        task_autosave: Autosave.load(Autosave.empty(), task, saved?: true),
        parent_picker: ParentPicker.empty(),
        cancel: "/projects/7",
        task_hierarchy:
          Hierarchy.load(
            Hierarchy.empty(),
            %Tasks.Hierarchy{selected_task_id: task.id, root: hierarchy_node(task.id)}
          ),
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        browse_path: fn
          nil -> "/projects/7?include_children=true"
          task_list -> "/projects/7/lists/#{task_list.id}?include_children=true"
        end,
        task_move: Move.empty()
      })

    document = LazyHTML.from_fragment(html)

    assert document
           |> LazyHTML.query(
             "#task-location-project-7[href='/projects/7?include_children=true'][data-containing-location]"
           )
           |> LazyHTML.text()
           |> String.trim() == "Atlas"
  end

  test "renders independent field-local save status and no form-wide footer" do
    task = %Task{id: 41, project_id: 7, title: "Launch", status: :pending, priority: :none}

    task_autosave = %{
      Autosave.load(Autosave.empty(), task, saved?: false)
      | field_states: %{"title" => :saving, "description" => :saved}
    }

    html =
      render_component(&Detail.detail/1, %{
        task: task,
        project: %Project{id: 7, name: "Atlas"},
        description_editor: %{editor_id: "description-editor", initial_value: ""},
        task_autosave: task_autosave,
        parent_picker: ParentPicker.empty(),
        cancel: "/projects/7",
        task_hierarchy:
          Hierarchy.load(
            Hierarchy.empty(),
            %Tasks.Hierarchy{selected_task_id: task.id, root: hierarchy_node(task.id)}
          ),
        task_path: fn task -> "/projects/7/tasks/#{task.id}" end,
        browse_path: fn
          nil -> "/projects/7"
          task_list -> "/projects/7/lists/#{task_list.id}"
        end,
        task_move: Move.empty()
      })

    document = LazyHTML.from_fragment(html)

    assert document
           |> LazyHTML.query("#task-location-task-label")
           |> LazyHTML.text()
           |> String.trim() ==
             "Task #41"

    refute Enum.empty?(LazyHTML.query(document, "#task-form"))

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-title-save-status[aria-live='polite'][data-state='saving']"
             )
           )

    assert LazyHTML.text(LazyHTML.query(document, "#task-title-save-status")) =~ "Saving…"

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-description-save-status[aria-live='polite'][data-state='saved']"
             )
           )

    assert LazyHTML.text(LazyHTML.query(document, "#task-description-save-status")) =~ "Saved"

    assert Enum.empty?(LazyHTML.query(document, "#task-status-save-status[data-state]"))
    assert Enum.empty?(LazyHTML.query(document, "#task-priority-save-status[data-state]"))
    assert Enum.empty?(LazyHTML.query(document, "#task-due-at-save-status[data-state]"))
    assert Enum.empty?(LazyHTML.query(document, "#task-save-status"))
    refute Enum.empty?(LazyHTML.query(document, "#move-task-detail-button-41.cursor-pointer"))
  end

  test "Activity tab and panel expose the selected accessible tab contract" do
    document = comment_tabs(:activity)

    assert tab_contract?(document, "activity", "true", "0")
    assert tab_contract?(document, "sessions", "false", "-1")
    assert visible_panel?(document, "activity")
    assert inactive_panel?(document, "sessions")
  end

  test "Sessions selection hides and inerts Activity while preserving linked panels" do
    document = comment_tabs(:sessions)

    assert tab_contract?(document, "sessions", "true", "0")
    assert tab_contract?(document, "activity", "false", "-1")
    assert visible_panel?(document, "sessions")
    assert inactive_panel?(document, "activity")
  end

  test "mixed Activity uses feed state for empty status and scroll revision" do
    time = ~U[2026-11-07 10:00:00Z]

    entry = %{
      id: "event-12",
      kind: :change,
      occurred_at: time,
      change: %{type: "task_created", actor: %{login: "actor@example.com"}}
    }

    document =
      comment_tabs(:activity,
        activity: %Activity.State{task_id: 41, empty?: false, revision: 7},
        activity_stream: [{"task-event-12", entry}]
      )

    assert LazyHTML.text(LazyHTML.query(document, "#task-event-12")) =~ "Task created"
    assert Enum.count(LazyHTML.query(document, "#task-detail-discussion[data-revision='7']")) == 1
    assert Enum.empty?(LazyHTML.query(document, "#task-activity-empty"))
  end

  defp comment_tabs(tab, opts \\ []) do
    task = task(41)

    render_component(&Detail.detail/1, %{
      task: task,
      project: %Project{id: 7, name: "Atlas"},
      description_editor: %{editor_id: "description-editor", initial_value: ""},
      task_autosave: Autosave.load(Autosave.empty(), task, saved?: true),
      parent_picker: ParentPicker.empty(),
      cancel: "/projects/7",
      task_hierarchy:
        Hierarchy.load(
          Hierarchy.empty(),
          %Tasks.Hierarchy{selected_task_id: task.id, root: hierarchy_node(task.id)}
        ),
      task_path: fn item -> "/projects/7/tasks/#{item.id}" end,
      browse_path: fn _ -> "/projects/7" end,
      task_move: Move.empty(),
      comments: %{Comments.State.empty() | tab: tab},
      activity: Keyword.get(opts, :activity, Activity.State.empty()),
      activity_stream: Keyword.get(opts, :activity_stream, [])
    })
    |> LazyHTML.from_fragment()
  end

  defp tab_contract?(document, tab, selected, tabindex) do
    selector =
      "#task-#{tab}-tab[role='tab'][aria-controls='task-#{tab}'][aria-selected='#{selected}'][tabindex='#{tabindex}']"

    length(LazyHTML.query(document, selector) |> LazyHTML.to_tree()) == 1
  end

  defp visible_panel?(document, tab) do
    selector =
      "#task-#{tab}[role='tabpanel'][aria-labelledby='task-#{tab}-tab']:not([hidden]):not([inert])"

    length(LazyHTML.query(document, selector) |> LazyHTML.to_tree()) == 1
  end

  defp inactive_panel?(document, tab) do
    selector = "#task-#{tab}[role='tabpanel'][aria-labelledby='task-#{tab}-tab'][hidden][inert]"
    length(LazyHTML.query(document, selector) |> LazyHTML.to_tree()) == 1
  end

  defp hierarchy_node(id, children \\ [], location_path \\ []) do
    %HierarchyNode{task: task(id), location_path: location_path, children: children}
  end

  defp task(id),
    do: %Task{id: id, project_id: 7, title: "Task #{id}", status: :pending, priority: :none}
end
