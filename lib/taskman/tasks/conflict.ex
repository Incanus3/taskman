defmodule Taskman.Tasks.Conflict do
  @enforce_keys [:task, :fields]
  defstruct [:task, :fields]

  @type t :: %__MODULE__{
          task: Taskman.Tasks.Task.t(),
          fields: [atom()]
        }
end
