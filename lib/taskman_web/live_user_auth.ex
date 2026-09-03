defmodule TaskmanWeb.LiveUserAuth do
  @moduledoc """
  Authentication boundary for Taskman's browser LiveViews.

  AshAuthenticationPhoenix loads `current_user` from the stored token before
  this hook runs. This module applies Taskman's active-and-confirmed account
  requirement, exposes the actor as both `current_user` and `current_scope`,
  and gives every session a stable return path and socket identity.
  """

  import Phoenix.Component, only: [assign: 3]
  import Plug.Conn, only: [put_session: 3]

  alias Phoenix.LiveView.Socket
  alias Plug.Conn
  alias Taskman.Accounts.Administration
  alias Taskman.Accounts.Token

  @public_paths [
    "/sign-in",
    "/setup",
    "/confirm-email",
    "/reset-password",
    "/auth"
  ]

  @admin_users_path "/admin?domain=Accounts&resource=User"

  @doc """
  LiveView `on_mount` hook for routes requiring an active confirmed User.
  """
  @spec on_mount(atom(), map(), map(), Socket.t()) :: {:cont | :halt, Socket.t()}
  def on_mount(:require_authenticated, _params, session, socket) do
    user = socket.assigns[:current_user]
    token = session["user_token"]

    if active_confirmed_user?(user) and is_binary(token) do
      {:cont, assign_authenticated(socket, user, token)}
    else
      return_to = session |> Map.get("return_to", "/") |> safe_return_path()
      {:halt, Phoenix.LiveView.redirect(socket, to: sign_in_path(return_to))}
    end
  end

  def on_mount(:admin_required, _params, session, socket) do
    user = socket.assigns[:current_user]
    token = session["user_token"]

    if active_admin_user?(user) and is_binary(token) do
      {:cont,
       socket
       |> assign_authenticated(user, token)
       |> Phoenix.LiveView.attach_hook(
         :taskman_admin_event_security,
         :handle_event,
         &secure_admin_event/3
       )
       |> Phoenix.LiveView.attach_hook(
         :taskman_admin_route_security,
         :handle_params,
         &secure_admin_route/3
       )}
    else
      return_to = (session["return_to"] || session["request_path"] || "/") |> safe_return_path()
      {:halt, Phoenix.LiveView.redirect(socket, to: sign_in_path(return_to))}
    end
  end

  def on_mount(:mount_current_user, _params, _session, socket) do
    {:cont, assign(socket, :current_scope, socket.assigns[:current_user])}
  end

  def on_mount(_, _params, _session, socket), do: {:cont, socket}

  @doc """
  Supplies the extra signed LiveView session values used by the auth hook.
  """
  @spec generate_session(Conn.t()) :: map()
  def generate_session(conn) do
    %{"return_to" => safe_return_path(request_path(conn))}
  end

  @doc """
  Captures a protected request's path before LiveView redirects the guest.

  The value is held in the signed Phoenix session so the sign-in POST can send
  the user back to the page they originally requested. Public authentication
  paths never overwrite it.
  """
  @spec capture_return_path(Conn.t(), keyword()) :: Conn.t()
  def capture_return_path(%Conn{} = conn, _opts) do
    path = request_path(conn)

    cond do
      conn.assigns[:current_user] -> conn
      public_path?(path) -> capture_sign_in_return_path(conn, path)
      true -> put_session(conn, :return_to, safe_return_path(path))
    end
  end

  @doc "Returns a safe internal path or `/` for untrusted input."
  @spec safe_return_path(term()) :: String.t()
  def safe_return_path(path) when is_binary(path) do
    path = String.trim(path)
    uri = URI.parse(path)
    uri_path = uri.path || ""

    cond do
      path == "" ->
        "/"

      String.contains?(path, ["\\", "\r", "\n", "\0"]) ->
        "/"

      uri.scheme not in [nil, ""] ->
        "/"

      uri.host not in [nil, ""] ->
        "/"

      String.starts_with?(path, "//") ->
        "/"

      not String.starts_with?(uri_path, "/") ->
        "/"

      true ->
        uri
        |> Map.put(:fragment, nil)
        |> URI.to_string()
    end
  end

  def safe_return_path(_path), do: "/"

  @doc "Builds the public sign-in URL while retaining a safe return path."
  @spec sign_in_path(term()) :: String.t()
  def sign_in_path("/"), do: "/sign-in"

  def sign_in_path(return_to) do
    return_to = safe_return_path(return_to)
    "/sign-in?" <> URI.encode_query(%{"return_to" => return_to})
  end

  @doc "Returns the session-specific socket topic for a browser token."
  @spec session_topic(String.t()) :: String.t()
  def session_topic(token), do: Token.session_topic(token)

  defp active_confirmed_user?(%{status: :active, confirmed_at: confirmed_at})
       when not is_nil(confirmed_at),
       do: true

  defp active_confirmed_user?(_), do: false

  defp active_admin_user?(%{status: :active, confirmed_at: confirmed_at, admin?: true})
       when not is_nil(confirmed_at),
       do: true

  defp active_admin_user?(_), do: false

  defp secure_admin_event(event, _params, socket)
       when event in ["toggle_authorizing", "clear_actor"] do
    {:halt, socket}
  end

  defp secure_admin_event(_event, _params, socket), do: {:cont, socket}

  defp secure_admin_route(params, _url, socket) do
    if Administration.persisted_active_administrator?(socket.assigns[:current_user]) do
      case admin_user_destination(params) do
        {:inspection, user_id} ->
          {:halt,
           Phoenix.LiveView.push_navigate(socket,
             to: "/admin/users/#{user_id}"
           )}

        :users_table ->
          {:halt, Phoenix.LiveView.push_navigate(socket, to: @admin_users_path)}

        :none ->
          {:cont, socket}
      end
    else
      {:halt, Phoenix.LiveView.redirect(socket, to: sign_in_path("/admin"))}
    end
  end

  defp admin_user_destination(%{"primary_key" => primary_key}) when is_binary(primary_key) do
    case Ecto.UUID.cast(primary_key) do
      {:ok, user_id} -> {:inspection, user_id}
      :error -> :users_table
    end
  end

  defp admin_user_destination(%{"primary_key" => _primary_key}), do: :users_table
  defp admin_user_destination(_params), do: :none

  defp assign_authenticated(socket, user, token) do
    socket
    |> assign(:current_user, user)
    |> assign(:current_scope, user)
    |> assign(:current_session_token, token)
    |> assign(:current_session_id, session_topic(token))
  end

  defp request_path(%Conn{request_path: path, query_string: query}) do
    case query do
      "" -> path
      _ -> path <> "?" <> query
    end
  end

  defp request_path(_conn), do: "/"

  defp public_path?(path) do
    path = URI.parse(path).path || path

    Enum.any?(@public_paths, fn prefix ->
      path == prefix or String.starts_with?(path, prefix <> "/")
    end)
  end

  defp capture_sign_in_return_path(conn, path) do
    if sign_in_path?(path) and is_nil(Plug.Conn.get_session(conn, :return_to)) do
      case conn.params["return_to"] do
        nil -> conn
        return_to -> put_session(conn, :return_to, safe_return_path(return_to))
      end
    else
      conn
    end
  end

  defp sign_in_path?(path) do
    path = URI.parse(path).path || path
    path == "/sign-in" or String.starts_with?(path, "/sign-in/")
  end
end
