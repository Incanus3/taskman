defmodule Taskman.Accounts.Changes.ProtectLastAdmin do
  @moduledoc false

  use Ash.Resource.Change

  import Ecto.Query, only: [from: 2]

  alias Taskman.Accounts.{ApiKey, Token, User}
  alias Taskman.Repo

  @impl true
  def change(changeset, opts, _context) do
    Ash.Changeset.before_action(changeset, fn changeset ->
      case lock_target_and_active_administrators(changeset.data.id) do
        {:ok, target, active_administrators} ->
          if protect_final_administrator?(target, active_administrators, opts) do
            Ash.Changeset.add_error(changeset,
              field: :admin?,
              message: "cannot remove the final active administrator"
            )
          else
            revoke_credentials(target, Keyword.fetch!(opts, :mode))
            changeset
          end

        :error ->
          Ash.Changeset.add_error(changeset, field: :id, message: "is no longer available")
      end
    end)
  end

  defp lock_target_and_active_administrators(id) do
    active_administrators =
      Repo.all(
        from user in User,
          where: user.status == :active and user.admin? == true,
          lock: "FOR UPDATE"
      )

    case Repo.one(from user in User, where: user.id == ^id, lock: "FOR UPDATE") do
      %User{} = target -> {:ok, target, active_administrators}
      nil -> :error
    end
  end

  defp protect_final_administrator?(target, active_administrators, opts) do
    Keyword.fetch!(opts, :mode) in [:demote, :disable, :delete] and
      target.status == :active and target.admin? and length(active_administrators) <= 1
  end

  defp revoke_credentials(target, :disable) do
    delete_tokens(target, nil)
    revoke_api_keys(target)
  end

  defp revoke_credentials(target, :delete) do
    delete_tokens(target, nil)
    delete_api_keys(target)
  end

  defp revoke_credentials(target, :sessions), do: delete_tokens(target, ["user"])
  defp revoke_credentials(target, :api_keys), do: revoke_api_keys(target)
  defp revoke_credentials(_target, :demote), do: :ok

  defp delete_tokens(user, purposes) do
    query = from token in Token, where: token.subject == ^AshAuthentication.user_to_subject(user)
    query = if purposes, do: from(token in query, where: token.purpose in ^purposes), else: query
    Repo.delete_all(query)
    :ok
  end

  defp revoke_api_keys(user) do
    Repo.update_all(
      from(api_key in ApiKey, where: api_key.user_id == ^user.id and is_nil(api_key.revoked_at)),
      set: [revoked_at: DateTime.utc_now()]
    )

    :ok
  end

  defp delete_api_keys(user) do
    Repo.delete_all(from api_key in ApiKey, where: api_key.user_id == ^user.id)
    :ok
  end
end
