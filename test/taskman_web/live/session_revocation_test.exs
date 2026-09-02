defmodule TaskmanWeb.Live.SessionRevocationTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.AccountsFixtures

  alias AshAuthentication.Jwt
  alias Taskman.Accounts.Token
  alias TaskmanWeb.AuthController

  test "each stored browser session gets an isolated socket identity", %{conn: conn} do
    user = user_fixture()
    first = log_in_user(conn, user)
    second = log_in_user(build_conn(), user)

    first_token = Plug.Conn.get_session(first, :user_token)
    second_token = Plug.Conn.get_session(second, :user_token)

    refute first_token == second_token

    refute Plug.Conn.get_session(first, :live_socket_id) ==
             Plug.Conn.get_session(second, :live_socket_id)
  end

  test "revocation broadcasts only to the affected session socket", %{conn: conn} do
    user = user_fixture()
    first = log_in_user(conn, user)
    second = log_in_user(build_conn(), user)
    first_socket = Plug.Conn.get_session(first, :live_socket_id)
    second_socket = Plug.Conn.get_session(second, :live_socket_id)

    TaskmanWeb.Endpoint.subscribe(first_socket)
    TaskmanWeb.Endpoint.subscribe(second_socket)

    assert :ok =
             Token.revoke_for_subject_except(
               user,
               "user",
               jti(Plug.Conn.get_session(second, :user_token))
             )

    assert_receive %Phoenix.Socket.Broadcast{topic: ^first_socket, event: "disconnect"}
    refute_receive %Phoenix.Socket.Broadcast{topic: ^second_socket, event: "disconnect"}
  end

  test "revoking every credential disconnects browser sessions only", %{conn: conn} do
    user = user_fixture()
    first = log_in_user(conn, user)
    second = log_in_user(build_conn(), user)
    first_socket = Plug.Conn.get_session(first, :live_socket_id)
    second_socket = Plug.Conn.get_session(second, :live_socket_id)

    assert {:ok, setup_token, _claims} =
             Jwt.token_for_user(user, %{"act" => "complete_setup"}, purpose: :setup)

    setup_socket = AuthController.session_topic(setup_token)
    TaskmanWeb.Endpoint.subscribe(first_socket)
    TaskmanWeb.Endpoint.subscribe(second_socket)
    TaskmanWeb.Endpoint.subscribe(setup_socket)

    assert :ok = Token.revoke_all_for_subject(user)
    assert_receive %Phoenix.Socket.Broadcast{topic: ^first_socket, event: "disconnect"}
    assert_receive %Phoenix.Socket.Broadcast{topic: ^second_socket, event: "disconnect"}
    refute_receive %Phoenix.Socket.Broadcast{topic: ^setup_socket, event: "disconnect"}
  end

  test "password-change replacement seam preserves the acting token while revoking peers", %{
    conn: conn
  } do
    user = user_fixture()
    acting = log_in_user(conn, user)
    peer = log_in_user(build_conn(), user)
    acting_token = Plug.Conn.get_session(acting, :user_token)
    peer_token = Plug.Conn.get_session(peer, :user_token)
    acting_socket = Plug.Conn.get_session(acting, :live_socket_id)
    peer_socket = Plug.Conn.get_session(peer, :live_socket_id)

    TaskmanWeb.Endpoint.subscribe(acting_socket)
    TaskmanWeb.Endpoint.subscribe(peer_socket)

    assert {:ok, replacement} = AuthController.replace_session_token(user, acting_token)
    assert replacement != acting_token
    assert :ok = Token.valid_for_purpose?(replacement, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(acting_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(peer_token, "user")
    assert_receive %Phoenix.Socket.Broadcast{topic: ^peer_socket, event: "disconnect"}
    refute_receive %Phoenix.Socket.Broadcast{topic: ^acting_socket, event: "disconnect"}

    assert {:ok, %{"exp" => expires_at}} = Jwt.peek(replacement)
    assert expires_at - System.system_time(:second) <= 30 * 24 * 60 * 60
  end

  defp jti(token) do
    {:ok, %{"jti" => jti}} = Jwt.peek(token)
    jti
  end
end
