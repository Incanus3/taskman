defmodule TaskmanWeb.Plugs.TrustedProxy do
  @moduledoc """
  Accepts forwarded client information only from the local reverse proxy.

  Caddy is the immediate loopback peer in the supported production topology.
  A public peer can therefore never select an address or HTTPS scheme by
  supplying forwarding headers directly.
  """

  @behaviour Plug

  import Plug.Conn

  alias Ash.PlugHelpers
  alias Taskman.Accounts.RateLimit

  @impl true
  def init(opts), do: opts

  @impl true
  def call(conn, _opts) do
    conn = if loopback?(conn.remote_ip), do: forwarded(conn), else: conn
    remote_ip = RateLimit.normalized_ip(conn.remote_ip)
    :ok = RateLimit.put_request_remote_ip(remote_ip)
    PlugHelpers.set_context(conn, %{remote_ip: remote_ip})
  end

  defp forwarded(conn) do
    conn
    |> maybe_forwarded_ip()
    |> maybe_forwarded_scheme()
  end

  defp maybe_forwarded_ip(conn) do
    case forwarded_addresses(get_req_header(conn, "x-forwarded-for")) do
      {:ok, [address | _rest]} -> %{conn | remote_ip: address}
      :error -> conn
    end
  end

  defp maybe_forwarded_scheme(conn) do
    case get_req_header(conn, "x-forwarded-proto") do
      [value] when is_binary(value) ->
        if String.downcase(String.trim(value)) == "https",
          do: %{conn | scheme: :https},
          else: conn

      _ ->
        conn
    end
  end

  defp forwarded_addresses([header]) when is_binary(header) do
    header
    |> String.split(",")
    |> Enum.map(&parse_address/1)
    |> case do
      [] ->
        :error

      parsed ->
        if Enum.all?(parsed, &match?({:ok, _address}, &1)) do
          {:ok, Enum.map(parsed, fn {:ok, address} -> address end)}
        else
          :error
        end
    end
  end

  defp forwarded_addresses(_headers), do: :error

  defp parse_address(address) do
    case :inet.parse_address(address |> String.trim() |> String.to_charlist()) do
      {:ok, parsed} -> {:ok, parsed}
      {:error, _reason} -> :error
    end
  end

  defp loopback?({127, _second, _third, _fourth}), do: true
  defp loopback?({0, 0, 0, 0, 0, 0, 0, 1}), do: true
  defp loopback?(_address), do: false
end
