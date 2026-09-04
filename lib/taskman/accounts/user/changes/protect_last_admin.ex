defmodule Taskman.Accounts.User.Changes.ProtectLastAdmin do
  @moduledoc false

  use Ash.Resource.Change

  alias Taskman.Accounts.{Administration, ApiKey, Token, User}
  alias Taskman.Accounts.ApiKey.Persistence, as: ApiKeyPersistence
  alias Taskman.Accounts.Token.Persistence, as: TokenPersistence

  @impl true
  def change(changeset, opts, context) do
    mode = Keyword.fetch!(opts, :mode)

    changeset =
      Ash.Changeset.before_action(changeset, fn changeset ->
        actor = get_in(changeset.context, [:private, :actor]) || context.actor
        protect(changeset, mode, actor)
      end)

    if mode in [:disable, :admin_delete, :self_delete, :sessions] do
      Ash.Changeset.after_transaction(changeset, fn changeset, result ->
        broadcast_revoked_sessions(changeset, result)
      end)
    else
      changeset
    end
  end

  defp protect(changeset, mode, actor) do
    with {:ok, locked_actor, target, active_administrators} <-
           Administration.lock_actor_target_and_active_administrators(actor, changeset.data.id),
         :ok <- authorize(locked_actor, target, mode),
         :ok <- validate_target_transition(target, mode),
         :ok <- protect_final_administrator(target, active_administrators, mode),
         {:ok, revoked_session_jtis} <- revoke_credentials(target, mode) do
      %{changeset | data: target}
      |> Ash.Changeset.put_context(:revoked_session_jtis, revoked_session_jtis)
    else
      :error -> unavailable(changeset)
      {:error, :forbidden} -> forbidden(changeset)
      {:error, :invalid_transition} -> invalid_transition(changeset)
      {:error, :final_administrator} -> final_administrator(changeset)
      {:error, :credential_revocation_failed} -> credential_revocation_failed(changeset)
    end
  end

  defp authorize(actor, target, :self_delete) do
    if actor.id == target.id and target.status == :active, do: :ok, else: {:error, :forbidden}
  end

  defp authorize(actor, target, :admin_delete) do
    if Administration.active_administrator?(actor) and actor.id != target.id,
      do: :ok,
      else: {:error, :forbidden}
  end

  defp authorize(actor, _target, _mode) do
    if Administration.active_administrator?(actor), do: :ok, else: {:error, :forbidden}
  end

  defp validate_target_transition(%User{status: :disabled}, :enable), do: :ok
  defp validate_target_transition(%User{status: :active}, :disable), do: :ok
  defp validate_target_transition(%User{admin?: false}, :promote), do: :ok
  defp validate_target_transition(%User{admin?: true}, :demote), do: :ok
  defp validate_target_transition(%User{status: :pending}, :resend_invitation), do: :ok
  defp validate_target_transition(%User{status: :pending}, :revoke_invitation), do: :ok

  defp validate_target_transition(_target, mode)
       when mode in [:sessions, :api_keys, :admin_delete, :self_delete],
       do: :ok

  defp validate_target_transition(_target, _mode), do: {:error, :invalid_transition}

  defp protect_final_administrator(target, active_administrators, mode)
       when mode in [:demote, :disable, :admin_delete, :self_delete] do
    if target.status == :active and target.admin? and length(active_administrators) <= 1,
      do: {:error, :final_administrator},
      else: :ok
  end

  defp protect_final_administrator(_target, _active_administrators, _mode), do: :ok

  defp revoke_credentials(target, :disable) do
    with {:ok, jtis} <- delete_tokens(target, nil),
         :ok <- revoke_api_keys(target) do
      {:ok, jtis}
    end
  end

  defp revoke_credentials(target, mode) when mode in [:admin_delete, :self_delete] do
    with {:ok, jtis} <- delete_tokens(target, nil),
         :ok <- delete_api_keys(target) do
      {:ok, jtis}
    end
  end

  defp revoke_credentials(target, :sessions),
    do: delete_tokens(target, Token.browser_session_purposes())

  defp revoke_credentials(target, :api_keys) do
    with :ok <- revoke_api_keys(target), do: {:ok, []}
  end

  defp revoke_credentials(_target, _mode), do: {:ok, []}

  defp delete_tokens(user, purposes) do
    case TokenPersistence.delete_for_subject(user, purposes) do
      {:ok, deleted_tokens} ->
        revoked_session_jtis =
          for {jti, purpose} <- deleted_tokens,
              purpose in Token.browser_session_purposes(),
              do: jti

        {:ok, revoked_session_jtis}

      {:error, _reason} ->
        {:error, :credential_revocation_failed}
    end
  end

  defp revoke_api_keys(user) do
    case ApiKey.revoke_all_for_user(user) do
      :ok -> :ok
      {:error, _reason} -> {:error, :credential_revocation_failed}
    end
  end

  defp delete_api_keys(user) do
    case ApiKeyPersistence.delete_all_for_user(user) do
      :ok -> :ok
      {:error, _reason} -> {:error, :credential_revocation_failed}
    end
  end

  defp broadcast_revoked_sessions(changeset, {:ok, _result} = result) do
    case Token.broadcast_session_jtis(changeset.context[:revoked_session_jtis] || []) do
      :ok -> result
      {:error, _reason} = error -> error
    end
  end

  defp broadcast_revoked_sessions(_changeset, result), do: result

  defp unavailable(changeset),
    do: Ash.Changeset.add_error(changeset, field: :id, message: "is no longer available")

  defp forbidden(changeset),
    do: Ash.Changeset.add_error(changeset, field: :id, message: "is not permitted")

  defp invalid_transition(changeset) do
    Ash.Changeset.add_error(changeset,
      field: :id,
      message: "does not allow that lifecycle transition"
    )
  end

  defp final_administrator(changeset) do
    Ash.Changeset.add_error(changeset,
      field: :admin?,
      message: "cannot remove the final active administrator"
    )
  end

  defp credential_revocation_failed(changeset),
    do:
      Ash.Changeset.add_error(changeset, field: :id, message: "credentials could not be revoked")
end
