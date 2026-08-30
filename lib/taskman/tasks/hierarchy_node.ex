defmodule Taskman.Tasks.HierarchyNode do
  alias Taskman.Lists.TaskList
  alias Taskman.Tasks.Task

  @enforce_keys [:task, :location_path, :children]
  defstruct [:task, :location_path, :children]

  @type t :: %__MODULE__{
          task: Task.t(),
          location_path: [TaskList.t()],
          children: [t()]
        }
end
