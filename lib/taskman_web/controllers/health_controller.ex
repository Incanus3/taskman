defmodule TaskmanWeb.HealthController do
  use TaskmanWeb, :controller

  require Logger

  alias Taskman.Health

  @spec show(Plug.Conn.t(), map()) :: Plug.Conn.t()
  def show(conn, _params) do
    health_module = Application.get_env(:taskman, :health_module, Health)

    case guarded_health_check(health_module) do
      :ready -> respond(conn, 200, "ready")
      :unavailable -> respond(conn, 503, "unavailable")
    end
  end

  defp guarded_health_check(health_module) do
    try do
      case health_module.check([]) do
        :ready -> :ready
        :unavailable -> :unavailable
        _unexpected -> unavailable_with_log()
      end
    rescue
      _exception -> unavailable_with_log()
    catch
      _kind, _reason -> unavailable_with_log()
    end
  end

  defp unavailable_with_log do
    Logger.warning("Taskman health check failed")
    :unavailable
  end

  defp respond(conn, status, body) do
    conn
    |> put_resp_header("cache-control", "no-store")
    |> put_status(status)
    |> text(body)
  end
end
