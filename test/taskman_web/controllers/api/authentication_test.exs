defmodule TaskmanWeb.API.AuthenticationTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.ConnTest
  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures

  alias Taskman.Accounts
  alias Taskman.Repo

  @api_key_lifetime_seconds 365 * 86_400

  test "missing, malformed, wrong-scheme, duplicate, query-only, and cookie-only credentials return the exact 401 envelope",
       %{conn: conn} do
    project_fixture(%{})

    user = user_fixture()

    assert {:ok, %{plaintext: plaintext}} =
             Accounts.create_api_key(user, %{
               name: "query-only",
               expires_at: DateTime.add(DateTime.utc_now(), @api_key_lifetime_seconds, :second)
             })

    expected = %{
      "error" => %{
        "code" => "unauthorized",
        "message" => "Authentication required"
      }
    }

    for request <- [
          fn c -> get(c, "/api/v1/projects") end,
          fn c -> put_req_header(c, "authorization", "Bearer") |> get("/api/v1/projects") end,
          fn c ->
            put_req_header(c, "authorization", "Basic tm_secret") |> get("/api/v1/projects")
          end,
          fn c ->
            put_req_header(c, "authorization", "Bearer tm_not-a-valid-key")
            |> get("/api/v1/projects")
          end,
          fn c ->
            get(c, "/api/v1/projects?" <> URI.encode_query(%{"api_key" => plaintext}))
          end
        ] do
      assert expected == request.(recycle(conn)) |> json_response(401)
    end

    cookie_conn = init_test_session(conn, %{"user" => "browser-session-only"})
    assert expected == get(cookie_conn, "/api/v1/projects") |> json_response(401)

    duplicate_conn =
      conn
      |> put_req_header("authorization", "Bearer tm_one")
      |> put_req_header("authorization", "Bearer tm_two")

    assert expected == get(duplicate_conn, "/api/v1/projects") |> json_response(401)
  end

  test "a valid key preserves the existing success and error envelopes", %{conn: conn} do
    user = user_fixture()

    assert {:ok, %{plaintext: plaintext}} =
             Accounts.create_api_key(user, %{
               name: "API tests",
               expires_at: DateTime.add(DateTime.utc_now(), @api_key_lifetime_seconds, :second)
             })

    project = project_fixture(%{})

    conn = put_api_key(conn, plaintext)
    assert %{"data" => _projects} = conn |> get("/api/v1/projects") |> json_response(200)

    assert %{"error" => %{"code" => "not_found", "message" => "Resource not found"}} =
             conn |> get("/api/v1/projects/#{project.id + 1}") |> json_response(404)
  end

  test "duplicate valid Authorization headers are ambiguous and rejected", %{conn: conn} do
    user = user_fixture()

    assert {:ok, %{plaintext: plaintext}} =
             Accounts.create_api_key(user, %{
               name: "duplicate",
               expires_at: DateTime.add(DateTime.utc_now(), @api_key_lifetime_seconds, :second)
             })

    conn =
      conn
      |> put_api_key(plaintext)
      |> prepend_req_headers([{"authorization", "Bearer " <> plaintext}])

    assert %{"error" => %{"code" => "unauthorized"}} =
             conn |> get("/api/v1/projects") |> json_response(401)
  end

  test "expired, revoked, pending-owner, and disabled-owner keys return 401", %{conn: conn} do
    pending = pending_user_fixture()
    disabled = user_fixture(status: :disabled)
    active = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{plaintext: revoked_key, api_key: revoked}} =
             Accounts.create_api_key(active, %{
               name: "revoked",
               expires_at: DateTime.add(now, @api_key_lifetime_seconds, :second)
             })

    assert :ok = Accounts.revoke_api_key(active, revoked.id)

    assert {:ok, %{plaintext: pending_plaintext, api_key: pending_record}} =
             Accounts.create_api_key(active, %{
               name: "pending",
               expires_at: DateTime.add(now, @api_key_lifetime_seconds, :second)
             })

    assert {:ok, _pending_record} =
             pending_record
             |> Ecto.Changeset.change(user_id: pending.id)
             |> Repo.update()

    assert {:ok, %{plaintext: disabled_plaintext, api_key: disabled_record}} =
             Accounts.create_api_key(active, %{
               name: "disabled",
               expires_at: DateTime.add(now, @api_key_lifetime_seconds, :second)
             })

    assert {:ok, _disabled_record} =
             disabled_record
             |> Ecto.Changeset.change(user_id: disabled.id)
             |> Repo.update()

    assert {:ok, %{plaintext: expired_plaintext, api_key: expired_record}} =
             Accounts.create_api_key(active, %{
               name: "expired",
               expires_at: DateTime.add(DateTime.utc_now(), 2 * 86_400, :second)
             })

    assert {:ok, _expired_record} =
             expired_record
             |> Ecto.Changeset.change(expires_at: DateTime.add(now, -1, :second))
             |> Repo.update()

    for key <- [revoked_key, pending_plaintext, disabled_plaintext, expired_plaintext] do
      assert %{"error" => %{"code" => "unauthorized"}} =
               conn |> put_api_key(key) |> get("/api/v1/projects") |> json_response(401)
    end
  end
end
