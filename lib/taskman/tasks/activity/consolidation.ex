defmodule Taskman.Tasks.Activity.Consolidation do
  @moduledoc false

  alias Taskman.Tasks.Activity.Event

  @type action :: :insert | :update | :delete
  @type candidate :: %{
          required(:actor_user_id) => Ecto.UUID.t() | nil,
          required(:before_value) => Event.value_container(),
          required(:after_value) => Event.value_container(),
          required(:last_changed_at) => DateTime.t(),
          optional(atom()) => term()
        }

  # One minute in microseconds, measured from the event's latest change.
  @window_microseconds 60_000_000

  @doc "Chooses consolidation within an inclusive sliding minute for continuous, same-actor edits."
  @spec action(candidate() | nil, Ecto.UUID.t() | nil, Event.value(), Event.value(), DateTime.t()) ::
          action()
  def action(nil, _actor_id, _before, _after, _now), do: :insert

  def action(candidate, actor_id, before_value, after_value, now) do
    elapsed = DateTime.diff(now, candidate.last_changed_at, :microsecond)

    if not is_nil(actor_id) and candidate.actor_user_id == actor_id and
         canonical(candidate.after_value["value"]) == canonical(before_value) and
         elapsed >= 0 and elapsed <= @window_microseconds do
      if canonical(candidate.before_value["value"]) == canonical(after_value),
        do: :delete,
        else: :update
    else
      :insert
    end
  end

  @doc "Compares Task/location identity without historical labels; other values pass through."
  @spec canonical(value) :: value | {:task, term()} | {:location, term(), term()}
        when value: term()
  def canonical(%{"id" => id}), do: {:task, id}
  def canonical(%{"list_id" => id, "project_id" => project_id}), do: {:location, project_id, id}
  def canonical(value), do: value
end
