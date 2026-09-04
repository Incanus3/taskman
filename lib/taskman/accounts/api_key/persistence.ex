defmodule Taskman.Accounts.ApiKey.Persistence do
  @moduledoc false

  import Ecto.Query, only: [from: 2]

  alias Taskman.Accounts.{ApiKey, User}
  alias Taskman.Repo

  @doc false
  @spec mark_all_revoked_for_user(User.t(), DateTime.t()) ::
          :ok | {:error, :api_key_revocation_failed}
  def mark_all_revoked_for_user(%User{} = user, revoked_at) do
    Repo.update_all(
      from(api_key in ApiKey,
        where: api_key.user_id == ^user.id and is_nil(api_key.revoked_at)
      ),
      set: [revoked_at: revoked_at]
    )

    :ok
  rescue
    _exception -> {:error, :api_key_revocation_failed}
  end

  @doc false
  @spec delete_all_for_user(User.t()) :: :ok | {:error, :api_key_deletion_failed}
  def delete_all_for_user(%User{} = user) do
    Repo.delete_all(from api_key in ApiKey, where: api_key.user_id == ^user.id)
    :ok
  rescue
    _exception -> {:error, :api_key_deletion_failed}
  end
end
