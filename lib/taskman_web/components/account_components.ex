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
        :if={@current_user.admin?}
        id="account-administration-link"
        navigate="/admin"
        class="rounded-lg px-2.5 py-1.5 text-sm font-medium text-slate-300 transition hover:bg-white/10 hover:text-white"
      >
        Administration
      </.link>
      <.link
        id="account-settings-link"
        navigate="/account/settings"
        class="rounded-lg px-2.5 py-1.5 text-sm font-medium text-slate-300 transition hover:bg-white/10 hover:text-white"
      >
        Account settings
      </.link>
      <.link
        id="account-sign-out-link"
        href="/sign-out"
        method="delete"
        class="rounded-lg px-2.5 py-1.5 text-sm font-medium text-slate-300 transition hover:bg-white/10 hover:text-white"
      >
        Sign out
      </.link>
    </nav>
    """
  end

  attr :id, :string, required: true
  attr :title, :string, required: true
  attr :description, :string, default: nil
  attr :danger?, :boolean, default: false
  slot :inner_block, required: true

  @doc "Renders a focused section within account settings."
  def settings_section(assigns) do
    ~H"""
    <section
      id={@id}
      class={[
        "rounded-2xl border p-6 shadow-sm",
        @danger? && "border-rose-500/40 bg-rose-950/20",
        !@danger? && "border-slate-700 bg-slate-900"
      ]}
    >
      <header class="mb-5">
        <h2 class="text-lg font-semibold text-slate-100">{@title}</h2>
        <p :if={@description} class="mt-1 text-sm text-slate-400">{@description}</p>
      </header>
      {render_slot(@inner_block)}
    </section>
    """
  end
end
