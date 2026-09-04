defmodule TaskmanWeb.HealthController do
  use TaskmanWeb, :controller

  alias Taskman.Health

  @spec show(Plug.Conn.t(), map()) :: Plug.Conn.t()
  def show(conn, _params) do
    health_module = Application.get_env(:taskman, :health_module, Health)

    case health_module.check([]) do
      :ready -> respond(conn, 200, "ready")
      :unavailable -> respond(conn, 503, "unavailable")
      _unexpected -> respond(conn, 503, "unavailable")
    end
  end

  defp respond(conn, status, body) do
    conn
    |> put_resp_header("cache-control", "no-store")
    |> put_status(status)
    |> text(body)
  end
end
