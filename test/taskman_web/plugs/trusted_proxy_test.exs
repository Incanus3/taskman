defmodule TaskmanWeb.Plugs.TrustedProxyTest do
  use ExUnit.Case, async: true

  import Plug.Conn
  import Plug.Test

  alias Ash.PlugHelpers
  alias TaskmanWeb.Plugs.TrustedProxy

  test "accepts a complete forwarded chain only from the loopback proxy" do
    conn =
      conn(:get, "/")
      |> put_req_header("x-forwarded-for", "198.51.100.7, 127.0.0.1")
      |> put_req_header("x-forwarded-proto", "https")
      |> with_remote_ip({127, 0, 0, 1})
      |> TrustedProxy.call([])

    assert conn.remote_ip == {198, 51, 100, 7}
    assert conn.scheme == :https
    assert PlugHelpers.get_context(conn) == %{remote_ip: "198.51.100.7"}
  end

  test "ignores forwarded spoofing from a public immediate peer" do
    conn =
      conn(:get, "/")
      |> put_req_header("x-forwarded-for", "198.51.100.7")
      |> put_req_header("x-forwarded-proto", "https")
      |> with_remote_ip({203, 0, 113, 20})
      |> TrustedProxy.call([])

    assert conn.remote_ip == {203, 0, 113, 20}
    assert conn.scheme == :http
    assert PlugHelpers.get_context(conn) == %{remote_ip: "203.0.113.20"}
  end

  test "ignores malformed, ambiguous, and unparseable forwarded headers" do
    for headers <- [
          [{"x-forwarded-for", "not-an-address"}],
          [{"x-forwarded-for", "198.51.100.7, not-an-address"}],
          [{"x-forwarded-for", "198.51.100.7"}, {"x-forwarded-for", "198.51.100.8"}],
          [{"x-forwarded-proto", "https"}, {"x-forwarded-proto", "http"}]
        ] do
      conn =
        conn(:get, "/")
        |> prepend_req_headers(headers)
        |> with_remote_ip({127, 0, 0, 1})
        |> TrustedProxy.call([])

      assert conn.remote_ip == {127, 0, 0, 1}
      assert conn.scheme == :http
    end
  end

  defp with_remote_ip(conn, remote_ip), do: %{conn | remote_ip: remote_ip}
end
