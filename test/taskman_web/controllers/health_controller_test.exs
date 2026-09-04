defmodule TaskmanWeb.HealthControllerTest do
  use TaskmanWeb.ConnCase, async: false

  defmodule UnavailableHealth do
    def check(_options), do: :unavailable
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

  test "POST /healthz is not routed", %{conn: conn} do
    conn = post(conn, "/healthz")
    assert conn.status == 404
  end
end
