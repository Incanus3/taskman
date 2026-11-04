defmodule TaskmanWeb.ProjectLive.Tasks.StateOptions do
  @moduledoc "Contextual state choices, independent of the complete remembered selection."

  alias Taskman.Tasks.Status
  alias TaskmanWeb.ProjectLive.Locations

  @spec merge_selection([term()], [term()], [term()]) :: [atom()]
  def merge_selection(current, offered, submitted) do
    current = normalize(current)
    offered = normalize(offered)
    submitted = normalize(submitted)

    Enum.filter(Status.values(), fn state ->
      if state in offered, do: state in submitted, else: state in current
    end)
  end

  @spec for_form(Locations.t(), pos_integer(), pos_integer() | nil, term(), boolean()) ::
          %{values: [atom()], hidden?: boolean()}
  def for_form(locations, project_id, list_id, current_state, revealed?) do
    offered =
      if revealed?,
        do: Status.values(),
        else:
          Locations.available(
            locations,
            project_id,
            list_id,
            if(list_id, do: :direct, else: :subtree)
          )

    values = normalize(offered ++ [current_state])
    %{values: values, hidden?: values != Status.values()}
  end

  defp normalize(values) do
    Enum.filter(Status.values(), &(&1 in values or Atom.to_string(&1) in values))
  end
end
