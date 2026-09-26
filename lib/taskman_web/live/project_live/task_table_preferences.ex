defmodule TaskmanWeb.ProjectLive.TaskTablePreferences do
  @moduledoc "Normalizes Task table preferences and explicit route snapshots."

  import Phoenix.Component, only: [assign: 3]
  import Phoenix.LiveView, only: [push_event: 3]

  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Tasks.Listing
  alias TaskmanWeb.ProjectLive.Tasks.Listing.State, as: ListingState

  def defaults, do: %{include_children: false, statuses: Task.statuses() -- [:will_not_do]}

  def normalize_statuses(statuses) when is_list(statuses) do
    Task.statuses()
    |> Enum.filter(fn status -> Atom.to_string(status) in statuses end)
  end

  def route_params(params, nil), do: Map.take(params, ["include_children", "statuses"])

  def route_params(_params, uri) do
    # Plug collapses duplicate keys; preserve their cardinality to match the browser snapshot.
    (URI.parse(uri).query || "")
    |> URI.query_decoder()
    |> Enum.filter(fn {key, _value} -> key in ["include_children", "statuses"] end)
    |> Enum.group_by(&elem(&1, 0), &elem(&1, 1))
    |> Enum.flat_map(fn
      {key, [value]} -> [{key, value}]
      _duplicate -> []
    end)
    |> Map.new()
  end

  def apply_route(preferences, params) do
    preferences
    |> apply_include_param(params)
    |> apply_status_param(params)
  end

  def apply_hydration(preferences, params) do
    preferences
    |> apply_boolean_param(params)
    |> apply_status_list_param(params)
  end

  def current(socket) do
    %{
      include_children: socket.assigns.workspace.include_children?,
      statuses: socket.assigns.listing.visible_statuses
    }
  end

  def assign_preferences(socket, preferences, opts \\ []) do
    if current(socket) == preferences do
      socket
    else
      workspace = %{socket.assigns.workspace | include_children?: preferences.include_children}

      listing =
        socket.assigns.listing
        |> ListingState.apply_statuses(Enum.map(preferences.statuses, &Atom.to_string/1))
        |> ListingState.available_sort(preferences.include_children)

      socket =
        socket
        |> assign(:workspace, workspace)
        |> assign(:listing, listing)

      if Keyword.get(opts, :refresh?, true), do: Listing.refresh(socket), else: socket
    end
  end

  def changed(socket) do
    preferences = current(socket)

    push_event(socket, "task_table_preferences_changed", %{
      include_children: preferences.include_children,
      statuses: Enum.map(preferences.statuses, &Atom.to_string/1)
    })
  end

  defp apply_include_param(preferences, %{"include_children" => value})
       when is_binary(value),
       do: %{preferences | include_children: value == "true"}

  defp apply_include_param(preferences, _params), do: preferences

  defp apply_status_param(preferences, %{"statuses" => value}) when is_binary(value),
    do: %{preferences | statuses: value |> String.split(",") |> normalize_statuses()}

  defp apply_status_param(preferences, _params), do: preferences

  defp apply_boolean_param(preferences, %{"include_children" => value})
       when is_boolean(value),
       do: %{preferences | include_children: value}

  defp apply_boolean_param(preferences, _params), do: preferences

  defp apply_status_list_param(preferences, %{"statuses" => statuses})
       when is_list(statuses),
       do: %{preferences | statuses: normalize_statuses(statuses)}

  defp apply_status_list_param(preferences, _params), do: preferences
end
