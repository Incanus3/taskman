defmodule Taskman.Lists.NavigationNode do
  @enforce_keys [:dom_id, :kind, :depth, :project, :expanded?, :expandable?, :selected?]
  defstruct [
    :dom_id,
    :kind,
    :depth,
    :project,
    :task_list,
    :expanded?,
    :expandable?,
    :selected?
  ]

  @type t :: %__MODULE__{
          dom_id: String.t(),
          kind: :project | :list,
          depth: pos_integer(),
          project: Taskman.Projects.Project.t(),
          task_list: Taskman.Lists.TaskList.t() | nil,
          expanded?: boolean(),
          expandable?: boolean(),
          selected?: boolean()
        }
end
