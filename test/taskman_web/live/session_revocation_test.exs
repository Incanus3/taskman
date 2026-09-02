defmodule TaskmanWeb.Live.SessionRevocationTest do
  use TaskmanWeb.ConnCase, async: false

  import Ecto.Query

  import Taskman.AccountsFixtures

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.Token
  alias Taskman.Repo
  alias TaskmanWeb.AuthController

  test "each stored browser session gets an isolated socket identity", %{conn: conn} do
    user = user_fixture()
    first = log_in_user(conn, user)
    second = log_in_user(build_conn(), user)

    first_token = Plug.Conn.get_session(first, :user_token)
    second_token = Plug.Conn.get_session(second, :user_token)

    refute first_token == second_token

    refute Plug.Conn.get_session(first, :live_socket_id) ==
             Plug.Conn.get_session(second, :live_socket_id)
  end

  test "revocation broadcasts only to the affected session socket", %{conn: conn} do
    user = user_fixture()
    first = log_in_user(conn, user)
    second = log_in_user(build_conn(), user)
    first_socket = Plug.Conn.get_session(first, :live_socket_id)
    second_socket = Plug.Conn.get_session(second, :live_socket_id)

    TaskmanWeb.Endpoint.subscribe(first_socket)
    TaskmanWeb.Endpoint.subscribe(second_socket)

    assert :ok =
             Token.revoke_for_subject_except(
               user,
               "user",
               jti(Plug.Conn.get_session(second, :user_token))
             )

    assert_receive %Phoenix.Socket.Broadcast{topic: ^first_socket, event: "disconnect"}
    refute_receive %Phoenix.Socket.Broadcast{topic: ^second_socket, event: "disconnect"}
  end

  test "revoking every credential disconnects browser sessions only", %{conn: conn} do
    user = user_fixture()
    first = log_in_user(conn, user)
    second = log_in_user(build_conn(), user)
    first_socket = Plug.Conn.get_session(first, :live_socket_id)
    second_socket = Plug.Conn.get_session(second, :live_socket_id)

    assert {:ok, setup_token, _claims} =
             Jwt.token_for_user(user, %{"act" => "complete_setup"}, purpose: :setup)

    setup_socket = AuthController.session_topic(setup_token)
    TaskmanWeb.Endpoint.subscribe(first_socket)
    TaskmanWeb.Endpoint.subscribe(second_socket)
    TaskmanWeb.Endpoint.subscribe(setup_socket)

    assert :ok = Token.revoke_all_for_subject(user)
    assert_receive %Phoenix.Socket.Broadcast{topic: ^first_socket, event: "disconnect"}
    assert_receive %Phoenix.Socket.Broadcast{topic: ^second_socket, event: "disconnect"}
    refute_receive %Phoenix.Socket.Broadcast{topic: ^setup_socket, event: "disconnect"}
  end

  test "password-change replacement seam preserves the acting token while revoking peers", %{
    conn: conn
  } do
    user = user_fixture()
    acting = log_in_user(conn, user)
    peer = log_in_user(build_conn(), user)
    acting_token = Plug.Conn.get_session(acting, :user_token)
    peer_token = Plug.Conn.get_session(peer, :user_token)
    acting_socket = Plug.Conn.get_session(acting, :live_socket_id)
    peer_socket = Plug.Conn.get_session(peer, :live_socket_id)

    TaskmanWeb.Endpoint.subscribe(acting_socket)
    TaskmanWeb.Endpoint.subscribe(peer_socket)

    assert {:ok, replacement} = AuthController.replace_session_token(user, acting_token)
    assert replacement != acting_token
    assert :ok = Token.valid_for_purpose?(replacement, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(acting_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(peer_token, "user")
    assert_receive %Phoenix.Socket.Broadcast{topic: ^peer_socket, event: "disconnect"}
    refute_receive %Phoenix.Socket.Broadcast{topic: ^acting_socket, event: "disconnect"}

    assert {:ok, %{"exp" => expires_at}} = Jwt.peek(replacement)
    lifetime = expires_at - System.system_time(:second)
    assert lifetime in (30 * 24 * 60 * 60 - 5)..(30 * 24 * 60 * 60 + 5)
  end

  test "replacement rejects a forged token even when its JTI belongs to the user", %{conn: conn} do
    user = user_fixture()
    acting = log_in_user(conn, user)
    peer = log_in_user(build_conn(), user)
    acting_token = Plug.Conn.get_session(acting, :user_token)
    peer_token = Plug.Conn.get_session(peer, :user_token)
    peer_socket = Plug.Conn.get_session(peer, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(peer_socket)

    forged = forge_signature(acting_token)

    assert {:error, :invalid_session} = AuthController.replace_session_token(user, forged)
    assert :ok = Token.valid_for_purpose?(acting_token, "user")
    assert :ok = Token.valid_for_purpose?(peer_token, "user")
    refute_receive %Phoenix.Socket.Broadcast{topic: ^peer_socket, event: "disconnect"}
  end

  test "reset rollback leaves browser sessions valid and emits no disconnect", %{conn: conn} do
    user = user_fixture()
    browser = log_in_user(conn, user)
    browser_token = Plug.Conn.get_session(browser, :user_token)
    browser_socket = Plug.Conn.get_session(browser, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(browser_socket)

    assert {:ok, reset_token, _claims} =
             Jwt.token_for_user(
               user,
               %{"act" => "reset_password"},
               token_lifetime: {1, :hours},
               purpose: :password_reset
             )

    assert {:error, _reason} =
             Accounts.reset_password(
               reset_token,
               %{password: "replacement-password", password_confirmation: "replacement-password"},
               session_revoker: fn _user -> {:error, :session_store_unavailable} end
             )

    assert :ok = Token.valid_for_purpose?(browser_token, "user")
    refute_receive %Phoenix.Socket.Broadcast{topic: ^browser_socket, event: "disconnect"}
  end

  test "reset revocation failure rolls back without broadcasting a disconnect", %{conn: conn} do
    user = user_fixture()
    browser = log_in_user(conn, user)
    browser_token = Plug.Conn.get_session(browser, :user_token)
    browser_socket = Plug.Conn.get_session(browser, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(browser_socket)

    assert {:ok, reset_token, _claims} =
             Jwt.token_for_user(
               user,
               %{"act" => "reset_password"},
               token_lifetime: {1, :hours},
               purpose: :password_reset
             )

    with_failing_revocation(jti(browser_token), fn ->
      assert {:error, _reason} =
               Accounts.reset_password(
                 reset_token,
                 %{
                   password: "replacement-password",
                   password_confirmation: "replacement-password"
                 }
               )
    end)

    assert :ok = Token.valid_for_purpose?(browser_token, "user")
    assert :ok = Token.valid_for_purpose?(reset_token, "password_reset")
    refute_receive %Phoenix.Socket.Broadcast{topic: ^browser_socket, event: "disconnect"}
  end

  test "rotation revokes a peer created while the acting token is locked", %{conn: conn} do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      user = user_fixture()

      try do
        acting = log_in_user(conn, user)
        acting_token = Plug.Conn.get_session(acting, :user_token)
        acting_socket = Plug.Conn.get_session(acting, :live_socket_id)
        TaskmanWeb.Endpoint.subscribe(acting_socket)

        with_session_lock(acting_token, fn release_lock ->
          replacer = start_replace_task(user, acting_token)
          await_backend_lock(replacer.backend_pid)

          peer_token = token_in_independent_connection(user)
          peer_socket = Token.session_topic(peer_token)
          TaskmanWeb.Endpoint.subscribe(peer_socket)

          release_lock.()

          assert {:ok, replacement} = Task.await(replacer.task, :infinity)
          assert :ok = Token.valid_for_purpose?(replacement, "user")
          assert {:error, :invalid_token} = Token.valid_for_purpose?(peer_token, "user")
          assert_receive %Phoenix.Socket.Broadcast{topic: ^peer_socket, event: "disconnect"}
          refute_receive %Phoenix.Socket.Broadcast{topic: ^acting_socket, event: "disconnect"}
        end)
      after
        delete_user_with_tokens(user)
      end
    end)
  end

  test "replacement racing a revoke never retires peers after the acting token loses its lock", %{
    conn: conn
  } do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      user = user_fixture()

      try do
        acting = log_in_user(conn, user)
        acting_token = Plug.Conn.get_session(acting, :user_token)
        acting_jti = jti(acting_token)
        subject = AshAuthentication.user_to_subject(user)
        peer_token = token_in_independent_connection(user)
        peer_socket = Token.session_topic(peer_token)
        TaskmanWeb.Endpoint.subscribe(peer_socket)

        with_session_lock(acting_token, fn release_lock ->
          revoker = start_revoke_task(acting_jti, subject)
          await_backend_lock(revoker.backend_pid)
          replacer = start_replace_task(user, acting_token)
          await_backend_lock(replacer.backend_pid)

          release_lock.()

          assert :ok = Task.await(revoker.task, :infinity)
          assert {:error, :invalid_session} = Task.await(replacer.task, :infinity)
          assert :ok = Token.valid_for_purpose?(peer_token, "user")
          refute_receive %Phoenix.Socket.Broadcast{topic: ^peer_socket, event: "disconnect"}
        end)
      after
        delete_user_with_tokens(user)
      end
    end)
  end

  test "multi-session revocation is atomic and emits no disconnect after a failed token", %{
    conn: conn
  } do
    user = user_fixture()
    first = log_in_user(conn, user)
    second = log_in_user(build_conn(), user)
    first_token = Plug.Conn.get_session(first, :user_token)
    second_token = Plug.Conn.get_session(second, :user_token)
    first_socket = Plug.Conn.get_session(first, :live_socket_id)
    second_socket = Plug.Conn.get_session(second, :live_socket_id)

    TaskmanWeb.Endpoint.subscribe(first_socket)
    TaskmanWeb.Endpoint.subscribe(second_socket)

    with_failing_revocation(jti(second_token), fn ->
      assert {:error, _reason} = Token.revoke_for_subject(user, "user")
    end)

    assert :ok = Token.valid_for_purpose?(first_token, "user")
    assert :ok = Token.valid_for_purpose?(second_token, "user")
    refute_receive %Phoenix.Socket.Broadcast{topic: ^first_socket, event: "disconnect"}
    refute_receive %Phoenix.Socket.Broadcast{topic: ^second_socket, event: "disconnect"}
  end

  defp jti(token) do
    {:ok, %{"jti" => jti}} = Jwt.peek(token)
    jti
  end

  defp forge_signature(token) do
    [header, payload, signature] = String.split(token, ".", parts: 3)
    replacement = if String.last(signature) == "A", do: "B", else: "A"

    Enum.join(
      [header, payload, String.slice(signature, 0, String.length(signature) - 1) <> replacement],
      "."
    )
  end

  defp with_failing_revocation(failing_jti, fun) do
    suffix = System.unique_integer([:positive])
    function_name = "taskman_session_fail_#{suffix}"
    trigger_name = "#{function_name}_trigger"

    Ecto.Adapters.SQL.query!(
      Repo,
      """
      CREATE FUNCTION #{function_name}()
      RETURNS trigger
      LANGUAGE plpgsql
      AS $$
      BEGIN
        IF NEW.jti = '#{failing_jti}' AND NEW.purpose = 'revocation' THEN
          RAISE EXCEPTION 'forced token revocation failure';
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

    try do
      fun.()
    after
      Ecto.Adapters.SQL.query!(Repo, "DROP TRIGGER IF EXISTS #{trigger_name} ON tokens")
      Ecto.Adapters.SQL.query!(Repo, "DROP FUNCTION IF EXISTS #{function_name}()")
    end
  end

  defp token_in_independent_connection(user) do
    test_pid = self()

    task =
      Task.async(fn ->
        :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

        try do
          {:ok, token, _claims} = Jwt.token_for_user(user, %{"purpose" => "user"}, purpose: :user)
          send(test_pid, {:independent_token_created, self()})
          token
        after
          :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
        end
      end)

    task_pid = task.pid
    assert_receive {:independent_token_created, ^task_pid}
    Task.await(task, :infinity)
  end

  defp with_session_lock(token, fun) do
    test_pid = self()
    locked_jti = jti(token)

    locker =
      Task.async(fn ->
        :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

        try do
          Repo.transaction(fn ->
            Repo.one!(
              from stored_token in Token,
                where:
                  stored_token.jti == ^locked_jti and
                    stored_token.purpose == "user",
                lock: "FOR UPDATE"
            )

            send(test_pid, {:session_lock_acquired, self(), database_backend_pid()})

            receive do
              :release_session_lock -> :ok
            end
          end)
        after
          :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
        end
      end)

    locker_pid = locker.pid
    assert_receive {:session_lock_acquired, ^locker_pid, _locker_backend_pid}
    release_lock = fn -> send(locker.pid, :release_session_lock) end

    try do
      fun.(release_lock)
    after
      release_lock.()
      Task.await(locker, :infinity)
    end
  end

  defp start_replace_task(user, acting_token) do
    test_pid = self()

    Task.async(fn ->
      :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

      try do
        send(test_pid, {:replacement_started, self(), database_backend_pid()})
        AuthController.replace_session_token(user, acting_token)
      after
        :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
      end
    end)
    |> then(fn task ->
      task_pid = task.pid
      assert_receive {:replacement_started, ^task_pid, backend_pid}
      %{task: task, backend_pid: backend_pid}
    end)
  end

  defp start_revoke_task(jti, subject) do
    test_pid = self()

    Task.async(fn ->
      :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

      try do
        send(test_pid, {:revocation_started, self(), database_backend_pid()})
        Token.revoke_jti(jti, subject, disconnect?: false)
      after
        :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
      end
    end)
    |> then(fn task ->
      task_pid = task.pid
      assert_receive {:revocation_started, ^task_pid, backend_pid}
      %{task: task, backend_pid: backend_pid}
    end)
  end

  defp await_backend_lock(%{backend_pid: backend_pid}), do: await_backend_lock(backend_pid)

  defp await_backend_lock(backend_pid), do: await_backend_lock(backend_pid, 1_000)

  defp await_backend_lock(_backend_pid, 0),
    do: flunk("worker never reached database lock contention")

  defp await_backend_lock(backend_pid, attempts_left) do
    assert %{rows: [[waiting]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               """
               SELECT count(*)
               FROM pg_stat_activity
               WHERE pid = $1 AND wait_event_type = 'Lock'
               """,
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

  defp delete_user_with_tokens(user) do
    subject = AshAuthentication.user_to_subject(user)
    Repo.delete_all(from token in Token, where: token.subject == ^subject)

    case Repo.get(Taskman.Accounts.User, user.id) do
      nil -> :ok
      persisted_user -> Repo.delete!(persisted_user)
    end
  end
end
