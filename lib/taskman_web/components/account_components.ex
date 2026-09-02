defmodule TaskmanWeb.AccountComponents do
  @moduledoc "Reusable account navigation components."

  use Phoenix.Component

  attr :current_user, :any, default: nil

  @doc "Renders the authenticated account menu when a User is present."
  def account_menu(assigns) do
    ~H"""
    <nav
      :if={@current_user}
      id="account-menu"
      aria-label="Account navigation"
      class="flex items-center gap-3"
    >
      <span id="account-identity" class="max-w-56 truncate text-sm text-slate-300">
        {to_string(@current_user.email)}
      </span>
      <.link
        id="account-settings-link"
        navigate="/account/settings"
        class="rounded-lg px-2.5 py-1.5 text-sm font-medium text-slate-300 transition hover:bg-white/10 hover:text-white"
      >
        Account settings
      </.link>
      <.link
        id="account-sign-out-link"
        navigate="/sign-out"
        class="rounded-lg px-2.5 py-1.5 text-sm font-medium text-slate-300 transition hover:bg-white/10 hover:text-white"
      >
        Sign out
      </.link>
    </nav>
    """
  end
end
