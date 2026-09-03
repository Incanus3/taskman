defmodule TaskmanWeb.AdminUserLive do
  use TaskmanWeb, :live_view

  alias Taskman.Accounts
  alias Taskman.Accounts.User

  require Ash.Query

  @impl true
  def mount(_params, _session, socket) do
    {:ok,
     socket
     |> assign_new(:current_scope, fn -> nil end)
     |> assign_new(:current_user, fn -> nil end)
     |> assign(:user, nil)
     |> assign(:resend_invitation_retry_after, nil)
     |> assign(:lifecycle_form, to_form(%{}, as: :lifecycle))
     |> assign(:email_form, to_form(%{}, as: :email))
     |> assign(:delete_form, to_form(%{}, as: :delete))}
  end

  @impl true
  def handle_params(%{"id" => id}, _uri, socket) do
    user =
      User
      |> Ash.Query.for_read(:admin_read, %{},
        actor: socket.assigns.current_user,
        domain: Accounts
      )
      |> Ash.Query.filter(id == ^id)
      |> Ash.read_one(actor: socket.assigns.current_user, authorize?: true, domain: Accounts)

    case user do
      {:ok, %User{} = user} ->
        {:noreply, assign_user(socket, user)}

      _ ->
        {:noreply, push_navigate(socket, to: "/admin?domain=Accounts&resource=User")}
    end
  end

  @impl true
  def handle_event("resend_invitation", _params, socket) do
    case Accounts.resend_invitation(socket.assigns.current_user, socket.assigns.user) do
      :ok ->
        refresh_user(socket, "Setup invitation sent.")

      {:ok, %User{}} ->
        refresh_user(socket, "Setup invitation sent.")

      {:error, retry_after: retry_after} ->
        {:noreply,
         socket
         |> assign(:resend_invitation_retry_after, retry_after)
         |> put_flash(:error, retry_guidance(retry_after))}

      {:error, _reason} ->
        {:noreply, put_flash(socket, :error, "Account could not be updated.")}
    end
  end

  def handle_event("revoke_invitation", _params, socket),
    do: run_target_action(socket, &Accounts.revoke_invitation/2, "Setup invitation revoked.")

  def handle_event("enable", _params, socket),
    do: run_target_action(socket, &Accounts.enable_user/2, "Account enabled.")

  def handle_event("disable", _params, socket),
    do: run_target_action(socket, &Accounts.disable_user/2, "Account disabled.")

  def handle_event("promote", _params, socket),
    do: run_target_action(socket, &Accounts.promote_user/2, "Administrator access granted.")

  def handle_event("demote", _params, socket),
    do: run_target_action(socket, &Accounts.demote_user/2, "Administrator access removed.")

  def handle_event("revoke_sessions", _params, socket),
    do: run_target_action(socket, &Accounts.revoke_user_sessions/2, "Browser sessions revoked.")

  def handle_event("revoke_api_keys", _params, socket),
    do:
      run_target_action(socket, &Accounts.revoke_user_api_keys/2, "API key credentials revoked.")

  def handle_event("manage_email", %{"email" => params}, socket) when is_map(params) do
    email = Map.get(params, "email")

    if is_binary(email) do
      confirmed? = confirmed?(Map.get(params, "confirmed?"))

      run_target_action(
        socket,
        fn actor, user -> Accounts.manage_email(actor, user, email, confirmed?) end,
        "Email updated."
      )
    else
      {:noreply, put_flash(socket, :error, "Enter an email address.")}
    end
  end

  def handle_event("manage_email", _params, socket),
    do: {:noreply, put_flash(socket, :error, "Enter an email address.")}

  def handle_event("delete", %{"delete" => %{"confirmation" => confirmation}}, socket)
      when is_binary(confirmation) do
    case Accounts.delete_user(socket.assigns.current_user, socket.assigns.user, confirmation) do
      :ok -> {:noreply, push_navigate(socket, to: "/admin?domain=Accounts&resource=User")}
      {:error, _reason} -> {:noreply, put_flash(socket, :error, "Account was not deleted.")}
    end
  end

  def handle_event("delete", _params, socket),
    do: {:noreply, put_flash(socket, :error, "Type DELETE to remove this account.")}

  defp run_target_action(socket, action, success_message) do
    case action.(socket.assigns.current_user, socket.assigns.user) do
      :ok -> refresh_user(socket, success_message)
      {:ok, %User{}} -> refresh_user(socket, success_message)
      {:error, _reason} -> {:noreply, put_flash(socket, :error, "Account could not be updated.")}
    end
  end

  defp refresh_user(socket, success_message) do
    case read_user(socket, socket.assigns.user.id) do
      {:ok, %User{} = user} ->
        {:noreply,
         socket
         |> assign_user(user)
         |> assign(:resend_invitation_retry_after, nil)
         |> put_flash(:info, success_message)}

      _ ->
        {:noreply, push_navigate(socket, to: "/admin?domain=Accounts&resource=User")}
    end
  end

  defp read_user(socket, id) do
    User
    |> Ash.Query.for_read(:admin_read, %{},
      actor: socket.assigns.current_user,
      domain: Accounts
    )
    |> Ash.Query.filter(id == ^id)
    |> Ash.read_one(actor: socket.assigns.current_user, authorize?: true, domain: Accounts)
  end

  defp assign_user(socket, user) do
    socket
    |> assign(:user, user)
    |> assign(
      :email_form,
      to_form(
        %{
          "email" => to_string(user.email),
          "confirmed?" => not is_nil(user.confirmed_at)
        },
        as: :email
      )
    )
    |> assign(:delete_form, to_form(%{}, as: :delete))
  end

  defp confirmed?(value) when value in [true, "true", "on", "1"], do: true
  defp confirmed?(_value), do: false

  defp retry_guidance(retry_after),
    do: "Too many requests. Please try again in #{retry_after} seconds."
end
