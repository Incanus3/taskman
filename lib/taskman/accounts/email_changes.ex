defmodule Taskman.Accounts.EmailChanges do
  @moduledoc false

  alias AshAuthentication.{Info, Jwt, Strategy}
  alias Taskman.Accounts.{Delivery, RateLimit, SecurityLog, Token, User}
  alias Taskman.Accounts.User.Persistence, as: UserPersistence

  @spec request_email_change(User.t(), String.t(), String.t()) ::
          {:ok, User.t()} | {:error, term()}
  def request_email_change(actor, email, current_password),
    do: request_email_change(actor, email, current_password, [])

  @spec request_email_change(User.t(), String.t(), String.t(), keyword()) ::
          {:ok, User.t()} | {:error, term()}
  def request_email_change(actor, email, current_password, _opts)
      when is_binary(email) and is_binary(current_password) do
    result =
      with :ok <- RateLimit.check(:email_change_resend, email: email, actor_id: actor.id) do
        case transact(fn ->
               with {:ok, locked_user} <- lock_user(actor.id) do
                 Delivery.with_result(
                   :email_change,
                   fn ->
                     update_user(
                       locked_user,
                       :request_email_change,
                       %{email: email, current_password: current_password},
                       actor: actor
                     )
                   end,
                   fn
                     {:ok, user}, delivery_result, token when is_binary(token) ->
                       with :ok <- revoke_older_tokens(user, "email_change", token) do
                         {:ok, {user, delivery_result}}
                       end

                     {:ok, _user}, nil, _token ->
                       {:error, :delivery_failed}

                     error, _delivery_result, _token ->
                       error
                   end
                 )
               end
             end) do
          {:ok, {user, :ok}} ->
            {:ok, user}

          {:ok, {_user, {:error, _reason} = delivery_error}} ->
            Delivery.log_failure(delivery_error)
            {:error, :delivery_failed}

          {:error, _reason} = error ->
            error
        end
      end

    SecurityLog.audit(
      result,
      :email_change_requested,
      :email_change_request_rejected,
      actor,
      result_user(result, actor)
    )
  end

  def request_email_change(actor, _email, _current_password, _opts) do
    result = {:error, :invalid_input}

    SecurityLog.audit(
      result,
      :email_change_requested,
      :email_change_request_rejected,
      actor,
      actor
    )
  end

  @spec confirm_email_change(String.t(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def confirm_email_change(token, opts \\ [])

  def confirm_email_change(token, opts) when is_binary(token) do
    {result, target} =
      case Taskman.Accounts.Authentication.token_user(token) do
        {:ok, user} ->
          result =
            transact(fn ->
              with {:ok, _locked_user} <- lock_user(user.id),
                   {:ok, _stored_token} <-
                     Token.claim_for_redemption(token, "email_change", now(opts)),
                   {:ok, changed_user} <-
                     Strategy.action(
                       Info.strategy!(User, :email_change),
                       :confirm,
                       %{"confirm" => token},
                       domain: Taskman.Accounts
                     ) do
                {:ok, changed_user}
              end
            end)

          {result, user}

        error ->
          {error, nil}
      end

    SecurityLog.audit(
      result,
      :email_change_confirmed,
      :email_change_confirmation_rejected,
      nil,
      result_user(result, target)
    )
  end

  def confirm_email_change(_token, _opts) do
    result = {:error, :invalid_input}
    SecurityLog.audit(result, :email_change_confirmed, :email_change_confirmation_rejected)
  end

  defp lock_user(id) do
    case UserPersistence.lock(id) do
      {:ok, user} -> {:ok, user}
      :error -> {:error, :invalid_token}
    end
  end

  defp revoke_older_tokens(user, purpose, current_token) do
    with {:ok, %{"jti" => current_jti}} <- Jwt.peek(current_token),
         :ok <- Token.revoke_for_subject_except(user, purpose, current_jti) do
      :ok
    else
      _ -> {:error, :token_revocation_failed}
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
end
