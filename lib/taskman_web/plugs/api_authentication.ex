defmodule TaskmanWeb.Plugs.ApiAuthentication do
  @moduledoc """
  Authenticates API requests with one `Authorization: Bearer tm_...` header.

  API requests deliberately do not consult browser session cookies or query
  parameters. Every authentication failure has the same JSON response.
  """

  @behaviour Plug

  import Plug.Conn

  alias Ash.PlugHelpers
  alias Taskman.Accounts

  @unauthorized_body %{
    error: %{
      code: "unauthorized",
      message: "Authentication required"
    }
  }

  @impl true
  def init(opts), do: opts

  @impl true
  def call(conn, _opts) do
    if api_request?(conn) do
      if conn.private[:taskman_api_authenticated?] do
        conn
      else
        authenticate(conn)
      end
    else
      conn
    end
  end

  defp authenticate(conn) do
    case bearer_credential(conn) do
      {:ok, credential} ->
        case Accounts.sign_in_with_api_key(%{api_key: credential}) do
          {:ok, user} ->
            conn
            |> PlugHelpers.set_actor(user)
            |> assign(:current_user, user)
            |> put_private(:taskman_api_authenticated?, true)

          {:error, _reason} ->
            unauthorized(conn)
        end

      {:error, _reason} ->
        unauthorized(conn)
    end
  end

  defp api_request?(conn) do
    path = conn.request_path || ""
    path == "/api/v1" or String.starts_with?(path, "/api/v1/")
  end

  defp bearer_credential(conn) do
    case get_req_header(conn, "authorization") do
      [value] -> parse_bearer_header(value)
      [] -> {:error, :missing}
      _ -> {:error, :ambiguous}
    end
  end

  defp parse_bearer_header(value) when is_binary(value) do
    case Regex.run(~r/\ABearer ([^\s]+)\z/, value, capture: :all_but_first) do
      [credential] -> {:ok, credential}
      _ -> {:error, :malformed}
    end
  end

  defp parse_bearer_header(_value), do: {:error, :malformed}

  defp unauthorized(conn) do
    conn
    |> put_resp_content_type("application/json")
    |> send_resp(401, Jason.encode!(@unauthorized_body))
    |> halt()
  end
end
