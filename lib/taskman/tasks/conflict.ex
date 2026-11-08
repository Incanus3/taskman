defmodule Taskman.Tasks.Conflict do
  @moduledoc "A persisted Task and the fields conflicting with a requested mutation."

  @enforce_keys [:task, :fields]
  defstruct [:task, :fields]

  @type t :: %__MODULE__{
          task: Taskman.Tasks.Task.t(),
          fields: [atom()]
        }
end
