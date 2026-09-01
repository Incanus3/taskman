defmodule Taskman.ChangeNotifications.Event do
  @moduledoc """
  Internal invalidation metadata sent through `Taskman.PubSub`.
  """

  @enforce_keys [:entity, :operation, :project_id, :entity_id, :fields]
  defstruct [:entity, :operation, :project_id, :entity_id, :lock_version, :fields]

  @type t :: %__MODULE__{
          entity: :project | :list | :task,
          operation: :created | :updated | :moved,
          project_id: pos_integer(),
          entity_id: pos_integer(),
          lock_version: non_neg_integer() | nil,
          fields: [atom()]
        }
end
