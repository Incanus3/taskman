defmodule Taskman.Accounts.User.EmailChangeTest do
  use Taskman.DataCase, async: false

  import Swoosh.TestAssertions

  alias AshAuthentication.Errors.AuthenticationFailed
  alias Taskman.Accounts

  setup :set_swoosh_global

  test "email change requires the current password and retains the old identity until confirmation" do
    user = user_fixture("old@example.com")

    assert {:error, _error} =
             Accounts.request_email_change(user, "new@example.com", "wrong-password")

    assert {:ok, _user} =
             Accounts.request_email_change(user, "new@example.com", "password1")

    token = receive_token("confirm-email")

    assert {:ok, _user} =
             Accounts.sign_in_with_password(%{email: "old@example.com", password: "password1"})

    assert {:error, %AuthenticationFailed{}} =
             Accounts.sign_in_with_password(%{email: "new@example.com", password: "password1"})

    assert {:ok, changed} = Accounts.confirm_email_change(token)
    assert to_string(changed.email) == "new@example.com"
  end

  test "resending an email change invalidates the earlier token and confirmation is single use" do
    user = user_fixture("resend-old@example.com")

    assert {:ok, _user} =
             Accounts.request_email_change(user, "resend-new@example.com", "password1")

    original_token = receive_token("confirm-email")

    assert {:ok, _user} =
             Accounts.request_email_change(user, "resend-new@example.com", "password1")

    replacement_token = receive_token("confirm-email")

    assert {:error, _error} = Accounts.confirm_email_change(original_token)
    assert {:ok, _user} = Accounts.confirm_email_change(replacement_token)
    assert {:error, _error} = Accounts.confirm_email_change(replacement_token)
  end

  test "email confirmation rejects an exact-expiry token and a duplicate target address" do
    user = user_fixture("expiry-old@example.com")

    assert {:ok, _user} =
             Accounts.request_email_change(user, "expiry-new@example.com", "password1")

    expiry_token = receive_token("confirm-email")

    assert {:error, _error} =
             Accounts.confirm_email_change(expiry_token, now: token_expiry(expiry_token))

    assert {:ok, _user} =
             Accounts.request_email_change(user, "taken@example.com", "password1")

    conflict_token = receive_token("confirm-email")
    _other = user_fixture("taken@example.com")

    assert {:error, _error} = Accounts.confirm_email_change(conflict_token)

    assert {:ok, _user} =
             Accounts.sign_in_with_password(%{
               email: "expiry-old@example.com",
               password: "password1"
             })
  end

  test "delivery failure preserves the pending confirmation while keeping the old email" do
    user = user_fixture("failure-old@example.com")
    mailer_delivery = Application.fetch_env!(:taskman, :mailer_delivery)
    Application.put_env(:taskman, :mailer_delivery, FailingMailer)

    on_exit(fn -> Application.put_env(:taskman, :mailer_delivery, mailer_delivery) end)

    assert {:error, :delivery_failed} =
             Accounts.request_email_change(user, "failure-new@example.com", "password1")

    assert {:ok, _user} =
             Accounts.sign_in_with_password(%{
               email: "failure-old@example.com",
               password: "password1"
             })

    Application.put_env(:taskman, :mailer_delivery, Taskman.Mailer)

    assert {:ok, _user} =
             Accounts.request_email_change(user, "failure-new@example.com", "password1")

    _token = receive_token("confirm-email")
  end

  defp user_fixture(email) do
    {:ok, user} =
      Accounts.bootstrap_admin(email, "password1")

    user
  end

  defp receive_token(path) do
    assert_receive {:email, email}

    [_, token] =
      Regex.run(~r{https://[^\s<]+/#{path}/([^\s<]+)}, email.text_body)

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
