defmodule TaskmanWeb.HealthControllerTest do
  use TaskmanWeb.ConnCase, async: false

  import ExUnit.CaptureLog

  defmodule UnavailableHealth do
    def check(_options), do: :unavailable
  end

  defmodule RaisingHealth do
    def check(_options), do: raise("controller secret detail")
  end

  defmodule ThrowingHealth do
    def check(_options), do: throw("controller secret detail")
  end

  defmodule ExitingHealth do
    def check(_options), do: exit("controller secret detail")
  end

  defmodule UnexpectedHealth do
    def check(_options), do: {:unexpected, "controller secret detail"}
  end

  setup do
    previous_health_module = Application.get_env(:taskman, :health_module)

    on_exit(fn ->
      case previous_health_module do
        nil -> Application.delete_env(:taskman, :health_module)
        health_module -> Application.put_env(:taskman, :health_module, health_module)
      end
    end)

    :ok
  end

  test "GET /healthz is unauthenticated and returns a fixed ready response", %{conn: conn} do
    conn = get(conn, "/healthz")

    assert conn.status == 200
    assert conn.resp_body == "ready"
    assert get_resp_header(conn, "content-type") == ["text/plain; charset=utf-8"]
    assert get_resp_header(conn, "cache-control") == ["no-store"]
  end

  test "GET /healthz reports unavailable with the fixed response", %{conn: conn} do
    Application.put_env(:taskman, :health_module, UnavailableHealth)

    conn = get(conn, "/healthz")

    assert conn.status == 503
    assert conn.resp_body == "unavailable"
    assert get_resp_header(conn, "content-type") == ["text/plain; charset=utf-8"]
    assert get_resp_header(conn, "cache-control") == ["no-store"]
  end

  for {label, health_module} <- [
        {"an exception", RaisingHealth},
        {"a thrown failure", ThrowingHealth},
        {"an exited failure", ExitingHealth}
      ] do
    test "GET /healthz maps #{label} to a fixed unavailable response", %{conn: conn} do
      Application.put_env(:taskman, :health_module, unquote(health_module))

      log =
        capture_log(fn ->
          conn = get(conn, "/healthz")

          assert conn.status == 503
          assert conn.resp_body == "unavailable"
          assert get_resp_header(conn, "content-type") == ["text/plain; charset=utf-8"]
          assert get_resp_header(conn, "cache-control") == ["no-store"]
        end)

      assert log =~ "Taskman health check failed"
      refute log =~ "controller secret detail"
    end
  end

  test "GET /healthz logs and maps an unexpected health result to unavailable", %{conn: conn} do
    Application.put_env(:taskman, :health_module, UnexpectedHealth)

    log =
      capture_log(fn ->
        conn = get(conn, "/healthz")

        assert conn.status == 503
        assert conn.resp_body == "unavailable"
        assert get_resp_header(conn, "content-type") == ["text/plain; charset=utf-8"]
        assert get_resp_header(conn, "cache-control") == ["no-store"]
      end)

    assert log =~ "Taskman health check failed"
    refute log =~ "controller secret detail"
  end

  test "POST /healthz is not routed", %{conn: conn} do
    conn = post(conn, "/healthz")
    assert conn.status == 404
  end
end
