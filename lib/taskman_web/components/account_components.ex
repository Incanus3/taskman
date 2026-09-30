defmodule TaskmanWeb.AccountComponents do
  @moduledoc "Reusable account navigation components."

  use Phoenix.Component
  import TaskmanWeb.CoreComponents, only: [icon: 1]

  attr :current_user, :any, default: nil

  @doc "Renders the authenticated account menu when a User is present."
  def account_menu(assigns) do
    ~H"""
    <nav
      :if={@current_user}
      id="account-menu"
      aria-label="Account navigation"
      class="flex items-center gap-1 sm:gap-3"
    >
      <span
        id="account-identity"
        class="hidden max-w-56 truncate pr-2.5 text-sm text-slate-300 xl:block"
      >
        {to_string(@current_user.email)}
      </span>
      <.link
        id="account-settings-link"
        navigate="/account/settings"
        aria-label="Account settings"
        title="Account settings"
        class="grid size-9 shrink-0 place-items-center rounded-lg text-sm font-medium text-slate-300 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
      >
        <.icon name="hero-cog-6-tooth" class="size-5" />
      </.link>
      <.link
        id="account-sign-out-link"
        href="/sign-out"
        method="delete"
        aria-label="Sign out"
        title="Sign out"
        class="grid size-9 shrink-0 place-items-center rounded-lg text-sm font-medium text-slate-300 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
      >
        <.icon name="hero-arrow-right-start-on-rectangle" class="size-5" />
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
