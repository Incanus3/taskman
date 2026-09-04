defmodule Taskman.Accounts.User.Changes.RequireActiveAdministrator do
  @moduledoc false

  use Ash.Resource.Change

  alias Taskman.Accounts.Administration

  @impl true
  def change(changeset, _opts, context) do
    Ash.Changeset.before_action(changeset, fn changeset ->
      actor = get_in(changeset.context, [:private, :actor]) || context.actor

      case actor do
        %{accounts_bootstrap?: true} ->
          changeset

        actor ->
          case Administration.lock_active_administrator(actor) do
            {:ok, _persisted_actor} ->
              changeset

            :error ->
              Ash.Changeset.add_error(changeset, field: :id, message: "is not permitted")
          end
      end
    end)
  end
end
