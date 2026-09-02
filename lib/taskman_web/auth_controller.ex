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
  alias Taskman.Accounts.Token
  alias TaskmanWeb.LiveUserAuth

  @doc false
  @impl AshAuthentication.Phoenix.Controller
  def success(conn, _activity, user, token) do
    token = token || get_in(user, [Access.key(:__metadata__), Access.key(:token)])

    conn =
      if is_binary(token) and is_struct(user) do
        conn
        |> store_in_session(Ash.Resource.put_metadata(user, :token, token))
        |> put_session(:live_socket_id, session_topic(token))
      else
        conn
      end

    return_to =
      conn
      |> get_session(:return_to)
      |> Kernel.||(conn.params["return_to"])
      |> LiveUserAuth.safe_return_path()

    conn
    |> delete_session(:return_to)
    |> redirect(to: return_to)
  end

  @doc false
  @impl AshAuthentication.Phoenix.Controller
  def failure(conn, _activity, _reason) do
    return_to =
      conn
      |> get_session(:return_to)
      |> Kernel.||(conn.params["return_to"])
      |> LiveUserAuth.safe_return_path()

    conn
    |> put_flash(:error, "The email or password is incorrect.")
    |> redirect(to: LiveUserAuth.sign_in_path(return_to))
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
    user = Ash.Resource.put_metadata(user, :token, token)

    conn
    |> store_in_session(user)
    |> put_session(:live_socket_id, session_topic(token))
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

    with :ok <- Token.valid_for_purpose?(acting_token, "user"),
         {:ok, %{"jti" => acting_jti, "sub" => ^subject}} <- Jwt.peek(acting_token),
         {:ok, peer_topics} <- Token.browser_session_topics(user, except_jti: acting_jti),
         {:ok, replacement} <- replace_token_transaction(user, acting_jti),
         :ok <- broadcast_peer_disconnects(peer_topics) do
      {:ok, replacement}
    else
      {:error, _reason} = error -> error
      _ -> {:error, :invalid_session}
    end
  end

  def replace_session_token(_user, _acting_token), do: {:error, :invalid_session}

  defp replace_token_transaction(user, acting_jti) do
    subject = AshAuthentication.user_to_subject(user)

    case Ash.transact([Taskman.Accounts.Token], fn ->
           with :ok <-
                  Token.revoke_for_subject_except(user, "user", acting_jti, disconnect?: false) do
             case Jwt.token_for_user(user, %{}, purpose: :user) do
               {:ok, replacement, _claims} ->
                 with :ok <- Token.revoke_jti(acting_jti, subject) do
                   {:ok, replacement}
                 end

               :error ->
                 {:error, :session_replacement_failed}
             end
           end
         end) do
      {:ok, {:ok, replacement}} -> {:ok, replacement}
      {:error, _reason} = error -> error
      _ -> {:error, :session_replacement_failed}
    end
  end

  defp broadcast_peer_disconnects(topics) do
    Enum.reduce_while(topics, :ok, fn topic, :ok ->
      case Token.broadcast_disconnect(topic) do
        :ok -> {:cont, :ok}
        {:error, _reason} = error -> {:halt, error}
      end
    end)
  end
end
