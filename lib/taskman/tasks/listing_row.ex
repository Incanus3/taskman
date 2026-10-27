defmodule Taskman.Tasks.ListingRow do
  @moduledoc "A Task listing row with contextual hierarchy and disclosure state."

  alias Taskman.Tasks.TaskWithLocation

  @enforce_keys [:task_with_location]
  defstruct [:task_with_location, depth: 0, expandable?: false, expanded?: false]

  @type t :: %__MODULE__{
          task_with_location: TaskWithLocation.t(),
          depth: non_neg_integer(),
          expandable?: boolean(),
          expanded?: boolean()
        }
end
