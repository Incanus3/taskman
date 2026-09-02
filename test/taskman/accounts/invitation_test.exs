defmodule Taskman.Accounts.InvitationTest do
  use Taskman.DataCase, async: false

  import Swoosh.TestAssertions

  alias Taskman.Accounts

  setup :set_swoosh_global

  test "an invitation creates a pending user and setup activates it only once" do
    admin = admin_fixture()

    assert {:ok, pending} = Accounts.invite_user(admin, %{email: "invited@example.com"})
    assert pending.status == :pending
    assert is_nil(pending.hashed_password)

    token = receive_token("setup")

    assert {:ok, active} =
             Accounts.complete_setup(token, %{
               password: "password1",
               password_confirmation: "password1"
             })

    assert active.status == :active
    assert %DateTime{} = active.confirmed_at

    assert {:error, _error} =
             Accounts.complete_setup(token, %{
               password: "another-password",
               password_confirmation: "another-password"
             })
  end

  test "setup rejects a token at its exact expiry boundary without activating the account" do
    admin = admin_fixture()
    assert {:ok, pending} = Accounts.invite_user(admin, %{email: "expires@example.com"})
    token = receive_token("setup")

    assert {:error, _error} =
             Accounts.complete_setup(
               token,
               %{password: "password1", password_confirmation: "password1"},
               now: token_expiry(token)
             )

    assert pending.status == :pending
  end

  test "setup keeps the email address that received the invitation" do
    admin = admin_fixture()
    assert {:ok, _pending} = Accounts.invite_user(admin, %{email: "verified@example.com"})
    token = receive_token("setup")

    assert {:ok, active} =
             Accounts.complete_setup(token, %{
               email: "unverified@example.com",
               password: "password1",
               password_confirmation: "password1"
             })

    assert to_string(active.email) == "verified@example.com"
  end

  test "resending rotates the setup token and revoking an invitation invalidates it" do
    admin = admin_fixture()
    assert {:ok, pending} = Accounts.invite_user(admin, %{email: "rotate@example.com"})
    original_token = receive_token("setup")

    assert {:ok, ^pending} = Accounts.resend_invitation(admin, pending)
    replacement_token = receive_token("setup")
    refute replacement_token == original_token

    assert {:error, _error} =
             Accounts.complete_setup(original_token, %{
               password: "password1",
               password_confirmation: "password1"
             })

    assert :ok = Accounts.revoke_invitation(admin, pending)

    assert {:error, _error} =
             Accounts.complete_setup(replacement_token, %{
               password: "password1",
               password_confirmation: "password1"
             })
  end

  test "an invitation rejects an email that is already provisioned" do
    admin = admin_fixture()
    assert {:ok, _pending} = Accounts.invite_user(admin, %{email: "duplicate@example.com"})
    _token = receive_token("setup")

    assert {:error, _error} = Accounts.invite_user(admin, %{email: "DUPLICATE@example.com"})
  end

  test "a delivery failure leaves the created invitation available for resend" do
    admin = admin_fixture()
    mailer_delivery = Application.fetch_env!(:taskman, :mailer_delivery)
    Application.put_env(:taskman, :mailer_delivery, FailingMailer)

    on_exit(fn -> Application.put_env(:taskman, :mailer_delivery, mailer_delivery) end)

    assert {:error, {:delivery_failed, pending}} =
             Accounts.invite_user(admin, %{email: "recoverable@example.com"})

    assert pending.status == :pending

    Application.put_env(:taskman, :mailer_delivery, Taskman.Mailer)
    assert {:ok, ^pending} = Accounts.resend_invitation(admin, pending)
    _token = receive_token("setup")
  end

  defp admin_fixture do
    unique = System.unique_integer([:positive])

    {:ok, admin} =
      Accounts.bootstrap_admin(%{
        email: "admin-#{unique}@example.com",
        password: "password1",
        password_confirmation: "password1"
      })

    admin
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
