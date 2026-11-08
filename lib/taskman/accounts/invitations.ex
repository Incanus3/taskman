defmodule Taskman.Accounts.Invitations do
  @moduledoc false

  alias AshAuthentication.{AddOn.Confirmation, Info, Strategy}

  alias Taskman.Accounts.{
    Administration,
    Authentication,
    Delivery,
    Emails,
    RateLimit,
    SecurityLog,
    Token,
    User
  }

  @spec invite_user(User.t(), map()) :: {:ok, User.t()} | {:error, term()}
  def invite_user(actor, params) when is_map(params) do
    with {:ok, actor} <- Administration.active_administrator(actor) do
      Delivery.with_result(
        :setup,
        fn ->
          Taskman.Accounts.create_pending_user(params, actor: actor)
        end,
        fn
          {:ok, user}, :ok, _token ->
            {:ok, user}

          {:ok, user}, {:error, _reason} = delivery_error, _token ->
            Delivery.log_failure(delivery_error)
            {:error, {:delivery_failed, user}}

          {:ok, user}, nil, _token ->
            {:error, {:delivery_failed, user}}

          error, _result, _token ->
            error
        end
      )
    end
  end

  def invite_user(_actor, _params), do: {:error, :invalid_input}

  @spec resend_invitation(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def resend_invitation(actor, user) do
    result =
      with {:ok, actor} <- Administration.active_administrator(actor),
           :ok <-
             RateLimit.check(:invitation_resend,
               email: to_string(user.email),
               actor_id: actor.id
             ) do
        transact(fn -> rotate_invitation(actor, user) end)
        |> invitation_resend_result()
      end

    SecurityLog.audit(result, :invitation_resent, :invitation_resend_rejected, actor, user)
  end

  @spec revoke_invitation(User.t(), User.t()) :: :ok | {:error, term()}
  def revoke_invitation(actor, user) do
    with {:ok, actor} <- Administration.active_administrator(actor) do
      transact(fn -> revoke_locked_invitation(actor, user) end)
      |> invitation_revocation_result()
    end
  end

  @spec complete_setup(String.t(), map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def complete_setup(token, params, opts \\ [])

  def complete_setup(token, params, opts) when is_binary(token) and is_map(params) do
    {result, target} =
      case Authentication.token_user(token) do
        {:ok, user} ->
          result =
            transact(fn -> complete_locked_setup(user, token, params, opts) end)

          {result, user}

        error ->
          {error, nil}
      end

    SecurityLog.audit(
      result,
      :setup_completed,
      :setup_completion_rejected,
      nil,
      result_user(result, target)
    )
  end

  def complete_setup(_token, _params, _opts) do
    result = {:error, :invalid_input}
    SecurityLog.audit(result, :setup_completed, :setup_completion_rejected)
  end

  defp lock_user(id) do
    case User.Persistence.lock(id) do
      {:ok, user} -> {:ok, user}
      :error -> {:error, :invalid_token}
    end
  end

  defp setup_token(user) do
    Confirmation.confirmation_token(
      Info.strategy!(User, :setup),
      Ash.Changeset.new(user),
      user,
      domain: Taskman.Accounts
    )
    |> case do
      {:ok, token} -> {:ok, token}
      _ -> {:error, :token_generation_failed}
    end
  end

  defp update_user(user, action, params, opts) do
    user
    |> Ash.Changeset.for_update(action, params)
    |> Ash.update(
      opts
      |> Keyword.put(:domain, Taskman.Accounts)
      |> Keyword.put_new(:authorize?, true)
    )
  end

  defp transact(operation) do
    case Ash.transact([User, Token, Taskman.Accounts.ApiKey], operation) do
      {:ok, {:ok, value}} -> {:ok, value}
      {:error, reason} -> {:error, reason}
    end
  end

  defp result_user({:ok, %User{} = user}, _fallback), do: user
  defp result_user({:ok, %{user: %User{} = user}}, _fallback), do: user
  defp result_user(_result, fallback), do: fallback

  defp now(opts), do: Keyword.get(opts, :now, DateTime.utc_now())

  defp rotate_invitation(actor, user) do
    with {:ok, updated_user} <-
           update_user(user, :resend_invitation, %{}, actor: actor),
         :ok <- Token.revoke_for_subject(updated_user, "setup"),
         {:ok, token} <- setup_token(updated_user) do
      {:ok, {updated_user, token}}
    end
  end

  defp revoke_locked_invitation(actor, user) do
    with {:ok, updated_user} <-
           update_user(user, :revoke_invitation, %{}, actor: actor),
         :ok <- Token.revoke_for_subject(updated_user, "setup") do
      {:ok, :ok}
    end
  end

  defp complete_locked_setup(user, token, params, opts) do
    with {:ok, _locked_user} <- lock_user(user.id),
         {:ok, _stored_token} <- Token.claim_for_redemption(token, "setup", now(opts)) do
      Strategy.action(
        Info.strategy!(User, :setup),
        :confirm,
        Map.put(params, "confirm", token),
        domain: Taskman.Accounts
      )
    end
  end

  defp deliver_rotated_invitation(updated_user, token) do
    case Emails.deliver_invitation(to_string(updated_user.email), token) do
      :ok ->
        {:ok, updated_user}

      {:error, _reason} = delivery_error ->
        Delivery.log_failure(delivery_error)
        {:error, {:delivery_failed, updated_user}}
    end
  end

  defp invitation_resend_result(result) do
    case result do
      {:ok, {updated_user, token}} ->
        deliver_rotated_invitation(updated_user, token)

      {:error, _reason} = error ->
        error
    end
  end

  defp invitation_revocation_result(result) do
    case result do
      {:ok, :ok} -> :ok
      {:error, _reason} = error -> error
    end
  end
end
