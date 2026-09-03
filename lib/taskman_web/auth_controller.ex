defmodule TaskmanWeb.AuthController do
  @moduledoc """
  HTTP callbacks for AshAuthentication's browser flows.

  Browser authentication stores the complete, revocable AshAuthentication token
  in the signed Phoenix session. The LiveView socket identity is derived from
  the token JTI so that revoking one browser session does not disconnect another
  session belonging to the same user.
  """

  use TaskmanWeb, :controller
  use AshAuthentication.Phoenix.Controller

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.RateLimit
  alias Taskman.Accounts.SecurityLog
  alias Taskman.Accounts.Token
  alias TaskmanWeb.LiveUserAuth

  @doc false
  @impl AshAuthentication.Phoenix.Controller
  def success(conn, activity, user, token) do
    log_authentication_result(activity, :success, user)
    token = token || get_in(user, [Access.key(:__metadata__), Access.key(:token)])

    return_to =
      conn
      |> get_session(:return_to)
      |> Kernel.||(conn.params["return_to"])
      |> LiveUserAuth.safe_return_path()

    case install_browser_session(conn, user, token) do
      {:ok, conn} ->
        conn
        |> delete_session(:return_to)
        |> redirect(to: return_to)

      {:error, _reason} ->
        conn
        |> delete_session(:return_to)
        |> put_flash(:error, "Unable to start a secure browser session.")
        |> redirect(to: LiveUserAuth.sign_in_path(return_to))
    end
  end

  @doc false
  @impl AshAuthentication.Phoenix.Controller
  def failure(conn, activity, reason) do
    log_authentication_result(activity, :rejected, nil)

    case RateLimit.retry_after(reason) do
      retry_after when is_integer(retry_after) ->
        rate_limited_response(conn, retry_after)

      nil ->
        return_to =
          conn
          |> get_session(:return_to)
          |> Kernel.||(conn.params["return_to"])
          |> LiveUserAuth.safe_return_path()

        conn
        |> put_flash(:error, "The email or password is incorrect.")
        |> redirect(to: LiveUserAuth.sign_in_path(return_to))
    end
  end

  @doc false
  @impl AshAuthentication.Phoenix.Controller
  def sign_out(conn, _params) do
    if socket_id = get_session(conn, :live_socket_id) do
      Token.broadcast_disconnect(socket_id)
    end

    conn
    |> clear_session(:taskman)
    |> redirect(to: ~p"/sign-in")
  end

  @doc false
  def update_password(conn, %{"password_change" => params}) when is_map(params) do
    user = conn.assigns[:current_user]
    acting_token = get_session(conn, :user_token)

    with true <- is_struct(user),
         true <- is_binary(acting_token),
         {:ok, %{user: updated_user, replacement_session: replacement_token}} <-
           Accounts.change_password(user, acting_token, params),
         {:ok, conn} <- install_browser_session(conn, updated_user, replacement_token) do
      conn
      |> put_flash(:info, "Password updated.")
      |> redirect(to: ~p"/account/settings")
    else
      _ ->
        conn
        |> put_flash(:error, "Unable to update the password.")
        |> redirect(to: ~p"/account/settings")
    end
  end

  def update_password(conn, _params) do
    conn
    |> put_flash(:error, "Unable to update the password.")
    |> redirect(to: ~p"/account/settings")
  end

  @doc false
  def delete_account(conn, %{"delete_account" => params}) when is_map(params) do
    user = conn.assigns[:current_user]

    with true <- is_struct(user),
         "DELETE" <- Map.get(params, "confirmation"),
         current_password when is_binary(current_password) <- Map.get(params, "current_password"),
         :ok <- Accounts.delete_own_account(user, current_password) do
      conn
      |> clear_session(:taskman)
      |> put_flash(:info, "Account deleted.")
      |> redirect(to: ~p"/sign-in")
    else
      _ ->
        conn
        |> put_flash(:error, "Account deletion was not completed.")
        |> redirect(to: ~p"/account/settings")
    end
  end

  def delete_account(conn, _params) do
    conn
    |> put_flash(:error, "Account deletion was not completed.")
    |> redirect(to: ~p"/account/settings")
  end

  @doc false
  def request_password_reset(conn, %{"user" => %{"email" => email}}) when is_binary(email) do
    case Accounts.request_password_reset(email, remote_ip: conn.remote_ip) do
      :ok -> redirect(conn, to: ~p"/")
      {:error, retry_after: retry_after} -> rate_limited_response(conn, retry_after)
    end
  end

  def request_password_reset(conn, _params), do: redirect(conn, to: ~p"/")

  @doc "Returns the session-specific LiveView socket topic for a browser token."
  @spec session_topic(String.t()) :: String.t()
  def session_topic(token), do: Token.session_topic(token)

  @doc """
  Creates a browser session for a known User record.

  The helper is intentionally small and is also used by `ConnCase` to make
  authenticated LiveView tests explicit. Normal browser sign-in reaches the
  same storage boundary through `success/4`.
  """
  @spec log_in_user(Plug.Conn.t(), Ash.Resource.record()) :: Plug.Conn.t()
  def log_in_user(conn, user) when is_struct(user) do
    {:ok, token, _claims} = Jwt.token_for_user(user, %{}, purpose: :user)

    case install_browser_session(conn, user, token) do
      {:ok, conn} -> conn
      {:error, reason} -> raise "could not establish browser session: #{inspect(reason)}"
    end
  end

  @doc """
  Rotates an acting browser token while preserving that session's connection.

  Other stored browser tokens are revoked (and their socket topics notified),
  then a replacement token is issued. The old acting token is revoked without a
  disconnect broadcast because its socket is the session being preserved.
  """
  @spec replace_session_token(Ash.Resource.record(), String.t()) ::
          {:ok, String.t()} | {:error, term()}
  def replace_session_token(user, acting_token)
      when is_struct(user) and is_binary(acting_token) do
    subject = AshAuthentication.user_to_subject(user)

    case replace_token_transaction(user, subject, acting_token) do
      {:ok, {replacement, peer_jtis}} ->
        case Token.broadcast_session_jtis(peer_jtis) do
          :ok -> {:ok, replacement}
          {:error, _reason} = error -> error
        end

      {:error, _reason} = error ->
        error
    end
  end

  def replace_session_token(_user, _acting_token), do: {:error, :invalid_session}

  defp replace_token_transaction(user, subject, acting_token) do
    case Ash.transact([Taskman.Accounts.Token], fn ->
           with {:ok, acting_record, claims} <- Token.claim_browser_session(acting_token),
                %{"jti" => acting_jti, "sub" => ^subject} <- claims,
                true <- acting_record.subject == subject,
                {:ok, peer_jtis} <-
                  Token.revoke_for_subject_except_with_jtis(
                    user,
                    "user",
                    acting_jti,
                    disconnect?: false
                  ),
                {:ok, replacement, _claims} <-
                  Jwt.token_for_user(user, %{"purpose" => "user"}, purpose: :user),
                :ok <- Token.revoke_jti(acting_jti, subject, disconnect?: false) do
             {:ok, {replacement, peer_jtis}}
           else
             :error -> {:error, :session_replacement_failed}
             false -> {:error, :invalid_session}
             _ -> {:error, :invalid_session}
           end
         end) do
      {:ok, {:ok, {replacement, peer_jtis}}} ->
        {:ok, {replacement, peer_jtis}}

      {:error, %Ash.Error.Unknown.UnknownError{error: "unknown error: :invalid_session"}} ->
        {:error, :invalid_session}

      {:error,
       %Ash.Error.Unknown.UnknownError{error: "unknown error: :session_replacement_failed"}} ->
        {:error, :session_replacement_failed}

      {:error, %Ash.Error.Unknown.UnknownError{error: reason}} ->
        {:error, reason}

      {:error, _reason} = error ->
        error

      _ ->
        {:error, :session_replacement_failed}
    end
  end

  defp install_browser_session(conn, user, token) when is_struct(user) and is_binary(token) do
    previous_token = get_session(conn, :user_token)

    with {:ok, retired_jtis} <- Token.establish_browser_session(user, token, previous_token),
         :ok <- Token.broadcast_session_jtis(retired_jtis) do
      conn =
        conn
        |> configure_session(renew: true)
        |> store_in_session(Ash.Resource.put_metadata(user, :token, token))
        |> put_session(:live_socket_id, session_topic(token))

      {:ok, conn}
    else
      {:error, _reason} = error ->
        _ = Token.revoke_token(token)
        error
    end
  end

  defp install_browser_session(conn, _user, _token), do: {:ok, conn}

  defp log_authentication_result({:password, :sign_in}, :success, user) do
    SecurityLog.record(:sign_in_succeeded, actor_id: record_id(user), target_id: record_id(user))
  end

  defp log_authentication_result({:password, :sign_in}, :rejected, _user) do
    SecurityLog.record(:sign_in_rejected)
  end

  defp log_authentication_result({:setup, :confirm}, :success, user) do
    SecurityLog.record(:setup_completed, target_id: record_id(user))
  end

  defp log_authentication_result({:setup, :confirm}, :rejected, _user) do
    SecurityLog.record(:setup_completion_rejected)
  end

  defp log_authentication_result({:email_change, :confirm}, :success, user) do
    SecurityLog.record(:email_change_confirmed, target_id: record_id(user))
  end

  defp log_authentication_result({:email_change, :confirm}, :rejected, _user) do
    SecurityLog.record(:email_change_confirmation_rejected)
  end

  defp log_authentication_result({:password, :reset}, :success, user) do
    SecurityLog.record(:password_reset, target_id: record_id(user))
  end

  defp log_authentication_result({:password, :reset}, :rejected, _user) do
    SecurityLog.record(:password_reset_rejected)
  end

  defp log_authentication_result(_activity, _result, _user), do: :ok

  defp record_id(%{id: id}) when is_binary(id), do: id
  defp record_id(_user), do: nil

  defp rate_limited_response(conn, retry_after) do
    conn
    |> put_resp_header("retry-after", Integer.to_string(max(1, retry_after)))
    |> put_resp_content_type("text/plain")
    |> send_resp(429, "Too many requests. Please try again later.")
  end
end
