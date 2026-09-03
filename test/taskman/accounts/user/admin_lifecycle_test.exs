defmodule Taskman.Accounts.User.AdminLifecycleTest do
  use Taskman.DataCase, async: false

  import Ecto.Query

  import Taskman.AccountsFixtures, only: [pending_user_fixture: 1, user_fixture: 1]

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.{ApiKey, Token, User}
  alias Taskman.Repo

  test "only active administrators can administer accounts" do
    administrator = admin_fixture("administrator@example.com")
    disabled_administrator = admin_fixture("disabled-administrator@example.com")
    ordinary_user = user_fixture(email: "ordinary-user@example.com")
    pending = pending_user_fixture(email: "pending-user@example.com")

    assert {:ok, _disabled} = Accounts.disable_user(administrator, disabled_administrator)

    for actor <- [ordinary_user, disabled_administrator] do
      assert {:error, _reason} =
               Accounts.invite_user(actor, %{email: "blocked-#{actor.id}@example.com"})

      assert {:error, _reason} = Accounts.manage_email(actor, pending, "new@example.com", true)
      assert {:error, _reason} = Accounts.disable_user(actor, pending)
      assert {:error, _reason} = Accounts.delete_user(actor, pending)
    end
  end

  test "an administrator can manage another account lifecycle and credentials" do
    administrator = admin_fixture("lifecycle-administrator@example.com")
    user = user_fixture(email: "lifecycle-user@example.com")

    assert {:ok, promoted} = Accounts.promote_user(administrator, user)
    assert promoted.admin?

    assert {:ok, demoted} = Accounts.demote_user(administrator, promoted)
    refute demoted.admin?

    assert {:ok, disabled} = Accounts.disable_user(administrator, demoted)
    assert disabled.status == :disabled

    assert {:ok, enabled} = Accounts.enable_user(administrator, disabled)
    assert enabled.status == :active

    now = DateTime.utc_now()

    assert {:ok, %{api_key: api_key}} =
             Accounts.create_api_key(
               enabled,
               %{name: "Lifecycle credential", expires_at: DateTime.add(now, 86_400, :second)},
               now: now
             )

    assert :ok = Accounts.revoke_user_api_keys(administrator, enabled)
    assert %ApiKey{revoked_at: %DateTime{}} = Repo.get!(ApiKey, api_key.id)

    assert {:ok, session_token, _claims} = Jwt.token_for_user(enabled, %{}, purpose: :user)
    assert :ok = Accounts.revoke_user_sessions(administrator, enabled)
    assert {:error, :invalid_token} = Accounts.Token.valid_for_purpose?(session_token, "user")
  end

  test "administrator email management and deletion reject self-targeting" do
    administrator = admin_fixture("self-target-administrator@example.com")

    assert {:error, _reason} =
             Accounts.manage_email(administrator, administrator, "different@example.com", true)

    assert {:error, _reason} = Accounts.delete_user(administrator, administrator)
    assert %{} = Repo.get(Taskman.Accounts.User, administrator.id)
  end

  test "concurrent demote, disable, and self-delete attempts preserve the final active administrator" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      administrator =
        admin_fixture("final-administrator-#{System.unique_integer([:positive])}@example.com")

      try do
        results =
          concurrently([:demote, :disable, :delete], fn
            :demote -> Accounts.demote_user(administrator, administrator)
            :disable -> Accounts.disable_user(administrator, administrator)
            :delete -> Accounts.delete_own_account(administrator, "password1")
          end)

        assert Enum.all?(results, &match?({:error, _reason}, &1))

        assert %{status: :active, admin?: true} =
                 Repo.get(Taskman.Accounts.User, administrator.id)
      after
        Repo.delete_all(from user in Taskman.Accounts.User, where: user.id == ^administrator.id)
      end
    end)
  end

  test "lifecycle actions reject stale administrator authority and invalid transitions" do
    administrator = admin_fixture("stale-administrator@example.com")
    survivor = admin_fixture("stale-administrator-survivor@example.com")
    target = user_fixture(email: "stale-target@example.com")
    stale_administrator = administrator

    assert {:ok, _demoted} = Accounts.demote_user(administrator, administrator)

    assert {:error, _reason} = direct_lifecycle_update(target, :disable, stale_administrator)
    assert %{status: :active} = Repo.get!(User, target.id)

    assert {:error, _reason} = direct_lifecycle_update(target, :enable, survivor)
    assert {:error, _reason} = direct_lifecycle_update(survivor, :promote, survivor)
    assert {:error, _reason} = direct_lifecycle_update(target, :demote, survivor)

    assert {:error, _reason} = direct_invite(stale_administrator, "stale-invite@example.com")

    assert {:ok, pending} = Accounts.invite_user(survivor, %{email: "stale-resend@example.com"})

    assert {:error, _reason} =
             direct_lifecycle_update(pending, :resend_invitation, stale_administrator)

    assert {:error, _reason} =
             direct_lifecycle_update(pending, :revoke_invitation, stale_administrator)
  end

  test "two active administrators contending to remove each other preserve exactly one active administrator" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      for action <- [:demote, :disable, :delete] do
        first = admin_fixture("#{action}-first-#{System.unique_integer([:positive])}@example.com")

        second =
          admin_fixture("#{action}-second-#{System.unique_integer([:positive])}@example.com")

        try do
          results =
            with_lifecycle_write_gate([first, second], fn release_gate ->
              attempts =
                start_concurrent_attempts([{:first, first, second}, {:second, second, first}], fn
                  {_name, actor, target} ->
                    case action do
                      :demote -> Accounts.demote_user(actor, target)
                      :disable -> Accounts.disable_user(actor, target)
                      :delete -> Accounts.delete_user(actor, target)
                    end
                end)

              assert 2 == attempts.backend_pids |> Enum.uniq() |> length()

              release_concurrent_attempts(attempts)

              assert :ok = await_backend_blocked_by_peer(attempts.backend_pids)

              release_gate.()
              await_concurrent_attempts(attempts)
            end)

          assert 1 == Enum.count(results, &(match?({:ok, _}, &1) or &1 == :ok))
          assert 1 == Enum.count(results, &match?({:error, _}, &1))

          assert 1 ==
                   Repo.aggregate(
                     from(user in User, where: user.status == :active and user.admin? == true),
                     :count
                   )
        after
          delete_users([first, second])
        end
      end
    end)
  end

  test "disabling serializes browser-session issuance with credential revocation" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      suffix = System.unique_integer([:positive])
      administrator = admin_fixture("session-race-administrator-#{suffix}@example.com")
      target = user_fixture(email: "session-race-target-#{suffix}@example.com")

      try do
        with_user_lock(target, fn release_user_lock ->
          disabler =
            start_independent_task(:disable, fn ->
              Accounts.disable_user(administrator, target)
            end)

          await_backend_lock(disabler.backend_pid)

          issuer =
            start_independent_task(:session, fn ->
              Jwt.token_for_user(target, %{}, purpose: :user)
            end)

          refute_receive {:independent_task_completed, :session, _result}, 50

          release_user_lock.()

          assert {:ok, _disabled} = Task.await(disabler.task, :infinity)
          assert :error = Task.await(issuer.task, :infinity)

          assert [] =
                   Repo.all(
                     from token in Token,
                       where: token.subject == ^AshAuthentication.user_to_subject(target)
                   )
        end)
      after
        delete_users([administrator, target])
      end
    end)
  end

  test "disabling broadcasts exactly the browser sessions deleted by its committed transaction" do
    administrator = admin_fixture("revocation-administrator@example.com")
    target = user_fixture(email: "revocation-target@example.com")

    assert {:ok, first_session, _claims} = Jwt.token_for_user(target, %{}, purpose: :user)
    assert {:ok, second_session, _claims} = Jwt.token_for_user(target, %{}, purpose: :user)

    assert {:ok, setup_token, _claims} =
             Jwt.token_for_user(target, %{"act" => "complete_setup"}, purpose: :setup)

    first_topic = Token.session_topic(first_session)
    second_topic = Token.session_topic(second_session)
    setup_topic = Token.session_topic(setup_token)

    TaskmanWeb.Endpoint.subscribe(first_topic)
    TaskmanWeb.Endpoint.subscribe(second_topic)
    TaskmanWeb.Endpoint.subscribe(setup_topic)

    assert {:ok, _disabled} = Accounts.disable_user(administrator, target)

    assert_receive %Phoenix.Socket.Broadcast{topic: ^first_topic, event: "disconnect"}
    assert_receive %Phoenix.Socket.Broadcast{topic: ^second_topic, event: "disconnect"}
    refute_receive %Phoenix.Socket.Broadcast{topic: ^setup_topic, event: "disconnect"}
  end

  test "disabling serializes API-key issuance with credential revocation" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      suffix = System.unique_integer([:positive])
      administrator = admin_fixture("api-key-race-administrator-#{suffix}@example.com")
      target = user_fixture(email: "api-key-race-target-#{suffix}@example.com")
      now = DateTime.utc_now()

      assert {:ok, %{api_key: _key}} =
               Accounts.create_api_key(
                 target,
                 %{name: "Existing credential", expires_at: DateTime.add(now, 86_400, :second)},
                 now: now
               )

      try do
        with_api_key_update_gate(target, fn release_gate ->
          disabler =
            start_independent_task(:disable, fn ->
              Accounts.disable_user(administrator, target)
            end)

          await_backend_lock(disabler.backend_pid)

          issuer =
            start_independent_task(:api_key, fn ->
              Accounts.create_api_key(
                target,
                %{name: "Late credential", expires_at: DateTime.add(now, 86_400, :second)},
                now: now
              )
            end)

          refute_receive {:independent_task_completed, :api_key, _result}, 50

          release_gate.()

          assert {:ok, _disabled} = Task.await(disabler.task, :infinity)
          assert {:error, _reason} = Task.await(issuer.task, :infinity)

          assert [] =
                   Repo.all(
                     from key in ApiKey,
                       where: key.user_id == ^target.id and is_nil(key.revoked_at)
                   )
        end)
      after
        delete_users([administrator, target])
      end
    end)
  end

  defp admin_fixture(email) do
    {:ok, administrator} =
      Accounts.bootstrap_admin(email, "password1")

    administrator
  end

  defp concurrently(inputs, operation) do
    test_pid = self()

    coordinator =
      Task.async(fn ->
        Task.async_stream(
          inputs,
          fn input ->
            :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

            try do
              send(test_pid, {:admin_lifecycle_attempt_ready, self()})

              receive do
                :run_attempt -> operation.(input)
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
        assert_receive {:admin_lifecycle_attempt_ready, worker}
        worker
      end

    Enum.each(workers, &send(&1, :run_attempt))
    Task.await(coordinator, :infinity)
  end

  defp start_concurrent_attempts(inputs, operation) do
    test_pid = self()

    coordinator =
      Task.async(fn ->
        Task.async_stream(
          inputs,
          fn input ->
            :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

            try do
              send(
                test_pid,
                {:admin_lifecycle_attempt_ready, self(), database_backend_pid()}
              )

              receive do
                :run_attempt -> operation.(input)
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
        assert_receive {:admin_lifecycle_attempt_ready, worker, backend_pid}
        %{pid: worker, backend_pid: backend_pid}
      end

    %{
      coordinator: coordinator,
      workers: workers,
      backend_pids: Enum.map(workers, & &1.backend_pid)
    }
  end

  defp release_concurrent_attempts(%{workers: workers}) do
    Enum.each(workers, &release_concurrent_attempt/1)
  end

  defp release_concurrent_attempt(%{pid: pid}), do: send(pid, :run_attempt)

  defp await_concurrent_attempts(%{coordinator: coordinator}),
    do: Task.await(coordinator, :infinity)

  defp await_backend_blocked_by_peer(backend_pids),
    do: await_backend_blocked_by_peer(backend_pids, 1_000)

  defp await_backend_blocked_by_peer(_backend_pids, 0),
    do: flunk("workers never contended on each other's row locks")

  defp await_backend_blocked_by_peer(backend_pids, attempts_left) do
    backend_ids = Enum.join(backend_pids, ",")

    assert %{rows: rows} =
             Ecto.Adapters.SQL.query!(
               Repo,
               "SELECT pid, pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid IN (#{backend_ids})"
             )

    if Enum.any?(rows, fn [pid, blockers] ->
         Enum.any?(blockers, &(&1 in (backend_pids -- [pid])))
       end) do
      :ok
    else
      :erlang.yield()
      await_backend_blocked_by_peer(backend_pids, attempts_left - 1)
    end
  end

  defp with_lifecycle_write_gate(users, fun) do
    suffix = System.unique_integer([:positive])
    function_name = "taskman_lifecycle_gate_#{suffix}"
    trigger_name = "#{function_name}_trigger"
    gate_key = System.unique_integer([:positive])
    user_ids = users |> Enum.map(&"'#{&1.id}'::uuid") |> Enum.join(", ")

    Ecto.Adapters.SQL.query!(
      Repo,
      """
      CREATE FUNCTION #{function_name}()
      RETURNS trigger
      LANGUAGE plpgsql
      AS $$
      BEGIN
        IF (TG_OP = 'DELETE' AND OLD.id IN (#{user_ids})) OR
            (TG_OP <> 'DELETE' AND NEW.id IN (#{user_ids})) THEN
          PERFORM pg_advisory_xact_lock(#{gate_key});
        END IF;

        IF TG_OP = 'DELETE' THEN
          RETURN OLD;
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
      BEFORE UPDATE OR DELETE ON users
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

  defp direct_lifecycle_update(user, action, actor) do
    user
    |> Ash.Changeset.for_update(action, %{})
    |> Ash.update(actor: actor, authorize?: true, domain: Accounts)
  end

  defp direct_invite(actor, email) do
    User
    |> Ash.Changeset.for_create(:create_pending_user, %{email: email})
    |> Ash.create(actor: actor, authorize?: true, domain: Accounts)
  end

  defp start_independent_task(kind, operation) do
    test_pid = self()

    Task.async(fn ->
      :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

      try do
        send(test_pid, {:independent_task_started, kind, self(), database_backend_pid()})
        result = operation.()
        send(test_pid, {:independent_task_completed, kind, result})
        result
      after
        :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
      end
    end)
    |> then(fn task ->
      assert_receive {:independent_task_started, ^kind, task_pid, backend_pid}
      assert task_pid == task.pid
      %{task: task, backend_pid: backend_pid}
    end)
  end

  defp with_user_lock(user, fun) do
    test_pid = self()

    locker =
      Task.async(fn ->
        :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

        try do
          Repo.transaction(fn ->
            Repo.one!(
              from locked_user in User, where: locked_user.id == ^user.id, lock: "FOR UPDATE"
            )

            send(test_pid, {:user_lock_acquired, self()})

            receive do
              :release_user_lock -> :ok
            end
          end)
        after
          :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
        end
      end)

    assert_receive {:user_lock_acquired, locker_pid}
    assert locker_pid == locker.pid
    release_lock = fn -> send(locker.pid, :release_user_lock) end

    try do
      fun.(release_lock)
    after
      release_lock.()
      Task.await(locker, :infinity)
    end
  end

  defp with_api_key_update_gate(user, fun) do
    suffix = System.unique_integer([:positive])
    function_name = "taskman_api_key_gate_#{suffix}"
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
        IF NEW.user_id = '#{user.id}'::uuid AND NEW.revoked_at IS NOT NULL AND OLD.revoked_at IS NULL THEN
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
      BEFORE UPDATE ON api_keys
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
      Ecto.Adapters.SQL.query!(Repo, "DROP TRIGGER IF EXISTS #{trigger_name} ON api_keys")
      Ecto.Adapters.SQL.query!(Repo, "DROP FUNCTION IF EXISTS #{function_name}()")
    end
  end

  defp await_backend_lock(backend_pid), do: await_backend_lock(backend_pid, 1_000)

  defp await_backend_lock(_backend_pid, 0),
    do: flunk("worker never reached database lock contention")

  defp await_backend_lock(backend_pid, attempts_left) do
    assert %{rows: [[waiting]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               "SELECT count(*) FROM pg_stat_activity WHERE pid = $1 AND wait_event_type = 'Lock'",
               [backend_pid]
             )

    if waiting == 1 do
      :ok
    else
      :erlang.yield()
      await_backend_lock(backend_pid, attempts_left - 1)
    end
  end

  defp database_backend_pid do
    assert %{rows: [[backend_pid]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT pg_backend_pid()")
    backend_pid
  end

  defp delete_users(users) do
    Enum.each(users, fn user ->
      Repo.delete_all(
        from token in Token, where: token.subject == ^AshAuthentication.user_to_subject(user)
      )

      Repo.delete_all(from key in ApiKey, where: key.user_id == ^user.id)

      case Repo.get(User, user.id) do
        nil -> :ok
        persisted_user -> Repo.delete!(persisted_user)
      end
    end)
  end
end
