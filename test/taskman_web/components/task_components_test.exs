defmodule TaskmanWeb.TaskComponentsTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Lists.TaskList
  alias Taskman.Tasks.{Task, TaskWithLocation}
  alias TaskmanWeb.TaskComponents

  test "renders the descendant Task location as its own labelled column cell" do
    task = %Task{id: 41, title: "Launch", status: :pending, priority: :none}

    task_with_location = %TaskWithLocation{
      task: task,
      location_path: [
        %TaskList{id: 11, name: "Planning"},
        %TaskList{id: 12, name: "Launch"}
      ]
    }

    html =
      render_component(&TaskComponents.row/1, %{
        id: "tasks-41",
        task_with_location: task_with_location,
        task_path: "/projects/7/tasks/41",
        include_children?: true,
        active_move_task: nil,
        move_query: "",
        move_destination: nil,
        move_options: [],
        move_error: nil
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-location-cell-41[aria-label='Location: Planning / Launch']"
             )
           )
  end

  test "omits the location column cell from a direct Task row" do
    task = %Task{id: 41, title: "Launch", status: :pending, priority: :none}
    task_with_location = %TaskWithLocation{task: task, location_path: []}

    html =
      render_component(&TaskComponents.row/1, %{
        id: "tasks-41",
        task_with_location: task_with_location,
        task_path: "/projects/7/tasks/41",
        include_children?: false,
        active_move_task: nil,
        move_query: "",
        move_destination: nil,
        move_options: [],
        move_error: nil
      })

    assert LazyHTML.from_fragment(html)
           |> LazyHTML.query("#task-location-cell-41")
           |> Enum.empty?()
  end

  test "renders an icon-only Move action with a floating right-aligned popover" do
    task = %Task{id: 41, title: "Launch", status: :pending, priority: :none}
    task_with_location = %TaskWithLocation{task: task, location_path: []}

    html =
      render_component(&TaskComponents.row/1, %{
        id: "tasks-41",
        task_with_location: task_with_location,
        task_path: "/projects/7/tasks/41",
        include_children?: false,
        active_move_task: %{task_id: 41, origin: "row", current_destination: "project"},
        move_query: "",
        move_destination: nil,
        move_options: [],
        move_error: nil
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-actions-41 #move-task-row-button-41[aria-label='Move Launch'][title='Move Task'] .hero-arrows-right-left"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#task-actions-41.z-40 [data-move-task-popover].absolute.right-0.top-full #move-task-41"
             )
           )
  end

  test "row Move action pushes its focus before opening the popover" do
    task = %Task{id: 41, title: "Launch", status: :pending, priority: :none}
    task_with_location = %TaskWithLocation{task: task, location_path: []}

    html =
      render_component(&TaskComponents.row/1, %{
        id: "tasks-41",
        task_with_location: task_with_location,
        task_path: "/projects/7/tasks/41",
        include_children?: false,
        active_move_task: nil,
        move_query: "",
        move_destination: nil,
        move_options: [],
        move_error: nil
      })

    [open_actions] =
      html
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#move-task-row-button-41")
      |> LazyHTML.attribute("phx-click")

    assert open_actions =~ ~r/^\[\["push_focus".*"push".*"open_move_task"/
  end

  test "row popover restores focus from its conditional wrapper" do
    task = %Task{id: 41, title: "Launch", status: :pending, priority: :none}
    task_with_location = %TaskWithLocation{task: task, location_path: []}

    html =
      render_component(&TaskComponents.row/1, %{
        id: "tasks-41",
        task_with_location: task_with_location,
        task_path: "/projects/7/tasks/41",
        include_children?: false,
        active_move_task: %{task_id: 41, origin: "row", current_destination: "project"},
        move_query: "",
        move_destination: nil,
        move_options: [],
        move_error: nil
      })

    document = LazyHTML.from_fragment(html)

    [remove_actions] =
      document
      |> LazyHTML.query("[data-move-task-popover]")
      |> LazyHTML.attribute("phx-remove")

    assert remove_actions =~ "pop_focus"

    assert Enum.empty?(
             LazyHTML.query(document, "[data-move-task-popover] #move-task-41[phx-remove]")
           )
  end

  test "keeps an inactive Move action below the active row's popover" do
    task = %Task{id: 41, title: "Launch", status: :pending, priority: :none}
    task_with_location = %TaskWithLocation{task: task, location_path: []}

    html =
      render_component(&TaskComponents.row/1, %{
        id: "tasks-41",
        task_with_location: task_with_location,
        task_path: "/projects/7/tasks/41",
        include_children?: false,
        active_move_task: %{task_id: 42, origin: "row", current_destination: "project"},
        move_query: "",
        move_destination: nil,
        move_options: [],
        move_error: nil
      })

    refute Enum.empty?(
             html
             |> LazyHTML.from_fragment()
             |> LazyHTML.query("#task-actions-41.z-10")
           )
  end
end
