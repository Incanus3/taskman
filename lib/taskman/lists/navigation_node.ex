defmodule Taskman.Lists.NavigationNode do
  @enforce_keys [:dom_id, :kind, :depth, :project, :expanded?, :expandable?, :selected?]
  defstruct [
    :dom_id,
    :kind,
    :depth,
    :project,
    :task_list,
    :list_kind,
    :icon,
    :expanded?,
    :expandable?,
    :selected?
  ]

  @type t :: %__MODULE__{
          dom_id: String.t(),
          kind: :list,
          depth: pos_integer(),
          project: Taskman.Projects.Project.t(),
          task_list: Taskman.Lists.TaskList.t(),
          list_kind: :leaf | :child_only | :mixed,
          icon: String.t(),
          expanded?: boolean(),
          expandable?: boolean(),
          selected?: boolean()
        }
end
