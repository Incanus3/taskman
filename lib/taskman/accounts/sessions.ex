defmodule Taskman.Accounts.Sessions do
  @moduledoc false

  alias AshAuthentication.Jwt
  alias Taskman.Accounts.{Authentication, SecurityLog, Token, User}
  alias Taskman.Accounts.Token.Persistence, as: TokenPersistence

  @spec account_settings_state(User.t()) ::
          {:ok, %{user: User.t(), pending_email: String.t() | nil}} | {:error, term()}
  def account_settings_state(actor) do
    with {:ok, actor} <- Authentication.current_api_key_actor(actor) do
      {:ok,
       %{
         user: actor,
         pending_email: TokenPersistence.pending_email_change(actor, DateTime.utc_now())
       }}
    end
  end

  @spec list_sessions(User.t(), keyword()) :: {:ok, [map()]} | {:error, term()}
  def list_sessions(actor, opts \\ [])

  def list_sessions(actor, opts) when is_list(opts) do
    with {:ok, actor} <- Authentication.current_api_key_actor(actor) do
      now = Keyword.get(opts, :now, DateTime.utc_now())
      {:ok, TokenPersistence.list_browser_sessions(actor, now)}
    end
  end

  def list_sessions(_actor, _opts), do: {:error, :invalid_input}

  @spec revoke_session(User.t(), String.t()) :: :ok | {:error, term()}
  def revoke_session(actor, jti) when is_binary(jti) do
    result =
      with {:ok, actor} <- Authentication.current_api_key_actor(actor),
           true <- TokenPersistence.browser_session_exists?(actor, jti),
           :ok <-
             Token.revoke_jti(jti, AshAuthentication.user_to_subject(actor), disconnect?: true) do
        :ok
      else
        false -> {:error, :not_found}
        {:error, _reason} = error -> error
      end

    SecurityLog.audit(result, :session_revoked, :session_revocation_rejected, actor, actor)
  end

  def revoke_session(actor, _jti) do
    result = {:error, :invalid_input}
    SecurityLog.audit(result, :session_revoked, :session_revocation_rejected, actor, actor)
  end

  @spec revoke_other_sessions(User.t(), String.t()) :: :ok | {:error, term()}
  def revoke_other_sessions(actor, acting_token) when is_binary(acting_token) do
    result =
      with {:ok, actor} <- Authentication.current_api_key_actor(actor),
           {:ok, _stored_token, %{"jti" => acting_jti, "sub" => subject}} <-
             Token.claim_browser_session(acting_token),
           true <- subject == AshAuthentication.user_to_subject(actor),
           :ok <- Token.revoke_for_subject_except(actor, "user", acting_jti) do
        :ok
      else
        false -> {:error, :invalid_session}
        {:error, _reason} = error -> error
        _ -> {:error, :invalid_session}
      end

    SecurityLog.audit(
      result,
      :other_sessions_revoked,
      :other_sessions_revocation_rejected,
      actor,
      actor
    )
  end

  def revoke_other_sessions(actor, _acting_token) do
    result = {:error, :invalid_input}

    SecurityLog.audit(
      result,
      :other_sessions_revoked,
      :other_sessions_revocation_rejected,
      actor,
      actor
    )
  end

  @doc false
  @spec replace_acting_browser_session(User.t(), String.t()) ::
          {:ok, String.t(), [String.t()]} | {:error, term()}
  def replace_acting_browser_session(user, acting_token) do
    subject = AshAuthentication.user_to_subject(user)

    with {:ok, acting_record, %{"jti" => acting_jti, "sub" => ^subject}} <-
           Token.claim_browser_session(acting_token),
         true <- acting_record.subject == subject,
         {:ok, peer_jtis} <-
           Token.revoke_for_subject_except_with_jtis(
             user,
             "user",
             acting_jti,
             disconnect?: false
           ),
         {:ok, replacement_session, _claims} <-
           Jwt.token_for_user(user, %{"purpose" => "user"}, purpose: :user),
         :ok <- Token.revoke_jti(acting_jti, subject, disconnect?: false) do
      {:ok, replacement_session, peer_jtis}
    else
      false -> {:error, :invalid_session}
      {:error, _reason} = error -> error
      _ -> {:error, :invalid_session}
    end
  end
end
