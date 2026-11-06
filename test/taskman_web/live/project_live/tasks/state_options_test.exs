defmodule TaskmanWeb.ProjectLive.Tasks.StateOptionsTest do
  use ExUnit.Case, async: true

  alias Taskman.Lists.TaskList
  alias Taskman.Tasks.Status
  alias TaskmanWeb.ProjectLive.Locations
  alias TaskmanWeb.ProjectLive.TaskTablePreferences, as: Preferences
  alias TaskmanWeb.ProjectLive.Tasks.StateOptions

  test "remembered absent state survives an empty checkbox submission" do
    assert StateOptions.merge_selection([:pending, :not_planned], [:pending], []) == [
             :not_planned
           ]
  end

  test "checkbox submission updates only offered states in canonical order" do
    assert StateOptions.merge_selection([:not_planned], [:pending], ["pending", "done", "bad"]) ==
             [:pending, :not_planned]

    assert StateOptions.merge_selection(["not_planned", :pending], ["pending"], [""]) == [
             :not_planned
           ]
  end

  test "empty URL replacement clears the complete remembered selection" do
    saved = %{include_children: false, statuses: [:pending, :not_planned]}
    assert Preferences.apply_route(saved, %{"statuses" => ""}).statuses == []
  end

  test "forms use direct List scope and whole Project scope, retaining current drafts" do
    lists = [%TaskList{id: 2, project_id: 1}, %TaskList{id: 3, project_id: 1, parent_list_id: 2}]
    locations = Locations.load(Locations.new(), 1, lists, %{3 => [:not_planned]})

    assert StateOptions.for_form(locations, 1, 2, :pending, false) == %{
             values: Status.default_values(),
             hidden?: true
           }

    assert StateOptions.for_form(locations, 1, nil, :pending, false) == %{
             values: Status.values(),
             hidden?: false
           }

    assert StateOptions.for_form(locations, 1, 2, "not_planned", false) == %{
             values: Status.values(),
             hidden?: false
           }

    assert StateOptions.for_form(locations, 1, 2, :pending, true) == %{
             values: Status.values(),
             hidden?: false
           }

    refute :unknown in StateOptions.for_form(locations, 1, 2, :unknown, false).values
  end
end
