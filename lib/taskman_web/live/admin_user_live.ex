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
     |> assign(:user, nil)}
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
        {:noreply, assign(socket, :user, user)}

      _ ->
        {:noreply, push_navigate(socket, to: "/admin?domain=Accounts&resource=User")}
    end
  end
end
