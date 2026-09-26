defmodule TaskmanWeb.ProjectLive.TaskTablePreferencesTest do
  use ExUnit.Case, async: true

  alias TaskmanWeb.ProjectLive.TaskTablePreferences, as: Preferences

  test "normalizes statuses in Task order without accepting unknown or duplicate keys" do
    assert Preferences.normalize_statuses(["done", "pending", "done", "bogus"]) ==
             [:pending, :done]

    assert Preferences.normalize_statuses([]) == []
  end

  test "only present string route parameters override mounted values" do
    previous = %{include_children: true, statuses: [:done]}

    assert Preferences.apply_route(previous, %{}) == previous

    assert Preferences.apply_route(previous, %{"include_children" => "false"}) ==
             %{include_children: false, statuses: [:done]}

    assert Preferences.apply_route(%{previous | include_children: false}, %{
             "include_children" => "true"
           }) ==
             previous

    assert Preferences.apply_route(previous, %{"include_children" => "1"}) ==
             %{include_children: false, statuses: [:done]}

    assert Preferences.apply_route(previous, %{"statuses" => "pending,done,pending,bogus"}) ==
             %{include_children: true, statuses: [:pending, :done]}

    assert Preferences.apply_route(previous, %{"statuses" => ""}) ==
             %{include_children: true, statuses: []}

    assert Preferences.apply_route(previous, %{
             "include_children" => ["true"],
             "statuses" => ["done"]
           }) ==
             previous
  end
end
