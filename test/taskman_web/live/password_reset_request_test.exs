defmodule TaskmanWeb.Live.PasswordResetRequestTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Plug.Conn
  import Swoosh.TestAssertions

  alias Taskman.Accounts
  alias Taskman.Accounts.Token

  setup :set_swoosh_global

  test "the recovery request form posts through the controller and issues a redeemable reset token",
       %{conn: conn} do
    email = "recovery-#{System.unique_integer([:positive])}@example.com"
    assert {:ok, _user} = Accounts.bootstrap_admin(email, "original-password")

    assert {:ok, view, _html} = live(conn, "/reset-password")

    assert has_element?(
             view,
             "#password-reset-request-form[action='/auth/user/password/reset_request'][method='post']"
           )

    assert has_element?(view, "#password-reset-request-form input[name='_csrf_token']")

    assert has_element?(
             view,
             "#password-reset-request-form input[name='user[email]'][type='email']"
           )

    refute has_element?(view, "#password-reset-request-form[phx-submit]")
    refute has_element?(view, "#password-reset-request-form[phx-target]")

    response = post(conn, "/auth/user/password/reset_request", %{"user" => %{"email" => email}})

    assert redirected_to(response) == "/"
    assert_receive {:email, message}
    [_, token] = Regex.run(~r{https://[^\s<]+/reset-password/([^\s<]+)}, message.text_body)
    token = URI.decode(token)
    assert :ok = Token.valid_for_purpose?(token, "password_reset")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(token, "user")

    assert {:ok, _view, _html} = live(conn, "/reset-password/#{token}")

    reset =
      post(conn, "/auth/user/password/reset", %{
        "user" => %{
          "reset_token" => token,
          "password" => "replacement-password",
          "password_confirmation" => "replacement-password"
        }
      })

    assert redirected_to(reset) == "/sign-in"

    signed_in =
      post(build_conn(), "/auth/user/password/sign_in", %{
        "user" => %{"email" => email, "password" => "replacement-password"}
      })

    assert redirected_to(signed_in) == "/"
    assert is_binary(get_session(signed_in, :user_token))
  end
end
