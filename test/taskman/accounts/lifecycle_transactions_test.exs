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
    with_committed_accounts(fn ->
      admin = committed_admin_fixture()

      assert {:ok, pending} =
               Accounts.invite_user(admin, %{email: "concurrent-setup@example.com"})

      track_committed_user(pending)
      token = receive_token("setup")
      attempts = ["first-setup-password", "second-setup-password"]

      results =
        with_update_contention_gate(pending.id, fn release_gate ->
          concurrently(
            attempts,
            fn password ->
              Accounts.complete_setup(token, %{
                password: password,
                password_confirmation: password
              })
            end,
            fn workers ->
              await_lock_contention(workers)
              release_gate.()
            end
          )
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
    end)
  end

  test "concurrent email confirmation consumes a token exactly once and persists one address" do
    with_committed_accounts(fn ->
      user = committed_active_user_fixture("concurrent-confirm-old@example.com")

      assert {:ok, _user} =
               Accounts.request_email_change(
                 user,
                 "concurrent-confirm-new@example.com",
                 "password1"
               )

      token = receive_token("confirm-email")

      results =
        with_token_contention_gate(user, fn release_gate ->
          concurrently(
            [:first, :second],
            fn _ -> Accounts.confirm_email_change(token) end,
            fn workers ->
              await_lock_contention(workers)
              release_gate.()
            end
          )
        end)

      assert [{:ok, changed_user}] = Enum.filter(results, &match?({:ok, _}, &1))
      assert to_string(changed_user.email) == "concurrent-confirm-new@example.com"

      assert {:ok, _user} =
               Accounts.sign_in_with_password(%{
                 email: "concurrent-confirm-new@example.com",
                 password: "password1"
               })
    end)
  end

  test "concurrent reset redemption consumes a token exactly once and persists one password" do
    with_committed_accounts(fn ->
      user = committed_active_user_fixture("concurrent-reset-redemption@example.com")
      assert :ok = Accounts.request_password_reset("concurrent-reset-redemption@example.com")
      token = receive_token("reset-password")
      attempts = ["first-replacement-password", "second-replacement-password"]

      results =
        with_token_contention_gate(user, fn release_gate ->
          concurrently(
            attempts,
            fn password ->
              Accounts.reset_password(token, %{
                password: password,
                password_confirmation: password
              })
            end,
            fn workers ->
              await_lock_contention(workers)
              release_gate.()
            end
          )
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
    end)
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
    with_committed_accounts(fn ->
      admin = committed_admin_fixture()

      assert {:ok, pending} =
               Accounts.invite_user(admin, %{email: "concurrent-resend@example.com"})

      track_committed_user(pending)
      original_token = receive_token("setup")

      results =
        with_token_contention_gate(pending, fn release_gate ->
          concurrently(
            [:first, :second],
            fn _ -> Accounts.resend_invitation(admin, pending) end,
            fn workers ->
              await_lock_contention(workers)
              release_gate.()
            end
          )
        end)

      assert Enum.all?(results, &match?({:ok, _}, &1))

      replacement_tokens = receive_tokens("setup", 2)

      valid_tokens =
        Enum.filter(
          [original_token | replacement_tokens],
          &(&1 |> Token.valid_for_purpose?("setup") == :ok)
        )

      assert [_current_token] = valid_tokens
    end)
  end

  test "concurrent email-change requests leave one current confirmation token" do
    with_committed_accounts(fn ->
      user = committed_active_user_fixture("concurrent-email-change@example.com")

      assert {:ok, _user} =
               Accounts.request_email_change(
                 user,
                 "concurrent-email-change-new@example.com",
                 "password1"
               )

      original_token = receive_token("confirm-email")

      results =
        with_token_contention_gate(user, fn release_gate ->
          concurrently(
            [:first, :second],
            fn _ ->
              Accounts.request_email_change(
                user,
                "concurrent-email-change-new@example.com",
                "password1"
              )
            end,
            fn workers ->
              await_lock_contention(workers)
              release_gate.()
            end
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
    end)
  end

  test "concurrent reset requests leave one current reset token" do
    with_committed_accounts(fn ->
      _user = committed_active_user_fixture("concurrent-reset-request@example.com")
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
    end)
  end

  test "concurrent failed invitation resends retain one recoverable setup token" do
    with_committed_accounts(fn ->
      admin = committed_admin_fixture()

      assert {:ok, pending} =
               Accounts.invite_user(admin, %{email: "failed-resend@example.com"})

      track_committed_user(pending)
      _original_token = receive_token("setup")

      mailer_delivery = Application.fetch_env!(:taskman, :mailer_delivery)
      Application.put_env(:taskman, :mailer_delivery, __MODULE__.FailingMailer)
      on_exit(fn -> Application.put_env(:taskman, :mailer_delivery, mailer_delivery) end)

      results =
        with_token_contention_gate(pending, fn release_gate ->
          concurrently(
            [:first, :second],
            fn _ -> Accounts.resend_invitation(admin, pending) end,
            fn workers ->
              await_lock_contention(workers)
              release_gate.()
            end
          )
        end)

      assert Enum.all?(results, fn
               {:error, {:delivery_failed, %{id: id}}} -> id == pending.id
               _ -> false
             end)

      assert [_current_token] = current_tokens(pending, "setup")
    end)
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

  defp committed_admin_fixture do
    admin_fixture()
    |> track_committed_user()
  end

  defp committed_active_user_fixture(email) do
    active_user_fixture(email)
    |> track_committed_user()
  end

  defp with_committed_accounts(fun) do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      Process.put({__MODULE__, :committed_users}, [])

      try do
        fun.()
      after
        Process.get({__MODULE__, :committed_users}, [])
        |> Enum.uniq_by(& &1.id)
        |> Enum.each(&delete_committed_user/1)

        Process.delete({__MODULE__, :committed_users})
      end
    end)
  end

  defp track_committed_user(user) do
    key = {__MODULE__, :committed_users}
    Process.put(key, [user | Process.get(key, [])])
    user
  end

  defp delete_committed_user(user) do
    subject = AshAuthentication.user_to_subject(user)
    Repo.delete_all(from token in Token, where: token.subject == ^subject)

    case Repo.get(Taskman.Accounts.User, user.id) do
      nil -> :ok
      persisted_user -> Repo.delete!(persisted_user)
    end
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

  defp concurrently(inputs, operation, before_await \\ fn _workers -> :ok end) do
    test_pid = self()

    coordinator =
      Task.async(fn ->
        Task.async_stream(
          inputs,
          fn input ->
            :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

            try do
              send(test_pid, {:lifecycle_attempt_ready, self(), database_backend_pid()})

              receive do
                :redeem -> operation.(input)
              end
            after
              :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
            end
          end,
          max_concurrency: length(inputs),
          timeout: :infinity
        )
        |> Enum.map(fn {:ok, result} -> result end)
      end)

    workers =
      for _ <- inputs do
        assert_receive {:lifecycle_attempt_ready, worker_pid, backend_pid}
        %{pid: worker_pid, backend_pid: backend_pid}
      end

    assert length(Enum.uniq_by(workers, & &1.backend_pid)) == length(inputs)

    Enum.each(workers, &send(&1.pid, :redeem))
    before_await.(workers)
    Task.await(coordinator, :infinity)
  end

  defp database_backend_pid do
    assert %{rows: [[backend_pid]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT pg_backend_pid()")
    backend_pid
  end

  defp with_update_contention_gate(user_id, fun) do
    suffix = System.unique_integer([:positive])
    function_name = "taskman_lifecycle_gate_#{suffix}"
    trigger_name = "#{function_name}_trigger"
    gate_key = System.unique_integer([:positive])

    Ecto.Adapters.SQL.query!(
      Repo,
      """
      CREATE FUNCTION #{function_name}()
      RETURNS trigger
      LANGUAGE plpgsql
      AS $$
      BEGIN
        IF NEW.id = '#{user_id}'::uuid THEN
          PERFORM pg_advisory_xact_lock(#{gate_key});
        END IF;

        RETURN NEW;
      END;
      $$
      """
    )

    Ecto.Adapters.SQL.query!(
      Repo,
      """
      CREATE TRIGGER #{trigger_name}
      BEFORE UPDATE ON users
      FOR EACH ROW
      EXECUTE FUNCTION #{function_name}()
      """
    )

    Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_lock($1)", [gate_key])

    released? = :atomics.new(1, [])

    release_gate = fn ->
      if :atomics.compare_exchange(released?, 1, 0, 1) == :ok do
        Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_unlock($1)", [gate_key])
      end
    end

    try do
      fun.(release_gate)
    after
      release_gate.()
      Ecto.Adapters.SQL.query!(Repo, "DROP TRIGGER IF EXISTS #{trigger_name} ON users")
      Ecto.Adapters.SQL.query!(Repo, "DROP FUNCTION IF EXISTS #{function_name}()")
    end
  end

  defp with_token_contention_gate(user, fun) do
    suffix = System.unique_integer([:positive])
    function_name = "taskman_lifecycle_token_gate_#{suffix}"
    trigger_name = "#{function_name}_trigger"
    gate_key = System.unique_integer([:positive])
    subject = AshAuthentication.user_to_subject(user)

    Ecto.Adapters.SQL.query!(
      Repo,
      """
      CREATE FUNCTION #{function_name}()
      RETURNS trigger
      LANGUAGE plpgsql
      AS $$
      BEGIN
        IF NEW.subject = '#{subject}' THEN
          PERFORM pg_advisory_xact_lock(#{gate_key});
        END IF;

        RETURN NEW;
      END;
      $$
      """
    )

    Ecto.Adapters.SQL.query!(
      Repo,
      """
      CREATE TRIGGER #{trigger_name}
      BEFORE INSERT OR UPDATE ON tokens
      FOR EACH ROW
      EXECUTE FUNCTION #{function_name}()
      """
    )

    Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_lock($1)", [gate_key])

    released? = :atomics.new(1, [])

    release_gate = fn ->
      if :atomics.compare_exchange(released?, 1, 0, 1) == :ok do
        Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_unlock($1)", [gate_key])
      end
    end

    try do
      fun.(release_gate)
    after
      release_gate.()
      Ecto.Adapters.SQL.query!(Repo, "DROP TRIGGER IF EXISTS #{trigger_name} ON tokens")
      Ecto.Adapters.SQL.query!(Repo, "DROP FUNCTION IF EXISTS #{function_name}()")
    end
  end

  defp await_lock_contention(workers), do: await_lock_contention(workers, 1_000)

  defp await_lock_contention(_workers, 0),
    do: flunk("workers never reached database lock contention")

  defp await_lock_contention(workers, attempts_left) do
    backend_pids = Enum.map(workers, & &1.backend_pid)

    assert %{rows: [[waiting_workers]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               """
               SELECT count(*)
               FROM pg_stat_activity
               WHERE pid = ANY($1) AND wait_event_type = 'Lock'
               """,
               [backend_pids]
             )

    if waiting_workers == length(workers) do
      :ok
    else
      await_lock_contention(workers, attempts_left - 1)
    end
  end

  defmodule FailingMailer do
    def deliver(_email), do: {:error, :unavailable}
  end

  defmodule SensitiveFailingMailer do
    def deliver(_email), do: {:error, {:transport, "token=secret-token password=secret-password"}}
  end
end
