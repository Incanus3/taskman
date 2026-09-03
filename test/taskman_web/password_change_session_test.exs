defmodule TaskmanWeb.PasswordChangeSessionTest do
  use TaskmanWeb.ConnCase, async: false

  import Ecto.Query
  import Phoenix.LiveViewTest

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.{Token, User}
  alias Taskman.Repo

  test "password change replaces the acting session, disconnects peers, and leaves API keys valid",
       %{
         conn: conn
       } do
    user = password_user()
    acting_conn = log_in_user(conn, user)
    peer_conn = log_in_user(build_conn(), user)
    acting_token = Plug.Conn.get_session(acting_conn, :user_token)
    peer_token = Plug.Conn.get_session(peer_conn, :user_token)
    peer_socket = Plug.Conn.get_session(peer_conn, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(peer_socket)

    now = DateTime.utc_now()

    assert {:ok, %{plaintext: plaintext}} =
             Accounts.create_api_key(
               user,
               %{
                 name: "Persistent automation",
                 expires_at: DateTime.add(now, 365 * 86_400, :second)
               },
               now: now
             )

    changed_conn =
      post(acting_conn, "/account/settings/password", %{
        "password_change" => %{
          "current_password" => "password1",
          "password" => "replacement-password",
          "password_confirmation" => "replacement-password"
        }
      })

    assert changed_conn.status == 302
    replacement_token = Plug.Conn.get_session(changed_conn, :user_token)
    refute replacement_token == acting_token
    assert :ok = Token.valid_for_purpose?(replacement_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(acting_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(peer_token, "user")
    assert_receive %Phoenix.Socket.Broadcast{topic: ^peer_socket, event: "disconnect"}
    assert {:ok, %{id: user_id}} = Accounts.sign_in_with_api_key(%{api_key: plaintext})
    assert user_id == user.id

    assert {:ok, view, _html} = live(changed_conn, "/account/settings")
    assert has_element?(view, "#account-settings")

    recovery_peer_conn = log_in_user(build_conn(), Repo.get!(User, user.id))
    recovery_peer_token = Plug.Conn.get_session(recovery_peer_conn, :user_token)
    replacement_socket = Plug.Conn.get_session(changed_conn, :live_socket_id)
    recovery_peer_socket = Plug.Conn.get_session(recovery_peer_conn, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(replacement_socket)
    TaskmanWeb.Endpoint.subscribe(recovery_peer_socket)

    assert {:ok, reset_token, _claims} =
             Jwt.token_for_user(
               user,
               %{"act" => "reset_password"},
               token_lifetime: {1, :hours},
               purpose: :password_reset
             )

    assert {:ok, _updated_user} =
             Accounts.reset_password(reset_token, %{
               password: "reset-password",
               password_confirmation: "reset-password"
             })

    assert {:error, :invalid_token} = Token.valid_for_purpose?(replacement_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(recovery_peer_token, "user")
    assert_receive %Phoenix.Socket.Broadcast{topic: ^replacement_socket, event: "disconnect"}
    assert_receive %Phoenix.Socket.Broadcast{topic: ^recovery_peer_socket, event: "disconnect"}
    assert {:ok, %{id: user_id}} = Accounts.sign_in_with_api_key(%{api_key: plaintext})
    assert user_id == user.id
  end

  test "an old-password sign-in cannot store a browser session after an authenticated password change",
       %{conn: conn} do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      user = password_user()

      try do
        acting_conn = log_in_user(conn, user)
        acting_token = Plug.Conn.get_session(acting_conn, :user_token)

        with_user_update_gate(user.id, fn release_gate ->
          password_change =
            start_independent_task(:password_change, fn ->
              Accounts.change_password(user, acting_token, %{
                current_password: "password1",
                password: "replacement-password",
                password_confirmation: "replacement-password"
              })
            end)

          await_backend_lock(password_change.backend_pid)

          old_password_sign_in =
            start_independent_task(:old_password_sign_in, fn ->
              Accounts.sign_in_with_password(%{
                email: to_string(user.email),
                password: "password1"
              })
            end)

          await_backend_lock(old_password_sign_in.backend_pid)
          release_gate.()

          assert {:ok, %{replacement_session: replacement_session}} =
                   Task.await(password_change.task, :infinity)

          assert {:ok, _user} = Task.await(old_password_sign_in.task, :infinity)
          assert :ok = Token.valid_for_purpose?(replacement_session, "user")
          assert_only_session(user, replacement_session)
        end)
      after
        delete_user_with_tokens(user)
      end
    end)
  end

  test "an old-password sign-in cannot store a browser session after a recovery reset", %{
    conn: conn
  } do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      user = password_user()

      try do
        browser_conn = log_in_user(conn, user)
        browser_token = Plug.Conn.get_session(browser_conn, :user_token)

        assert {:ok, reset_token, _claims} =
                 Jwt.token_for_user(
                   user,
                   %{"act" => "reset_password"},
                   token_lifetime: {1, :hours},
                   purpose: :password_reset
                 )

        with_user_update_gate(user.id, fn release_gate ->
          reset =
            start_independent_task(:password_reset, fn ->
              Accounts.reset_password(reset_token, %{
                password: "replacement-password",
                password_confirmation: "replacement-password"
              })
            end)

          await_backend_lock(reset.backend_pid)

          old_password_sign_in =
            start_independent_task(:old_password_sign_in, fn ->
              Accounts.sign_in_with_password(%{
                email: to_string(user.email),
                password: "password1"
              })
            end)

          await_backend_lock(old_password_sign_in.backend_pid)
          release_gate.()

          assert {:ok, _updated_user} = Task.await(reset.task, :infinity)
          assert {:ok, _user} = Task.await(old_password_sign_in.task, :infinity)
          assert {:error, :invalid_token} = Token.valid_for_purpose?(browser_token, "user")
          assert {:ok, []} = Accounts.list_sessions(user)
        end)
      after
        delete_user_with_tokens(user)
      end
    end)
  end

  defp password_user do
    email = "password-change-#{System.unique_integer([:positive])}@example.com"

    {:ok, user} =
      Accounts.bootstrap_admin(email, "password1")

    user
  end

  defp assert_only_session(user, token) do
    {:ok, %{"jti" => replacement_jti}} = Jwt.peek(token)
    assert {:ok, [%{jti: ^replacement_jti}]} = Accounts.list_sessions(user)
  end

  defp start_independent_task(label, operation) do
    test_pid = self()

    task =
      Task.async(fn ->
        :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

        try do
          send(test_pid, {:independent_task_started, label, self(), database_backend_pid()})
          operation.()
        after
          :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
        end
      end)

    task_pid = task.pid
    assert_receive {:independent_task_started, ^label, ^task_pid, backend_pid}
    %{task: task, backend_pid: backend_pid}
  end

  defp with_user_update_gate(user_id, fun) do
    suffix = Base.encode16(:crypto.strong_rand_bytes(6), case: :lower)
    function_name = "taskman_password_update_gate_#{suffix}"
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

    case Repo.get(User, user.id) do
      nil -> :ok
      persisted_user -> Repo.delete!(persisted_user)
    end
  end
end
