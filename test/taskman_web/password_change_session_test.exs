defmodule TaskmanWeb.PasswordChangeSessionTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.Token

  test "password change replaces the acting session, disconnects peers, and leaves API keys valid",
       %{
         conn: conn
       } do
    user = password_user()
    acting_conn = log_in_user(conn, user)
    peer_conn = log_in_user(build_conn(), user)
    acting_token = Plug.Conn.get_session(acting_conn, :user_token)
    peer_token = Plug.Conn.get_session(peer_conn, :user_token)
    peer_socket = Plug.Conn.get_session(peer_conn, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(peer_socket)

    now = DateTime.utc_now()

    assert {:ok, %{plaintext: plaintext}} =
             Accounts.create_api_key(
               user,
               %{
                 name: "Persistent automation",
                 expires_at: DateTime.add(now, 365 * 86_400, :second)
               },
               now: now
             )

    changed_conn =
      post(acting_conn, "/account/settings/password", %{
        "password_change" => %{
          "current_password" => "password1",
          "password" => "replacement-password",
          "password_confirmation" => "replacement-password"
        }
      })

    assert changed_conn.status == 302
    replacement_token = Plug.Conn.get_session(changed_conn, :user_token)
    refute replacement_token == acting_token
    assert :ok = Token.valid_for_purpose?(replacement_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(acting_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(peer_token, "user")
    assert_receive %Phoenix.Socket.Broadcast{topic: ^peer_socket, event: "disconnect"}
    assert {:ok, %{id: user_id}} = Accounts.sign_in_with_api_key(%{api_key: plaintext})
    assert user_id == user.id

    assert {:ok, view, _html} = live(changed_conn, "/account/settings")
    assert has_element?(view, "#account-settings")

    recovery_peer_conn = log_in_user(build_conn(), user)
    recovery_peer_token = Plug.Conn.get_session(recovery_peer_conn, :user_token)
    replacement_socket = Plug.Conn.get_session(changed_conn, :live_socket_id)
    recovery_peer_socket = Plug.Conn.get_session(recovery_peer_conn, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(replacement_socket)
    TaskmanWeb.Endpoint.subscribe(recovery_peer_socket)

    assert {:ok, reset_token, _claims} =
             Jwt.token_for_user(
               user,
               %{"act" => "reset_password"},
               token_lifetime: {1, :hours},
               purpose: :password_reset
             )

    assert {:ok, _updated_user} =
             Accounts.reset_password(reset_token, %{
               password: "reset-password",
               password_confirmation: "reset-password"
             })

    assert {:error, :invalid_token} = Token.valid_for_purpose?(replacement_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(recovery_peer_token, "user")
    assert_receive %Phoenix.Socket.Broadcast{topic: ^replacement_socket, event: "disconnect"}
    assert_receive %Phoenix.Socket.Broadcast{topic: ^recovery_peer_socket, event: "disconnect"}
    assert {:ok, %{id: user_id}} = Accounts.sign_in_with_api_key(%{api_key: plaintext})
    assert user_id == user.id
  end

  defp password_user do
    email = "password-change-#{System.unique_integer([:positive])}@example.com"

    {:ok, user} =
      Accounts.bootstrap_admin(%{
        email: email,
        password: "password1",
        password_confirmation: "password1"
      })

    user
  end
end
