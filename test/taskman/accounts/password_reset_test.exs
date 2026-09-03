defmodule Taskman.Accounts.PasswordResetTest do
  use Taskman.DataCase, async: false

  import Swoosh.TestAssertions
  import Taskman.AccountsFixtures

  alias AshAuthentication.{Argon2Provider, Errors.AuthenticationFailed}
  alias Taskman.Accounts

  setup :set_swoosh_global

  test "reset requests have the same public result for unknown, pending, active, disabled, and failed delivery" do
    active = active_user_fixture("active@example.com")
    _pending = pending_user_fixture(email: "pending@example.com")
    _disabled = disabled_user_fixture("disabled@example.com")

    assert_email_sent(fn email -> email.subject =~ "Set up" end)

    assert :ok = Accounts.request_password_reset("unknown@example.com")
    refute_email_sent()

    assert :ok = Accounts.request_password_reset("pending@example.com")
    refute_email_sent()

    assert :ok = Accounts.request_password_reset("disabled@example.com")
    refute_email_sent()

    assert :ok = Accounts.request_password_reset(to_string(active.email))
    _token = receive_token()

    mailer_delivery = Application.fetch_env!(:taskman, :mailer_delivery)
    Application.put_env(:taskman, :mailer_delivery, FailingMailer)
    on_exit(fn -> Application.put_env(:taskman, :mailer_delivery, mailer_delivery) end)

    assert :ok = Accounts.request_password_reset(to_string(active.email))
    refute_email_sent()
  end

  test "direct reset requests use the public limiter before revoking a token or sending mail" do
    user = active_user_fixture("direct-rate-limited@example.com")
    remote_ip = {203, 0, 113, 20}

    token =
      for _ <- 1..5 do
        assert :ok =
                 Accounts.request_password_reset("  DIRECT-RATE-LIMITED@example.com ",
                   remote_ip: remote_ip
                 )

        receive_token()
      end
      |> List.last()

    # The 429 result does not reveal account existence, and it must not revoke
    # the fifth token or send another recovery email.
    assert {:error, retry_after: retry_after} =
             Accounts.request_password_reset(to_string(user.email), remote_ip: remote_ip)

    assert retry_after >= 1
    refute_email_sent()

    assert {:ok, _user} =
             Accounts.reset_password(token, %{
               password: "replacement-password",
               password_confirmation: "replacement-password"
             })
  end

  test "a valid reset changes the password, consumes its token, and invalid attempts do not consume it" do
    user = active_user_fixture("reset@example.com")
    original_hash = user.hashed_password

    assert :ok = Accounts.request_password_reset("reset@example.com")
    token = receive_token()

    assert {:error, _error} =
             Accounts.reset_password(token, %{
               password: "new-password",
               password_confirmation: "mismatch"
             })

    assert {:ok, updated} =
             Accounts.reset_password(token, %{
               password: "new-password",
               password_confirmation: "new-password"
             })

    refute updated.hashed_password == original_hash
    assert Argon2Provider.valid?("new-password", updated.hashed_password)

    assert {:error, %AuthenticationFailed{}} =
             Accounts.sign_in_with_password(%{email: "reset@example.com", password: "password1"})

    assert {:ok, _user} =
             Accounts.sign_in_with_password(%{
               email: "reset@example.com",
               password: "new-password"
             })

    assert {:error, _error} =
             Accounts.reset_password(token, %{
               password: "third-password",
               password_confirmation: "third-password"
             })
  end

  test "a reset token is invalid at its exact expiry boundary" do
    _user = active_user_fixture("reset-expiry@example.com")
    assert :ok = Accounts.request_password_reset("reset-expiry@example.com")
    token = receive_token()

    assert {:error, _error} =
             Accounts.reset_password(
               token,
               %{password: "new-password", password_confirmation: "new-password"},
               now: token_expiry(token)
             )
  end

  test "a newer reset request invalidates the prior reset token" do
    _user = active_user_fixture("reset-resend@example.com")

    assert :ok = Accounts.request_password_reset("reset-resend@example.com")
    original_token = receive_token()

    assert :ok = Accounts.request_password_reset("reset-resend@example.com")
    replacement_token = receive_token()
    refute replacement_token == original_token

    assert {:error, _error} =
             Accounts.reset_password(original_token, %{
               password: "new-password",
               password_confirmation: "new-password"
             })

    assert {:ok, _user} =
             Accounts.reset_password(replacement_token, %{
               password: "new-password",
               password_confirmation: "new-password"
             })
  end

  defp active_user_fixture(email) do
    {:ok, user} =
      Accounts.bootstrap_admin(%{
        email: email,
        password: "password1",
        password_confirmation: "password1"
      })

    user
  end

  defp disabled_user_fixture(email) do
    {:ok, hash} = Argon2Provider.hash("password1")

    {:ok, user} =
      Accounts.bootstrap_user(
        %{
          email: email,
          hashed_password: hash,
          status: :disabled,
          confirmed_at: DateTime.utc_now()
        },
        actor: %{accounts_bootstrap?: true}
      )

    user
  end

  defp receive_token do
    assert_receive {:email, email}

    [_, token] = Regex.run(~r{https://[^\s<]+/reset-password/([^\s<]+)}, email.text_body)
    URI.decode(token)
  end

  defp token_expiry(token) do
    assert {:ok, %{"exp" => expiry}} = AshAuthentication.Jwt.peek(token)
    {:ok, expiry} = DateTime.from_unix(expiry)
    expiry
  end

  defmodule FailingMailer do
    def deliver(_email), do: {:error, :unavailable}
  end
end
