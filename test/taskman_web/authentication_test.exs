defmodule TaskmanWeb.AuthenticationTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Plug.Conn
  import Swoosh.TestAssertions
  import Taskman.AccountsFixtures

  alias AshAuthentication.{Jwt, TokenResource}
  alias Taskman.Accounts.Token
  alias TaskmanWeb.{AuthController, LiveUserAuth}

  setup :set_swoosh_global

  test "workspace redirects guests to sign-in with a safe internal return path", %{conn: conn} do
    assert {:error, {:redirect, %{to: to}}} = live(conn, "/projects/42/tasks/new")
    assert to == "/sign-in?return_to=%2Fprojects%2F42%2Ftasks%2Fnew"
  end

  test "external and unsafe return paths are discarded" do
    assert LiveUserAuth.safe_return_path("/projects/42") == "/projects/42"
    assert LiveUserAuth.safe_return_path("https://evil.example/phish") == "/"
    assert LiveUserAuth.safe_return_path("//evil.example/phish") == "/"
    assert LiveUserAuth.safe_return_path("javascript:alert(1)") == "/"
    assert LiveUserAuth.safe_return_path(nil) == "/"
  end

  test "a sign-in query can seed only a safe session return path", %{conn: conn} do
    conn = get(conn, "/sign-in?return_to=https%3A%2F%2Fevil.example%2Fphish")
    assert get_session(conn, :return_to) == "/"

    conn = get(build_conn(), "/sign-in?return_to=%2Fprojects%2F42")
    assert get_session(conn, :return_to) == "/projects/42"
  end

  test "registration is not a route", %{conn: conn} do
    conn = get(conn, "/register")
    assert conn.status == 404
  end

  test "active users can open every Project route", %{conn: conn} do
    user = user_fixture()
    project = Taskman.ProjectsFixtures.project_fixture(%{})
    list = Taskman.ListsFixtures.list_fixture(project)
    task = Taskman.TasksFixtures.task_fixture(project)
    conn = TaskmanWeb.ConnCase.log_in_user(conn, user)

    for path <- [
          "/",
          "/projects/#{project.id}",
          "/projects/#{project.id}/tasks/new",
          "/projects/#{project.id}/tasks/#{task.id}",
          "/projects/#{project.id}/lists/#{list.id}",
          "/projects/#{project.id}/lists/#{list.id}/tasks/new",
          "/projects/#{project.id}/lists/#{list.id}/tasks/#{task.id}"
        ] do
      assert {:ok, _view, _html} = live(conn, path), path
    end
  end

  test "authenticated LiveViews expose the actor through current_user and current_scope", %{
    conn: conn
  } do
    user = user_fixture()
    conn = log_in_user(conn, user)
    {:ok, view, _html} = live(conn, "/")

    assert has_element?(view, "#authenticated-navigation")
    assert has_element?(view, "#account-menu")
    assert has_element?(view, "#account-identity")
    assert has_element?(view, "#account-sign-out-link")
  end

  test "pending and disabled users are rejected by the authenticated session", %{conn: conn} do
    pending = pending_user_fixture()
    disabled = user_fixture(%{status: :disabled})

    for user <- [pending, disabled] do
      assert {:error, {:redirect, %{to: "/sign-in" <> _}}} = live(conn, "/")
      assert :error = Jwt.token_for_user(user, %{}, purpose: :user)
    end
  end

  test "sign-out is CSRF protected", %{conn: conn} do
    user = user_fixture()
    conn = TaskmanWeb.ConnCase.log_in_user(conn, user)

    conn_without_test_bypass = %{
      conn
      | private: Map.delete(conn.private, :plug_skip_csrf_protection)
    }

    assert_raise Plug.CSRFProtection.InvalidCSRFTokenError, fn ->
      delete(conn_without_test_bypass, "/sign-out")
    end

    conn = get(conn, "/sign-out")
    assert conn.status == 404

    {:ok, view, _html} = live(conn_without_test_bypass, "/")

    assert has_element?(
             view,
             "#account-sign-out-link[data-method='delete'][data-to='/sign-out']"
           )
  end

  test "sign-out revokes the browser token and disconnects its socket", %{conn: conn} do
    user = user_fixture()
    conn = log_in_user(conn, user)
    token = get_session(conn, :user_token)
    socket_id = get_session(conn, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(socket_id)

    conn = delete(conn, "/sign-out")

    assert redirected_to(conn) == "/sign-in"
    assert {:error, :invalid_token} = Token.valid_for_purpose?(token, "user")
    assert_receive %Phoenix.Socket.Broadcast{topic: ^socket_id, event: "disconnect"}
  end

  test "sign-in success stores a 30-day browser token and session-specific socket id", %{
    conn: conn
  } do
    user = user_fixture()
    conn = log_in_user(conn, user)
    token = get_session(conn, :user_token)

    assert is_binary(token)
    assert get_session(conn, :live_socket_id) == AuthController.session_topic(token)

    assert {:ok, [_]} =
             TokenResource.Actions.get_token(Token, %{"jti" => jti(token), "purpose" => "user"})

    assert {:ok, %{"exp" => expires_at}} = Jwt.peek(token)
    lifetime = expires_at - System.system_time(:second)
    assert lifetime in (30 * 24 * 60 * 60 - 5)..(30 * 24 * 60 * 60 + 5)
  end

  test "password sign-in follows the stored-session callback", %{conn: conn} do
    email = "signin-#{System.unique_integer([:positive])}@example.com"

    assert {:ok, _user} =
             Taskman.Accounts.bootstrap_admin(%{
               email: email,
               password: "fixture-password",
               password_confirmation: "fixture-password"
             })

    conn = get(conn, "/sign-in?return_to=%2Fprojects%2F42")
    document = LazyHTML.from_fragment(html_response(conn, 200))
    refute Enum.empty?(LazyHTML.query(document, "form[action='/auth/user/password/sign_in']"))
    refute Enum.empty?(LazyHTML.query(document, "input[name='user[email]']"))
    refute Enum.empty?(LazyHTML.query(document, "input[name='user[password]']"))

    conn =
      post(conn, "/auth/user/password/sign_in", %{
        "user" => %{"email" => email, "password" => "fixture-password"}
      })

    assert redirected_to(conn) == "/projects/42"
    assert is_binary(get_session(conn, :user_token))
  end

  test "sign-in attempts receive enumeration-safe 429 retry guidance after the email limit", %{
    conn: conn
  } do
    email = "limited-#{System.unique_integer([:positive])}@example.com"

    assert {:ok, _user} =
             Taskman.Accounts.bootstrap_admin(%{
               email: email,
               password: "fixture-password",
               password_confirmation: "fixture-password"
             })

    for _ <- 1..10 do
      response =
        conn
        |> recycle()
        |> post("/auth/user/password/sign_in", %{
          "user" => %{"email" => email, "password" => "wrong-password"}
        })

      assert redirected_to(response) =~ "/sign-in"
    end

    response =
      conn
      |> recycle()
      |> post("/auth/user/password/sign_in", %{
        "user" => %{"email" => email, "password" => "wrong-password"}
      })

    assert response.status == 429
    assert response.resp_body == "Too many requests. Please try again later."
    assert [retry_after] = get_resp_header(response, "retry-after")
    assert {seconds, ""} = Integer.parse(retry_after)
    assert seconds >= 1
  end

  test "password-reset requests receive 429 guidance before issuing a sixth token", %{conn: conn} do
    email = "reset-limited-#{System.unique_integer([:positive])}@example.com"

    assert {:ok, _user} =
             Taskman.Accounts.bootstrap_admin(%{
               email: email,
               password: "fixture-password",
               password_confirmation: "fixture-password"
             })

    for _ <- 1..5 do
      response =
        conn
        |> recycle()
        |> post("/auth/user/password/reset_request", %{"user" => %{"email" => email}})

      assert redirected_to(response) == "/"
      assert_receive {:email, _email}
    end

    response =
      conn
      |> recycle()
      |> post("/auth/user/password/reset_request", %{"user" => %{"email" => email}})

    assert response.status == 429
    assert response.resp_body == "Too many requests. Please try again later."
    assert [retry_after] = get_resp_header(response, "retry-after")
    assert {seconds, ""} = Integer.parse(retry_after)
    assert seconds >= 1
    refute_email_sent()
  end

  test "password sign-in renews the Plug session and retires the previous browser token", %{
    conn: conn
  } do
    email = "renew-#{System.unique_integer([:positive])}@example.com"

    assert {:ok, user} =
             Taskman.Accounts.bootstrap_admin(%{
               email: email,
               password: "fixture-password",
               password_confirmation: "fixture-password"
             })

    conn = log_in_user(conn, user)
    old_token = get_session(conn, :user_token)
    old_socket = get_session(conn, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(old_socket)

    conn =
      post(conn, "/auth/user/password/sign_in", %{
        "user" => %{"email" => email, "password" => "fixture-password"}
      })

    assert conn.private.plug_session_info == :renew
    new_token = get_session(conn, :user_token)
    refute new_token == old_token
    assert :ok = Token.valid_for_purpose?(new_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(old_token, "user")
    assert_receive %Phoenix.Socket.Broadcast{topic: ^old_socket, event: "disconnect"}
  end

  test "a new user sign-in retires a valid browser token from the prior user", %{conn: conn} do
    previous_user = user_fixture()
    next_user = user_fixture()
    conn = log_in_user(conn, previous_user)
    old_token = get_session(conn, :user_token)
    old_socket = get_session(conn, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(old_socket)

    conn = AuthController.log_in_user(conn, next_user)
    new_token = get_session(conn, :user_token)

    assert :ok = Token.valid_for_purpose?(new_token, "user")
    assert {:error, :invalid_token} = Token.valid_for_purpose?(old_token, "user")
    assert_receive %Phoenix.Socket.Broadcast{topic: ^old_socket, event: "disconnect"}
  end

  defp jti(token) do
    {:ok, %{"jti" => jti}} = Jwt.peek(token)
    jti
  end
end
