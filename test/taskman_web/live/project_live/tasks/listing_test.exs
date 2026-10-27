defmodule TaskmanWeb.ProjectLive.Tasks.ListingTest do
  use ExUnit.Case, async: true

  alias Taskman.Tasks.{Task, TaskWithLocation, ListingProjection, ListingRow}
  alias TaskmanWeb.ProjectLive.Tasks.Listing.State

  test "normalizes visible statuses and cycles sort directions" do
    state = State.new(Task.statuses() -- [:will_not_do])
    state = State.apply_statuses(state, ["done", "unknown", "pending"])

    assert state.visible_statuses == [:pending, :done]
    assert state.status_filter_form[:statuses].value == ["pending", "done"]

    assert state |> State.sort_by(:title) |> Map.fetch!(:sort) == {:title, :asc}
    assert state |> State.sort_by(:status) |> Map.fetch!(:sort) == {:status, :desc}

    assert state |> State.sort_by(:title) |> State.sort_by(:title) |> Map.fetch!(:sort) ==
             {:title, :desc}
  end

  test "clears an unavailable location sort and updates both empty flags together" do
    state =
      Task.statuses()
      |> State.new()
      |> State.sort_by(:location)
      |> State.available_sort(false)
      |> State.put_projection(%ListingProjection{filtered_empty?: true})

    assert state.sort == nil
    assert state.tasks_empty?
    assert state.tasks_filtered_empty?
    assert State.clear_results(state).tasks_empty?
    refute State.clear_results(state).tasks_filtered_empty?
  end

  test "location changes clear expansion and projection metadata while same location preserves them" do
    projection = %ListingProjection{
      rows: [
        %ListingRow{task_with_location: %TaskWithLocation{task: %Task{id: 1}, location_path: []}}
      ],
      expandable_root_ids: MapSet.new([1]),
      expanded_task_ids: MapSet.new([1, 2, 3]),
      matching_empty?: false
    }

    state =
      State.new([:pending]) |> State.for_location({1, 2}) |> State.put_projection(projection)

    assert state.rendered_task_ids == MapSet.new([1])
    assert state.expanded_task_ids == MapSet.new([1, 2, 3])
    assert State.for_location(state, {2, 2}).expanded_task_ids == MapSet.new()
    assert State.for_location(state, {1, 2}) == state
    changed = State.for_location(state, {1, nil})
    assert changed.location_key == {1, nil}
    assert changed.expanded_task_ids == MapSet.new()
    assert changed.expandable_root_ids == MapSet.new()
    assert changed.rendered_task_ids == MapSet.new()
    pruned = State.put_projection(state, %ListingProjection{})
    assert pruned.expanded_task_ids == MapSet.new()
    assert pruned.tasks_empty?
    cleared = State.clear_results(state)
    assert cleared.location_key == nil
    assert cleared.expanded_task_ids == MapSet.new()
    assert cleared.expandable_root_ids == MapSet.new()
    assert cleared.rendered_task_ids == MapSet.new()
    assert cleared.visible_statuses == [:pending]
  end
end
