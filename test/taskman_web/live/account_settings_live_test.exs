defmodule TaskmanWeb.Live.AccountSettingsLiveTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Swoosh.TestAssertions
  import Taskman.AccountsFixtures

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.{ApiKey, Token, User}
  alias Taskman.Repo

  setup :set_swoosh_global

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

  test "settings retains the current email and shows a pending email change", %{conn: conn} do
    {user, conn} = password_user_conn(conn)
    {:ok, view, _html} = live(conn, "/account/settings")
    pending_email = "changed-#{System.unique_integer([:positive])}@example.com"

    view
    |> form("#email-change-form", %{
      "email_change" => %{
        "email" => pending_email,
        "current_password" => "password1"
      }
    })
    |> render_submit()

    assert %User{} = reloaded_user = Repo.get(User, user.id)
    assert to_string(reloaded_user.email) == to_string(user.email)
    assert has_element?(view, "#current-email-address")
    assert has_element?(view, "#pending-email-change")
    assert text_at(view, "#current-email-address") == to_string(user.email)
    assert text_at(view, "#pending-email-change") == "Pending confirmation: #{pending_email}"
    assert [""] = values_at(view, "#email-change-form input[name='email_change[email]']", "value")
  end

  test "email-change requests show retry guidance without a sixth email", %{conn: conn} do
    {user, conn} = password_user_conn(conn)
    {:ok, view, _html} = live(conn, "/account/settings")
    pending_email = "rate-limited-#{System.unique_integer([:positive])}@example.com"

    for _ <- 1..5 do
      view
      |> form("#email-change-form", %{
        "email_change" => %{"email" => pending_email, "current_password" => "password1"}
      })
      |> render_submit()

      assert_receive {:email, _email}
    end

    view
    |> form("#email-change-form", %{
      "email_change" => %{"email" => pending_email, "current_password" => "password1"}
    })
    |> render_submit()

    assert has_element?(view, "#email-change-rate-limited", "Please try again in")
    refute_email_sent()
    assert to_string(Repo.get!(User, user.id).email) == to_string(user.email)
  end

  test "settings replaces the displayed plaintext when a second API key is created", %{conn: conn} do
    {_user, conn} = password_user_conn(conn)
    {:ok, view, _html} = live(conn, "/account/settings")

    assert ["365"] =
             values_at(view, "#api-key-form input[name='api_key[expires_in_days]']", "value")

    view
    |> form("#api-key-form", %{"api_key" => %{"name" => "First"}})
    |> render_submit()

    first_plaintext = api_key_plaintext(view)

    view
    |> form("#api-key-form", %{"api_key" => %{"name" => "Second"}})
    |> render_submit()

    second_plaintext = api_key_plaintext(view)

    refute second_plaintext == first_plaintext
    assert [^second_plaintext] = values_at(view, "#api-key-plaintext-value", "value")
    assert [^second_plaintext] = values_at(view, "#api-key-copy", "data-copy-value")
  end

  test "settings creates API keys with custom expiry, rejects invalid expiry, displays once, and revokes",
       %{
         conn: conn
       } do
    {user, conn} = password_user_conn(conn)
    {:ok, view, _html} = live(conn, "/account/settings")

    view
    |> form("#api-key-form", %{
      "api_key" => %{"name" => "Automation", "expires_in_days" => "14"}
    })
    |> render_submit()

    assert has_element?(view, "#api-key-plaintext")
    assert {:ok, [%ApiKey{name: "Automation"} = api_key]} = Accounts.list_api_keys(user)
    assert DateTime.diff(api_key.expires_at, DateTime.utc_now(), :day) in 13..14

    view
    |> form("#api-key-form", %{
      "api_key" => %{"name" => "Invalid", "expires_in_days" => "366"}
    })
    |> render_submit()

    assert {:ok, [_]} = Accounts.list_api_keys(user)

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

  test "settings revokes every other browser session without revoking the acting session", %{
    conn: conn
  } do
    user = user_fixture()
    conn = log_in_user(conn, user)
    acting_token = Plug.Conn.get_session(conn, :user_token)
    peer = log_in_user(build_conn(), user)
    peer_token = Plug.Conn.get_session(peer, :user_token)

    {:ok, view, _html} = live(conn, "/account/settings")

    view
    |> element("#revoke-other-sessions")
    |> render_click()

    assert :ok = Token.valid_for_purpose?(acting_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(peer_token, "user")
  end

  test "settings excludes a session at its expiration boundary", %{conn: conn} do
    user = user_fixture()
    conn = log_in_user(conn, user)
    token = Plug.Conn.get_session(conn, :user_token)
    {:ok, %{"jti" => jti}} = Jwt.peek(token)
    now = DateTime.utc_now() |> DateTime.truncate(:second)

    Token
    |> Repo.get!(jti)
    |> Ecto.Changeset.change(expires_at: now)
    |> Repo.update!()

    assert {:ok, []} = Accounts.list_sessions(user)
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
    refute_receive %Phoenix.Socket.Broadcast{topic: ^socket, event: "disconnect"}
  end

  test "settings controllers reject incorrect passwords and destructive confirmation", %{
    conn: conn
  } do
    _remaining_administrator = password_user()
    user = password_user()
    conn = log_in_user(conn, user)
    token = Plug.Conn.get_session(conn, :user_token)

    failed_password_change =
      post(conn, "/account/settings/password", %{
        "password_change" => %{
          "current_password" => "wrong-password",
          "password" => "replacement-password",
          "password_confirmation" => "replacement-password"
        }
      })

    assert redirected_to(failed_password_change) == "/account/settings"
    assert :ok = Token.valid_for_purpose?(token, "user")

    failed_confirmation =
      post(conn, "/account/settings/delete", %{
        "delete_account" => %{"current_password" => "password1", "confirmation" => "DELETE ME"}
      })

    assert redirected_to(failed_confirmation) == "/account/settings"
    assert %User{} = Repo.get(User, user.id)

    failed_deletion =
      post(conn, "/account/settings/delete", %{
        "delete_account" => %{"current_password" => "wrong-password", "confirmation" => "DELETE"}
      })

    assert redirected_to(failed_deletion) == "/account/settings"
    assert %User{} = Repo.get(User, user.id)
  end

  defp password_user_conn(conn) do
    user = password_user()
    {user, log_in_user(conn, user)}
  end

  defp password_user do
    email = "settings-#{System.unique_integer([:positive])}@example.com"

    {:ok, user} =
      Accounts.bootstrap_admin(email, "password1")

    user
  end

  defp api_key_plaintext(view) do
    [plaintext] = values_at(view, "#api-key-plaintext-value", "value")
    plaintext
  end

  defp text_at(view, selector) do
    view
    |> render()
    |> LazyHTML.from_fragment()
    |> LazyHTML.query(selector)
    |> LazyHTML.text()
    |> String.trim()
  end

  defp values_at(view, selector, attribute) do
    view
    |> render()
    |> LazyHTML.from_fragment()
    |> LazyHTML.query(selector)
    |> LazyHTML.attribute(attribute)
  end
end
