defmodule Taskman.Accounts.Changes.RequireActiveAdministrator do
  @moduledoc false

  use Ash.Resource.Change

  import Ecto.Query, only: [from: 2]

  alias Taskman.Accounts.User
  alias Taskman.Repo

  @impl true
  def change(changeset, _opts, context) do
    Ash.Changeset.before_action(changeset, fn changeset ->
      actor = get_in(changeset.context, [:private, :actor]) || context.actor

      case actor do
        %{accounts_bootstrap?: true} ->
          changeset

        %User{id: actor_id} ->
          case Repo.one(from user in User, where: user.id == ^actor_id, lock: "FOR UPDATE") do
            %User{status: :active, admin?: true} -> changeset
            _ -> Ash.Changeset.add_error(changeset, field: :id, message: "is not permitted")
          end

        _ ->
          Ash.Changeset.add_error(changeset, field: :id, message: "is not permitted")
      end
    end)
  end
end
