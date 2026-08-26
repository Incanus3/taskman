defmodule Taskman.Tasks.TaskWithLocation do
  alias Taskman.Lists.TaskList
  alias Taskman.Tasks.Task

  @enforce_keys [:task, :location_path]
  defstruct [:task, :location_path]

  @type t :: %__MODULE__{task: Task.t(), location_path: [TaskList.t()]}
end
