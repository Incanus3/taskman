defmodule Taskman.Accounts.AccountClosure do
  @moduledoc false

  alias Taskman.Accounts.{SecurityLog, User}

  @spec delete_user(User.t(), User.t(), String.t()) :: :ok | {:error, term()}
  def delete_user(actor, user, confirmation)
      when is_struct(actor, User) and is_struct(user, User) and is_binary(confirmation) do
    SecurityLog.record(:account_deletion_attempted,
      actor_id: record_id(actor),
      target_id: record_id(user)
    )

    result =
      user
      |> Ash.Changeset.for_destroy(:admin_delete, %{confirmation: confirmation})
      |> Ash.destroy(actor: actor, authorize?: true, domain: Taskman.Accounts)
      |> normalize_destroy_result()

    SecurityLog.audit(result, :account_deleted, :account_deletion_rejected, actor, user)
  end

  def delete_user(_actor, _user, _confirmation), do: {:error, :invalid_input}

  @spec delete_user(User.t(), User.t()) :: :ok | {:error, term()}
  def delete_user(actor, user), do: delete_user(actor, user, "DELETE")

  @spec delete_own_account(User.t(), String.t()) :: :ok | {:error, term()}
  def delete_own_account(actor, current_password)
      when is_struct(actor, User) and is_binary(current_password) do
    SecurityLog.record(:account_deletion_attempted,
      actor_id: record_id(actor),
      target_id: record_id(actor)
    )

    result =
      actor
      |> Ash.Changeset.for_destroy(:self_delete, %{current_password: current_password})
      |> Ash.destroy(actor: actor, authorize?: true, domain: Taskman.Accounts)
      |> normalize_destroy_result()

    SecurityLog.audit(result, :account_deleted, :account_deletion_rejected, actor, actor)
  end

  def delete_own_account(_actor, _current_password), do: {:error, :invalid_input}

  defp normalize_destroy_result(:ok), do: :ok
  defp normalize_destroy_result({:ok, _user}), do: :ok
  defp normalize_destroy_result({:error, _reason} = error), do: error

  defp record_id(%User{id: id}) when is_binary(id), do: id
  defp record_id(%{id: id}) when is_binary(id), do: id
  defp record_id(_record), do: nil
end
