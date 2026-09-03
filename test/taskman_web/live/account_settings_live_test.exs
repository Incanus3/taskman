defmodule TaskmanWeb.Live.AccountSettingsLiveTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.{ApiKey, Token, User}
  alias Taskman.Repo

  test "settings renders the stable account-management surfaces", %{conn: conn} do
    user = user_fixture()
    conn = log_in_user(conn, user)

    assert {:ok, view, _html} = live(conn, "/account/settings")
    assert has_element?(view, "#account-settings")
    assert has_element?(view, "#email-change-form")
    assert has_element?(view, "#password-change-form")
    assert has_element?(view, "#sessions")
    assert has_element?(view, "#api-key-form")
    assert has_element?(view, "#delete-account-form")
  end

  test "settings requests an email change without replacing the current email", %{conn: conn} do
    {user, conn} = password_user_conn(conn)
    {:ok, view, _html} = live(conn, "/account/settings")

    view
    |> form("#email-change-form", %{
      "email_change" => %{
        "email" => "changed-#{System.unique_integer([:positive])}@example.com",
        "current_password" => "password1"
      }
    })
    |> render_submit()

    assert %User{} = reloaded_user = Repo.get(User, user.id)
    assert to_string(reloaded_user.email) == to_string(user.email)
  end

  test "settings creates, displays once, and revokes an API key", %{conn: conn} do
    {user, conn} = password_user_conn(conn)
    {:ok, view, _html} = live(conn, "/account/settings")

    view
    |> form("#api-key-form", %{"api_key" => %{"name" => "Automation"}})
    |> render_submit()

    assert has_element?(view, "#api-key-plaintext")
    assert {:ok, [%ApiKey{name: "Automation"} = api_key]} = Accounts.list_api_keys(user)
    assert DateTime.diff(api_key.expires_at, DateTime.utc_now(), :day) in 364..365

    {:ok, view, _html} = live(conn, "/account/settings")
    refute has_element?(view, "#api-key-plaintext")

    view
    |> element("#api-key-#{api_key.id} [data-action='revoke-api-key']")
    |> render_click()

    assert {:ok, [%ApiKey{id: id, revoked_at: revoked_at}]} = Accounts.list_api_keys(user)
    assert id == api_key.id
    assert %DateTime{} = revoked_at
  end

  test "settings lists stored sessions and revokes the selected session", %{conn: conn} do
    user = user_fixture()
    conn = log_in_user(conn, user)
    peer = log_in_user(build_conn(), user)
    peer_token = Plug.Conn.get_session(peer, :user_token)

    {:ok, view, _html} = live(conn, "/account/settings")
    assert has_element?(view, "#sessions")

    {:ok, %{"jti" => peer_jti}} = Jwt.peek(peer_token)

    view
    |> element("#session-#{peer_jti} [data-action='revoke-session']")
    |> render_click()

    assert {:error, :invalid_token} = Token.valid_for_purpose?(peer_token, "user")
  end

  test "settings requires destructive confirmation and deletes a password-confirmed account", %{
    conn: conn
  } do
    _remaining_administrator = password_user()
    user = password_user()
    conn = log_in_user(conn, user)
    token = Plug.Conn.get_session(conn, :user_token)
    socket = Plug.Conn.get_session(conn, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(socket)

    deleted_conn =
      post(conn, "/account/settings/delete", %{
        "delete_account" => %{
          "current_password" => "password1",
          "confirmation" => "DELETE"
        }
      })

    assert redirected_to(deleted_conn) == "/sign-in"
    assert is_nil(Plug.Conn.get_session(deleted_conn, :user_token))
    assert is_nil(Repo.get(User, user.id))
    assert {:error, :invalid_token} = Token.valid_for_purpose?(token, "user")
    assert_receive %Phoenix.Socket.Broadcast{topic: ^socket, event: "disconnect"}
  end

  defp password_user_conn(conn) do
    user = password_user()
    {user, log_in_user(conn, user)}
  end

  defp password_user do
    email = "settings-#{System.unique_integer([:positive])}@example.com"

    {:ok, user} =
      Accounts.bootstrap_admin(%{
        email: email,
        password: "password1",
        password_confirmation: "password1"
      })

    user
  end
end
