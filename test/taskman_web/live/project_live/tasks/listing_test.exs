defmodule TaskmanWeb.ProjectLive.Tasks.ListingTest do
  use ExUnit.Case, async: true

  alias Taskman.Tasks.Task
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
      |> State.put_results([], true)

    assert state.sort == nil
    assert state.tasks_empty?
    assert state.tasks_filtered_empty?
    assert State.clear_results(state).tasks_empty?
    refute State.clear_results(state).tasks_filtered_empty?
  end
end
