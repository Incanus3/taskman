defmodule TaskmanWeb.AccountSettingsLive do
  use TaskmanWeb, :live_view

  alias AshAuthentication.Jwt
  alias Taskman.Accounts

  @api_key_default_lifetime_days 365

  @impl true
  def mount(_params, _session, socket) do
    socket =
      socket
      |> assign_new(:current_scope, fn -> nil end)
      |> assign_new(:current_user, fn -> nil end)
      |> stream_configure(:sessions, dom_id: &"session-#{&1.jti}")
      |> stream_configure(:api_keys, dom_id: &"api-key-#{&1.id}")
      |> assign(:email_form, email_form())
      |> assign(:password_form, password_form())
      |> assign(:api_key_form, api_key_form())
      |> assign(:delete_account_form, delete_account_form())
      |> assign(:pending_email_change, nil)
      |> assign(:email_change_retry_after, nil)
      |> assign(:plaintext_api_key, nil)
      |> assign(:plaintext_api_key_generation, 0)
      |> refresh_email_state()
      |> refresh_sessions()
      |> refresh_api_keys()

    {:ok, socket}
  end

  @impl true
  def handle_params(_params, _uri, socket) do
    {:noreply, assign(socket, :plaintext_api_key, nil)}
  end

  @impl true
  def handle_event("request_email_change", %{"email_change" => params}, socket) do
    email = Map.get(params, "email", "")
    current_password = Map.get(params, "current_password", "")

    case Accounts.request_email_change(socket.assigns.current_user, email, current_password) do
      {:ok, _user} ->
        {:noreply,
         socket
         |> refresh_email_state()
         |> assign(:email_change_retry_after, nil)
         |> put_flash(:info, "Email confirmation sent.")}

      {:error, retry_after: retry_after} ->
        {:noreply,
         socket
         |> assign(:email_form, to_form(params, as: :email_change))
         |> assign(:email_change_retry_after, retry_after)
         |> put_flash(:error, retry_guidance(retry_after))}

      {:error, _reason} ->
        {:noreply,
         socket
         |> assign(:email_form, to_form(params, as: :email_change))
         |> put_flash(:error, "Unable to request an email change.")}
    end
  end

  def handle_event("revoke-session", %{"jti" => jti}, socket) do
    socket =
      case Accounts.revoke_session(socket.assigns.current_user, jti) do
        :ok -> socket |> refresh_sessions() |> put_flash(:info, "Session revoked.")
        {:error, _reason} -> put_flash(socket, :error, "Unable to revoke that session.")
      end

    {:noreply, socket}
  end

  def handle_event("revoke-other-sessions", _params, socket) do
    socket =
      case Accounts.revoke_other_sessions(
             socket.assigns.current_user,
             socket.assigns.current_session_token
           ) do
        :ok -> socket |> refresh_sessions() |> put_flash(:info, "Other sessions revoked.")
        {:error, _reason} -> put_flash(socket, :error, "Unable to revoke other sessions.")
      end

    {:noreply, socket}
  end

  def handle_event("create-api-key", %{"api_key" => params}, socket) do
    socket =
      case api_key_expiry(params) do
        {:ok, expires_at, now} ->
          case Accounts.create_api_key(
                 socket.assigns.current_user,
                 %{name: Map.get(params, "name", ""), expires_at: expires_at},
                 now: now
               ) do
            {:ok, %{api_key: api_key, plaintext: plaintext}} ->
              socket
              |> stream_insert(:api_keys, api_key)
              |> assign(:api_key_form, api_key_form())
              |> assign(:plaintext_api_key, plaintext)
              |> update(:plaintext_api_key_generation, &(&1 + 1))

            {:error, _reason} ->
              socket
              |> assign(:api_key_form, to_form(params, as: :api_key))
              |> put_flash(:error, "Unable to create an API key.")
          end

        :error ->
          socket
          |> assign(:api_key_form, to_form(params, as: :api_key))
          |> put_flash(:error, "Choose an expiration between 1 and 365 days.")
      end

    {:noreply, socket}
  end

  def handle_event("revoke-api-key", %{"id" => id}, socket) do
    socket =
      case Accounts.revoke_api_key(socket.assigns.current_user, id) do
        :ok -> socket |> refresh_api_keys() |> put_flash(:info, "API key revoked.")
        {:error, _reason} -> put_flash(socket, :error, "Unable to revoke that API key.")
      end

    {:noreply, socket}
  end

  defp refresh_sessions(socket) do
    case Accounts.list_sessions(socket.assigns.current_user) do
      {:ok, sessions} ->
        stream(socket, :sessions, mark_current_session(sessions, socket), reset: true)

      {:error, _reason} ->
        stream(socket, :sessions, [], reset: true)
    end
  end

  defp refresh_email_state(socket) do
    case Accounts.account_settings_state(socket.assigns.current_user) do
      {:ok, %{user: user, pending_email: pending_email}} ->
        socket
        |> assign(:current_user, user)
        |> assign(:pending_email_change, pending_email)
        |> assign(:email_form, email_form())

      {:error, _reason} ->
        socket
    end
  end

  defp refresh_api_keys(socket) do
    case Accounts.list_api_keys(socket.assigns.current_user) do
      {:ok, api_keys} -> stream(socket, :api_keys, api_keys, reset: true)
      {:error, _reason} -> stream(socket, :api_keys, [], reset: true)
    end
  end

  defp mark_current_session(sessions, socket) do
    current_jti = session_jti(socket.assigns.current_session_token)

    Enum.map(sessions, fn session -> Map.put(session, :current?, session.jti == current_jti) end)
  end

  defp session_jti(token) when is_binary(token) do
    case Jwt.peek(token) do
      {:ok, %{"jti" => jti}} when is_binary(jti) -> jti
      _ -> nil
    end
  end

  defp session_jti(_token), do: nil

  defp api_key_expiry(params) do
    case Integer.parse(Map.get(params, "expires_in_days", "")) do
      {days, ""} when days in 1..@api_key_default_lifetime_days ->
        now = DateTime.utc_now()
        {:ok, DateTime.add(now, days * 86_400, :second), now}

      _ ->
        :error
    end
  end

  defp email_form do
    to_form(%{"email" => "", "current_password" => ""}, as: :email_change)
  end

  defp password_form do
    to_form(%{"current_password" => "", "password" => "", "password_confirmation" => ""},
      as: :password_change
    )
  end

  defp api_key_form do
    to_form(%{"name" => "", "expires_in_days" => to_string(@api_key_default_lifetime_days)},
      as: :api_key
    )
  end

  defp delete_account_form do
    to_form(%{"current_password" => "", "confirmation" => ""}, as: :delete_account)
  end

  defp retry_guidance(retry_after),
    do: "Too many requests. Please try again in #{retry_after} seconds."
end
