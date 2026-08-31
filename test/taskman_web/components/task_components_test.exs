defmodule TaskmanWeb.TaskComponentsTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Lists.TaskList
  alias Taskman.Tasks.{Task, TaskWithLocation}
  alias TaskmanWeb.{TaskComponents, TaskMove}

  test "renders the Task number with the row title" do
    task = %Task{id: 41, title: "Launch", status: :pending, priority: :none}
    task_with_location = %TaskWithLocation{task: task, location_path: []}

    html =
      render_component(&TaskComponents.row/1, %{
        id: "tasks-41",
        task_with_location: task_with_location,
        task_path: "/projects/7/tasks/41",
        include_children?: false,
        task_move: TaskMove.empty()
      })

    document = LazyHTML.from_fragment(html)

    assert LazyHTML.text(LazyHTML.query(document, "#task-number-41")) =~ "#41"
    refute Enum.empty?(LazyHTML.query(document, "#task-identity-41 #task-41"))
  end

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
        task_move: TaskMove.empty()
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
        task_move: TaskMove.empty()
      })

    assert LazyHTML.from_fragment(html)
           |> LazyHTML.query("#task-location-cell-41")
           |> Enum.empty?()
  end

  test "renders an icon-only Move action with a floating right-aligned popover" do
    task = %Task{id: 41, title: "Launch", status: :pending, priority: :none}
    task_with_location = %TaskWithLocation{task: task, location_path: []}

    task_move = %TaskMove{
      active_task: %{
        task_id: task.id,
        origin: :row,
        current_destination: "project",
        task_with_location: task_with_location
      }
    }

    html =
      render_component(&TaskComponents.row/1, %{
        id: "tasks-41",
        task_with_location: task_with_location,
        task_path: "/projects/7/tasks/41",
        include_children?: false,
        task_move: task_move
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
        task_move: TaskMove.empty()
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

    task_move = %TaskMove{
      active_task: %{
        task_id: task.id,
        origin: :row,
        current_destination: "project",
        task_with_location: task_with_location
      }
    }

    html =
      render_component(&TaskComponents.row/1, %{
        id: "tasks-41",
        task_with_location: task_with_location,
        task_path: "/projects/7/tasks/41",
        include_children?: false,
        task_move: task_move
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

    task_move = %TaskMove{
      active_task: %{
        task_id: 42,
        origin: :row,
        current_destination: "project",
        task_with_location: task_with_location
      }
    }

    html =
      render_component(&TaskComponents.row/1, %{
        id: "tasks-41",
        task_with_location: task_with_location,
        task_path: "/projects/7/tasks/41",
        include_children?: false,
        task_move: task_move
      })

    refute Enum.empty?(
             html
             |> LazyHTML.from_fragment()
             |> LazyHTML.query("#task-actions-41.z-10")
           )
  end
end
