defmodule TaskmanWeb.AshAdminActorPlug do
  @moduledoc false

  @behaviour AshAdmin.ActorPlug

  @impl true
  def actor_assigns(socket, _session) do
    current_user = socket.assigns[:current_scope] || socket.assigns[:current_user]
    [actor: current_user]
  end

  @impl true
  def set_actor_session(conn), do: conn
end
