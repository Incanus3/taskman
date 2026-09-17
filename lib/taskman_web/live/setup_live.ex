defmodule TaskmanWeb.SetupLive do
  use TaskmanWeb, :live_view

  @impl true
  def mount(_params, _session, socket) do
    {:ok,
     socket
     |> assign_new(:current_scope, fn -> nil end)
     |> assign_new(:current_user, fn -> nil end)
     |> assign(:setup_form, setup_form(""))}
  end

  @impl true
  def handle_params(%{"token" => token}, _uri, socket) do
    {:noreply, assign(socket, :setup_form, setup_form(token))}
  end

  defp setup_form(token) do
    to_form(%{"token" => token, "password" => "", "password_confirmation" => ""}, as: :setup)
  end
end
