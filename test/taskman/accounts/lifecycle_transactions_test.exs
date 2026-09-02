defmodule Taskman.Accounts.LifecycleTransactionsTest do
  use Taskman.DataCase, async: false

  import ExUnit.CaptureLog
  import Swoosh.TestAssertions

  alias AshAuthentication.{Argon2Provider, Jwt}
  alias Taskman.Accounts
  alias Taskman.Accounts.Token
  alias Taskman.Repo

  setup :set_swoosh_global

  test "concurrent setup redemption consumes a token exactly once and persists one password" do
    admin = admin_fixture()
    assert {:ok, _pending} = Accounts.invite_user(admin, %{email: "concurrent-setup@example.com"})
    token = receive_token("setup")

    attempts = ["first-setup-password", "second-setup-password"]

    results =
      concurrently(attempts, fn password ->
        Accounts.complete_setup(token, %{password: password, password_confirmation: password})
      end)

    successes =
      Enum.filter(Enum.zip(attempts, results), fn {_password, result} ->
        match?({:ok, _}, result)
      end)

    assert [{winning_password, {:ok, active}}] = successes
    assert active.status == :active
    assert Argon2Provider.valid?(winning_password, active.hashed_password)

    refute Argon2Provider.valid?(
             Enum.find(attempts, &(&1 != winning_password)),
             active.hashed_password
           )
  end

  test "concurrent email confirmation consumes a token exactly once and persists one address" do
    user = active_user_fixture("concurrent-confirm-old@example.com")

    assert {:ok, _user} =
             Accounts.request_email_change(
               user,
               "concurrent-confirm-new@example.com",
               "password1"
             )

    token = receive_token("confirm-email")

    results = concurrently([:first, :second], fn _ -> Accounts.confirm_email_change(token) end)

    assert [{:ok, changed_user}] = Enum.filter(results, &match?({:ok, _}, &1))
    assert to_string(changed_user.email) == "concurrent-confirm-new@example.com"

    assert {:ok, _user} =
             Accounts.sign_in_with_password(%{
               email: "concurrent-confirm-new@example.com",
               password: "password1"
             })
  end

  test "concurrent reset redemption consumes a token exactly once and persists one password" do
    _user = active_user_fixture("concurrent-reset-redemption@example.com")
    assert :ok = Accounts.request_password_reset("concurrent-reset-redemption@example.com")
    token = receive_token("reset-password")

    attempts = ["first-replacement-password", "second-replacement-password"]

    results =
      concurrently(attempts, fn password ->
        Accounts.reset_password(token, %{password: password, password_confirmation: password})
      end)

    assert [{winning_password, {:ok, changed_user}}] =
             Enum.filter(Enum.zip(attempts, results), fn {_password, result} ->
               match?({:ok, _}, result)
             end)

    assert Argon2Provider.valid?(winning_password, changed_user.hashed_password)

    assert {:ok, _user} =
             Accounts.sign_in_with_password(%{
               email: "concurrent-reset-redemption@example.com",
               password: winning_password
             })
  end

  test "reset revokes browser sessions while preserving an email-change confirmation" do
    user = active_user_fixture("session-preservation@example.com")

    assert {:ok, _user} =
             Accounts.request_email_change(
               user,
               "session-preservation-new@example.com",
               "password1"
             )

    email_change_token = receive_token("confirm-email")

    assert {:ok, browser_session_token, _claims} = Jwt.token_for_user(user, %{}, purpose: :user)
    assert :ok = Token.valid_for_purpose?(browser_session_token, "user")

    assert :ok = Accounts.request_password_reset("session-preservation@example.com")
    reset_token = receive_token("reset-password")

    assert {:ok, _user} =
             Accounts.reset_password(reset_token, %{
               password: "replacement-password",
               password_confirmation: "replacement-password"
             })

    assert {:error, :invalid_token} = Token.valid_for_purpose?(browser_session_token, "user")
    assert {:ok, changed} = Accounts.confirm_email_change(email_change_token)
    assert to_string(changed.email) == "session-preservation-new@example.com"
  end

  test "a browser-session revocation failure rolls back password installation and token consumption" do
    user = active_user_fixture("reset-rollback@example.com")
    assert {:ok, browser_session_token, _claims} = Jwt.token_for_user(user, %{}, purpose: :user)

    assert :ok = Accounts.request_password_reset("reset-rollback@example.com")
    reset_token = receive_token("reset-password")

    assert {:error, _reason} =
             Accounts.reset_password(
               reset_token,
               %{password: "replacement-password", password_confirmation: "replacement-password"},
               session_revoker: fn _user -> {:error, :session_store_unavailable} end
             )

    assert {:ok, _user} =
             Accounts.sign_in_with_password(%{
               email: "reset-rollback@example.com",
               password: "password1"
             })

    assert :ok = Token.valid_for_purpose?(reset_token, "password_reset")
    assert :ok = Token.valid_for_purpose?(browser_session_token, "user")
  end

  test "concurrent invitation resends leave one current setup token" do
    admin = admin_fixture()
    assert {:ok, pending} = Accounts.invite_user(admin, %{email: "concurrent-resend@example.com"})
    original_token = receive_token("setup")

    results =
      concurrently([:first, :second], fn _ -> Accounts.resend_invitation(admin, pending) end)

    assert Enum.all?(results, &match?({:ok, _}, &1))

    replacement_tokens = receive_tokens("setup", 2)

    valid_tokens =
      Enum.filter(
        [original_token | replacement_tokens],
        &(&1 |> Token.valid_for_purpose?("setup") == :ok)
      )

    assert [_current_token] = valid_tokens
  end

  test "concurrent email-change requests leave one current confirmation token" do
    user = active_user_fixture("concurrent-email-change@example.com")

    assert {:ok, _user} =
             Accounts.request_email_change(
               user,
               "concurrent-email-change-new@example.com",
               "password1"
             )

    original_token = receive_token("confirm-email")

    results =
      concurrently([:first, :second], fn _ ->
        Accounts.request_email_change(
          user,
          "concurrent-email-change-new@example.com",
          "password1"
        )
      end)

    assert Enum.all?(results, &match?({:ok, _}, &1))

    replacement_tokens = receive_tokens("confirm-email", 2)

    valid_tokens =
      Enum.filter(
        [original_token | replacement_tokens],
        &(&1 |> Token.valid_for_purpose?("email_change") == :ok)
      )

    assert [_current_token] = valid_tokens
  end

  test "concurrent reset requests leave one current reset token" do
    _user = active_user_fixture("concurrent-reset-request@example.com")
    assert :ok = Accounts.request_password_reset("concurrent-reset-request@example.com")
    original_token = receive_token("reset-password")

    results =
      concurrently([:first, :second], fn _ ->
        Accounts.request_password_reset("concurrent-reset-request@example.com")
      end)

    assert results == [:ok, :ok]

    replacement_tokens = receive_tokens("reset-password", 2)

    valid_tokens =
      Enum.filter(
        [original_token | replacement_tokens],
        &(&1 |> Token.valid_for_purpose?("password_reset") == :ok)
      )

    assert [_current_token] = valid_tokens
  end

  test "concurrent failed invitation resends retain one recoverable setup token" do
    admin = admin_fixture()
    assert {:ok, pending} = Accounts.invite_user(admin, %{email: "failed-resend@example.com"})
    _original_token = receive_token("setup")

    mailer_delivery = Application.fetch_env!(:taskman, :mailer_delivery)
    Application.put_env(:taskman, :mailer_delivery, __MODULE__.FailingMailer)
    on_exit(fn -> Application.put_env(:taskman, :mailer_delivery, mailer_delivery) end)

    results =
      concurrently([:first, :second], fn _ -> Accounts.resend_invitation(admin, pending) end)

    assert Enum.all?(results, fn
             {:error, {:delivery_failed, %{id: id}}} -> id == pending.id
             _ -> false
           end)

    assert [_current_token] = current_tokens(pending, "setup")
  end

  test "delivery logs a sanitized failure class without credential-bearing detail" do
    admin = admin_fixture()
    mailer_delivery = Application.fetch_env!(:taskman, :mailer_delivery)
    Application.put_env(:taskman, :mailer_delivery, __MODULE__.SensitiveFailingMailer)
    on_exit(fn -> Application.put_env(:taskman, :mailer_delivery, mailer_delivery) end)

    log =
      capture_log(fn ->
        assert {:error, {:delivery_failed, _pending}} =
                 Accounts.invite_user(admin, %{email: "log-recipient@example.com"})
      end)

    assert log =~ "class=transport"
    refute log =~ "log-recipient@example.com"
    refute log =~ "token=secret-token"
    refute log =~ "password=secret-password"
  end

  defp admin_fixture do
    unique = System.unique_integer([:positive])

    {:ok, admin} =
      Accounts.bootstrap_admin(%{
        email: "transaction-admin-#{unique}@example.com",
        password: "password1",
        password_confirmation: "password1"
      })

    admin
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

  defp receive_token(path) do
    assert_receive {:email, email}

    [_, token] =
      Regex.run(~r{https://[^\s<]+/#{path}/([^\s<]+)}, email.text_body)

    URI.decode(token)
  end

  defp receive_tokens(path, count) do
    for _ <- 1..count, do: receive_token(path)
  end

  defp current_tokens(user, purpose) do
    subject = AshAuthentication.user_to_subject(user)

    Repo.all(from token in Token, where: token.subject == ^subject and token.purpose == ^purpose)
  end

  defp concurrently(inputs, operation) do
    test_pid = self()

    coordinator =
      Task.async(fn ->
        Task.async_stream(
          inputs,
          fn input ->
            send(test_pid, {:lifecycle_attempt_ready, self()})

            receive do
              :redeem -> operation.(input)
            end
          end,
          max_concurrency: length(inputs),
          timeout: :infinity
        )
        |> Enum.map(fn {:ok, result} -> result end)
      end)

    worker_pids =
      for _ <- inputs do
        assert_receive {:lifecycle_attempt_ready, worker_pid}
        worker_pid
      end

    Enum.each(worker_pids, &send(&1, :redeem))
    Task.await(coordinator, :infinity)
  end

  defmodule FailingMailer do
    def deliver(_email), do: {:error, :unavailable}
  end

  defmodule SensitiveFailingMailer do
    def deliver(_email), do: {:error, {:transport, "token=secret-token password=secret-password"}}
  end
end
