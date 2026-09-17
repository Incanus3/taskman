defmodule TaskmanWeb.Live.SetupLiveTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Swoosh.TestAssertions
  import ExUnit.CaptureLog

  alias Taskman.Accounts
  alias Taskman.Accounts.{Token, User}
  alias Taskman.Repo

  setup :set_swoosh_global

  test "an invitation setup link renders a password form that posts its token", %{conn: conn} do
    {token, _pending, _email} = invitation()

    assert {:ok, view, _html} = live(conn, "/setup/#{token}")
    assert has_element?(view, "#setup-form[action='/auth/user/setup'][method='post']")
    assert has_element?(view, "#setup-form input[name='setup[token]'][type='hidden']")
    assert has_element?(view, "#setup-form input[name='setup[password]'][type='password']")

    assert has_element?(
             view,
             "#setup-form input[name='setup[password_confirmation]'][type='password']"
           )

    document = view |> render() |> LazyHTML.from_fragment()

    assert [^token] =
             document
             |> LazyHTML.query("#setup-form input[name='setup[token]']")
             |> LazyHTML.attribute("value")
  end

  test "posting a valid setup form activates a confirmed non-administrator who can sign in", %{
    conn: conn
  } do
    {token, pending, email} = invitation()

    response = submit_setup(conn, token, "new-invitation-password", "new-invitation-password")

    assert redirected_to(response) == "/sign-in"
    assert Plug.Conn.get_session(response, :user_token) == nil

    assert Phoenix.Flash.get(response.assigns.flash, :info) ==
             "Account setup complete. Sign in with your new password."

    assert %User{status: :active, admin?: false, confirmed_at: %DateTime{}} =
             Repo.get!(User, pending.id)

    assert {:ok, %User{id: user_id}} =
             Accounts.sign_in_with_password(%{email: email, password: "new-invitation-password"})

    assert user_id == pending.id
  end

  test "an invalid password leaves the invitation usable for a corrected retry", %{conn: conn} do
    {token, pending, _email} = invitation()

    rejected = submit_setup(conn, token, "short", "different")

    assert redirected_to(rejected) == "/setup/#{token}"
    assert %User{status: :pending, confirmed_at: nil} = Repo.get!(User, pending.id)
    assert :ok = Token.valid_for_purpose?(token, "setup")

    completed = submit_setup(conn, token, "corrected-password", "corrected-password")

    assert redirected_to(completed) == "/sign-in"
    assert %User{status: :active, confirmed_at: %DateTime{}} = Repo.get!(User, pending.id)
  end

  test "invalid, expired, revoked, rotated, and reused setup links receive the same recovery guidance",
       %{conn: conn} do
    invalid = "invalid-setup-token"

    {expired, _pending, _email} = invitation()
    expire_token(expired)

    {revoked, revoked_pending, _email} = invitation()
    administrator = administrator()
    assert :ok = Accounts.revoke_invitation(administrator, revoked_pending)

    {rotated, rotated_pending, _email} = invitation()
    assert {:ok, _resent} = Accounts.resend_invitation(administrator, rotated_pending)
    assert_receive {:email, _replacement_email}

    {reused, _pending, _email} = invitation()

    assert redirected_to(submit_setup(conn, reused, "reused-password", "reused-password")) ==
             "/sign-in"

    for token <- [invalid, expired, revoked, rotated, reused] do
      response = submit_setup(conn, token, "another-password", "another-password")

      assert redirected_to(response) == "/setup/#{token}"

      assert Phoenix.Flash.get(response.assigns.flash, :error) ==
               "Unable to complete setup. Check your password and confirmation. If this link is invalid or expired, ask an administrator to resend the invitation."
    end
  end

  test "the setup POST requires the nested token context", %{conn: conn} do
    {token, pending, _email} = invitation()
    submitted_password = "top-level-password-must-not-be-used"
    previous_level = Logger.level()
    Logger.configure(level: :info)
    on_exit(fn -> Logger.configure(level: previous_level) end)

    output =
      capture_log([level: :info], fn ->
        response =
          post(conn, "/auth/user/setup", %{
            "user" => %{
              "confirm" => token,
              "password" => submitted_password,
              "password_confirmation" => submitted_password
            }
          })

        assert redirected_to(response) == "/sign-in"
      end)

    assert %User{status: :pending, confirmed_at: nil} = Repo.get!(User, pending.id)
    assert :ok = Token.valid_for_purpose?(token, "setup")
    assert output =~ "security_event=setup_completion_rejected"
    refute output =~ token
    refute output =~ submitted_password
  end

  defp invitation do
    administrator = administrator()
    email = "setup-user-#{System.unique_integer([:positive])}@example.com"

    assert {:ok, pending} = Accounts.invite_user(administrator, %{email: email})

    assert_receive {:email, invitation}
    [_, token] = Regex.run(~r{https://[^\s<]+/setup/([^\s<]+)}, invitation.text_body)
    {URI.decode(token), pending, email}
  end

  defp administrator do
    {:ok, administrator} =
      Accounts.bootstrap_admin(
        "setup-admin-#{System.unique_integer([:positive])}@example.com",
        "administrator-password"
      )

    administrator
  end

  defp submit_setup(conn, token, password, password_confirmation) do
    post(conn, "/auth/user/setup", %{
      "setup" => %{
        "token" => token,
        "password" => password,
        "password_confirmation" => password_confirmation
      }
    })
  end

  defp expire_token(token) do
    assert {:ok, %{"jti" => jti}} = AshAuthentication.Jwt.peek(token)

    Token
    |> Repo.get!(jti)
    |> Ecto.Changeset.change(
      expires_at: DateTime.utc_now() |> DateTime.add(-1, :second) |> DateTime.truncate(:second)
    )
    |> Repo.update!()
  end
end
