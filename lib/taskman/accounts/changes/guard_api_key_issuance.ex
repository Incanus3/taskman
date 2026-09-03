defmodule Taskman.Accounts.Changes.GuardApiKeyIssuance do
  @moduledoc false

  use Ash.Resource.Change

  import Ecto.Query, only: [from: 2]

  alias Taskman.Accounts.User
  alias Taskman.Repo

  @impl true
  def change(changeset, _opts, context) do
    Ash.Changeset.before_action(changeset, fn changeset ->
      with user_id when is_binary(user_id) <- Ash.Changeset.get_attribute(changeset, :user_id),
           %User{} = user <-
             Repo.one(from user in User, where: user.id == ^user_id, lock: "FOR UPDATE"),
           %User{id: actor_id} <- get_in(changeset.context, [:private, :actor]) || context.actor,
           true <- actor_id == user.id and eligible?(user) do
        changeset
      else
        _ ->
          Ash.Changeset.add_error(changeset,
            field: :user_id,
            message: "is not eligible for API keys"
          )
      end
    end)
  end

  defp eligible?(%User{status: :active, confirmed_at: %DateTime{}}), do: true
  defp eligible?(_user), do: false
end
