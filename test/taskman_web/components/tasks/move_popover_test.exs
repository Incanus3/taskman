defmodule TaskmanWeb.Tasks.MovePopoverTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias TaskmanWeb.ProjectLive.Tasks.Move
  alias TaskmanWeb.Tasks.MovePopover

  test "renders a labelled destination combobox with complete location paths" do
    html =
      render_component(&MovePopover.popover/1, %{
        task_id: 41,
        task_move: %Move{
          active_task: %{
            task_id: 41,
            origin: :row,
            current_destination: "list:12",
            task_with_location: nil
          },
          options_open?: true,
          options: [
            %{id: 1, value: "project", label: "Project · Taskman", current?: false},
            %{id: 11, value: "list:11", label: "Planning", current?: false},
            %{id: 12, value: "list:12", label: "Planning / Launch", current?: true}
          ]
        }
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#move-task-41[role='dialog'][phx-click-away='cancel_move_task'][phx-window-keydown='cancel_move_task'][phx-key='escape']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#move-task-search-41[role='combobox'][aria-controls='move-task-options-41']"
             )
           )

    refute Enum.empty?(LazyHTML.query(document, "#move-task-options-41[role='listbox']"))

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#move-task-option-project-1[aria-label='Project · Taskman']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(document, "#move-task-option-list-11[aria-label='Planning']")
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#move-task-option-list-12[aria-label='Planning / Launch'][data-current-location='true']"
             )
           )

    refute Enum.empty?(LazyHTML.query(document, "#move-task-submit-41[type='button'][disabled]"))
  end

  test "renders an empty search state, local error, and explicit cancellation" do
    html =
      render_component(&MovePopover.popover/1, %{
        task_id: 41,
        task_move:
          task_move(
            destination: "list:12",
            error: "This Task is already in that location.",
            options_open?: true
          )
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(LazyHTML.query(document, "#move-task-options-41 #move-task-no-results-41"))

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#move-task-error-41[role='alert']"
             )
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#move-task-41 button[phx-click='cancel_move_task'][type='button']"
             )
           )

    refute Enum.empty?(LazyHTML.query(document, "#move-task-submit-41[type='button'][disabled]"))
  end

  test "focuses a collapsed destination combobox when the popover mounts" do
    html =
      render_component(&MovePopover.popover/1, %{
        task_id: 41,
        task_move: task_move()
      })

    mounted_actions =
      html
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#move-task-41")
      |> LazyHTML.attribute("phx-mounted")

    assert [mounted_actions] = mounted_actions
    assert mounted_actions =~ "focus"
    assert mounted_actions =~ "#move-task-search-41"

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#move-task-search-41[aria-expanded='false'][phx-click='open_move_destinations']"
             )
           )

    assert Enum.empty?(LazyHTML.query(document, "#move-task-options-41"))
  end

  test "restores the previously pushed focus when the popover is removed" do
    html =
      render_component(&MovePopover.popover/1, %{
        task_id: 41,
        task_move: task_move()
      })

    [remove_actions] =
      html
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#move-task-41")
      |> LazyHTML.attribute("phx-remove")

    assert remove_actions =~ "pop_focus"
  end

  test "can leave Escape ownership to an enclosing dialog" do
    html =
      render_component(&MovePopover.popover/1, %{
        task_id: 41,
        task_move: task_move(),
        window_escape?: false
      })

    document = LazyHTML.from_fragment(html)

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#move-task-41[phx-click-away='cancel_move_task']"
             )
           )

    assert Enum.empty?(
             LazyHTML.query(
               document,
               "#move-task-41[phx-window-keydown]"
             )
           )
  end

  defp task_move(overrides \\ []) do
    struct!(
      %Move{
        active_task: %{
          task_id: 41,
          origin: :row,
          current_destination: "list:12",
          task_with_location: nil
        }
      },
      overrides
    )
  end
end
