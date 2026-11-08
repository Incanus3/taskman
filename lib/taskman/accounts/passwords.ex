defmodule Taskman.Accounts.Passwords do
  @moduledoc false

  alias AshAuthentication.{Info, Jwt, Strategy}

  alias Taskman.Accounts.{
    Authentication,
    Delivery,
    Emails,
    RateLimit,
    SecurityLog,
    Sessions,
    Token,
    User
  }

  @spec change_password(User.t(), map()) :: {:ok, User.t()} | {:error, term()}
  def change_password(actor, params) when is_map(params) do
    result =
      with {:ok, actor} <- Authentication.current_api_key_actor(actor) do
        update_user(actor, :change_password, params, actor: actor)
      end

    SecurityLog.audit(
      result,
      :password_changed,
      :password_change_rejected,
      actor,
      result_user(result, actor)
    )
  end

  def change_password(actor, _params) do
    result = {:error, :invalid_input}
    SecurityLog.audit(result, :password_changed, :password_change_rejected, actor, actor)
  end

  @spec change_password(User.t(), String.t(), map()) ::
          {:ok, %{user: User.t(), replacement_session: String.t()}} | {:error, term()}
  def change_password(actor, acting_token, params)
      when is_binary(acting_token) and is_map(params) do
    result =
      with {:ok, actor} <- Authentication.current_api_key_actor(actor) do
        transact(fn -> replace_password_session(actor, acting_token, params) end)
        |> password_session_replacement_result()
      end

    SecurityLog.audit(
      result,
      :password_changed,
      :password_change_rejected,
      actor,
      result_user(result, actor)
    )
  end

  def change_password(actor, _acting_token, _params) do
    result = {:error, :invalid_input}
    SecurityLog.audit(result, :password_changed, :password_change_rejected, actor, actor)
  end

  @spec request_password_reset(String.t(), keyword()) ::
          :ok | {:error, retry_after: pos_integer()}
  def request_password_reset(email, opts \\ [])

  def request_password_reset(email, opts) when is_binary(email) and is_list(opts) do
    normalized_email = RateLimit.normalized_email(email)

    remote_ip =
      opts
      |> Keyword.get(:remote_ip, RateLimit.request_remote_ip())
      |> RateLimit.normalized_ip()

    with :ok <-
           RateLimit.check(
             :password_reset,
             email: normalized_email,
             remote_ip: remote_ip
           ) do
      transact(fn -> issue_password_reset(normalized_email) end)
      |> password_reset_request_result()
    end
  end

  def request_password_reset(_email, _opts), do: :ok

  @spec reset_password(String.t(), map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def reset_password(token, params, opts \\ [])

  def reset_password(token, params, opts) when is_binary(token) and is_map(params) do
    {result, target} =
      case Authentication.token_user(token) do
        {:ok, user} ->
          result =
            transact(fn -> redeem_password_reset(user, token, params, opts) end)
            |> broadcast_password_reset_sessions()

          {result, user}

        error ->
          {error, nil}
      end

    SecurityLog.audit(
      result,
      :password_reset,
      :password_reset_rejected,
      nil,
      result_user(result, target)
    )
  end

  def reset_password(_token, _params, _opts) do
    result = {:error, :invalid_input}
    SecurityLog.audit(result, :password_reset, :password_reset_rejected)
  end

  defp lock_user(id) do
    case User.Persistence.lock(id) do
      {:ok, user} -> {:ok, user}
      :error -> {:error, :invalid_token}
    end
  end

  defp lock_eligible_user_by_email(email) do
    case User.Persistence.lock_eligible_by_email(email) do
      {:ok, user} -> {:ok, user}
      :error -> {:error, :ineligible_account}
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

  defp replace_password_session(actor, acting_token, params) do
    with {:ok, locked_actor} <- lock_user(actor.id),
         {:ok, updated_user} <-
           update_user(locked_actor, :change_password, params, actor: actor),
         {:ok, replacement_session, peer_jtis} <-
           Sessions.replace_acting_browser_session(
             updated_user,
             acting_token
           ) do
      {:ok,
       %{
         user: updated_user,
         replacement_session: replacement_session,
         peer_jtis: peer_jtis
       }}
    end
  end

  defp issue_password_reset(normalized_email) do
    with {:ok, user} <- lock_eligible_user_by_email(normalized_email),
         :ok <- Token.revoke_for_subject(user, "password_reset"),
         {:ok, token, _claims} <-
           Jwt.token_for_user(
             user,
             %{"act" => "reset_password"},
             token_lifetime: {1, :hours},
             purpose: :password_reset
           ) do
      {:ok, {user, token}}
    end
  end

  defp redeem_password_reset(user, token, params, opts) do
    with {:ok, locked_user} <- lock_user(user.id),
         true <- Authentication.eligible?(locked_user),
         {:ok, _stored_token} <-
           Token.claim_for_redemption(token, "password_reset", now(opts)),
         {:ok, updated_user} <-
           Strategy.action(
             Info.strategy!(User, :password),
             :reset,
             Map.put(params, "reset_token", token),
             domain: Taskman.Accounts
           ),
         {:ok, revoked_jtis} <-
           Token.revoke_browser_sessions_with_jtis(updated_user, opts) do
      {:ok, {updated_user, revoked_jtis}}
    else
      false -> {:error, :invalid_token}
      {:error, _reason} = error -> error
      _ -> {:error, :invalid_token}
    end
  end

  defp deliver_password_reset(user, token) do
    case Emails.deliver_password_reset(to_string(user.email), token) do
      :ok ->
        :ok

      {:error, _reason} = delivery_error ->
        Delivery.log_failure(delivery_error)
        :ok
    end
  end

  defp broadcast_password_reset_sessions({:ok, {updated_user, revoked_jtis}}) do
    case Token.broadcast_session_jtis(revoked_jtis) do
      :ok -> {:ok, updated_user}
      {:error, _reason} = error -> error
    end
  end

  defp broadcast_password_reset_sessions({:error, _reason} = error), do: error

  defp broadcast_replaced_password_session(result, peer_jtis) do
    with :ok <- Token.broadcast_session_jtis(peer_jtis) do
      {:ok, Map.delete(result, :peer_jtis)}
    end
  end

  defp password_session_replacement_result(result) do
    case result do
      {:ok, %{peer_jtis: peer_jtis} = result} ->
        broadcast_replaced_password_session(result, peer_jtis)

      {:error, _reason} = error ->
        error
    end
  end

  defp password_reset_request_result(result) do
    case result do
      {:ok, {user, token}} ->
        deliver_password_reset(user, token)

      {:error, _reason} ->
        :ok
    end
  end
end
