defmodule Taskman.Tasks.Activity.Event do
  alias Taskman.Tasks.Task

  @moduledoc false
  use Ecto.Schema

  @typedoc "Historical Task snapshot with string keys: id, project_id, project_name, and title."
  @type task_reference :: %{String.t() => pos_integer() | String.t()}
  @typedoc "Historical location with string keys: project_id, project_name, list_id, and path."
  @type location :: %{String.t() => pos_integer() | String.t() | nil | [String.t()]}
  @type value :: nil | boolean() | String.t() | task_reference() | location()
  @typedoc "A stored value wrapped under the string key value."
  @type value_container :: %{String.t() => value()}
  @typedoc """
  Historical subject and endpoint snapshots used to describe an event in Task feeds.

  The stored JSON uses these string keys:

  - `"task"` is always present and identifies the event's owning Task (`task_id`).
    For a parent change this is the child; for a blocking change it is the blocker.
  - `"blocking_task"` is present for blocking changes and repeats the blocker snapshot.
  - `"blocked_task"` is present for blocking changes and identifies the blocked Task.

  Each value is a `task_reference()` snapshot. These snapshots let parent feeds name
  the child and blocking feeds name both endpoints, while `before_value`/`after_value`
  hold the changed parents or link-presence booleans. Consolidation refreshes the
  context snapshots; later unrelated renames do not rewrite their stored labels.
  """
  @type stored_context :: %{String.t() => task_reference()}
  @type t :: %__MODULE__{
          id: pos_integer() | nil,
          task_id: pos_integer() | nil,
          actor_user_id: Ecto.UUID.t() | nil,
          grouping_key: String.t() | nil,
          kind: String.t() | nil,
          field: String.t() | nil,
          before_value: value_container() | nil,
          after_value: value_container() | nil,
          context: stored_context() | nil,
          first_changed_at: DateTime.t() | nil,
          last_changed_at: DateTime.t() | nil
        }

  schema "task_activity_events" do
    belongs_to :task, Task
    field :actor_user_id, Ecto.UUID
    field :grouping_key, :string
    field :kind, :string
    field :field, :string
    field :before_value, :map
    field :after_value, :map
    field :context, :map
    field :first_changed_at, :utc_datetime_usec
    field :last_changed_at, :utc_datetime_usec
  end
end
