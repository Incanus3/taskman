defmodule TaskmanWeb.Layouts do
  @moduledoc """
  This module holds layouts and related functionality
  used by your application.
  """
  use TaskmanWeb, :html

  # Embed all files in layouts/* within this module.
  # The default root.html.heex file contains the HTML
  # skeleton of your application, namely HTML headers
  # and other static content.
  embed_templates "layouts/*"

  @doc """
  Renders your app layout.

  This function is typically invoked from every template,
  and it often contains your application menu, sidebar,
  or similar.

  ## Examples

      <Layouts.app flash={@flash}>
        <h1>Content</h1>
      </Layouts.app>

  """
  attr :flash, :map, required: true, doc: "the map of flash messages"

  attr :current_scope, :any,
    default: nil,
    doc: "the current [scope](https://phoenix.hexdocs.pm/scopes.html)"

  attr :current_user, :any,
    default: nil,
    doc: "the authenticated User actor"

  attr :content_scroll?, :boolean, default: true
  attr :search_workspace, :any, default: nil
  attr :search_enabled?, :boolean, default: true

  slot :inner_block, required: true

  def app(assigns) do
    assigns = assign(assigns, :current_user, assigns.current_user || assigns.current_scope)

    ~H"""
    <div id="application-shell" class="flex h-dvh flex-col overflow-hidden">
      <header
        :if={@current_user}
        id="authenticated-navigation"
        class="relative grid shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 border-b border-slate-800 bg-slate-950 px-3 py-3 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,32rem)_minmax(0,1fr)]"
      >
        <nav
          id="application-navigation"
          aria-label="Application navigation"
          class="flex min-w-0 flex-wrap items-center justify-self-start gap-1 sm:gap-3"
        >
          <.link
            id="application-home-link"
            navigate="/"
            class="shrink-0 rounded-lg px-2.5 py-1.5 text-base font-semibold text-slate-200 transition hover:bg-white/10 hover:text-white"
          >
            Taskman
          </.link>
          <.link
            :if={@current_user.admin?}
            id="account-administration-link"
            navigate="/admin"
            aria-label="Administration"
            title="Administration"
            class="grid size-9 shrink-0 place-items-center rounded-lg text-sm font-medium text-slate-300 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 md:size-auto md:px-2.5 md:py-1.5"
          >
            <.icon name="hero-wrench-screwdriver" class="size-5 md:hidden" />
            <span class="hidden md:inline">Administration</span>
          </.link>
        </nav>
        <.live_component
          module={TaskmanWeb.GlobalTaskSearch}
          id="global-task-search"
          workspace={@search_workspace}
          enabled?={@search_enabled?}
        />
        <div class="col-start-3 min-w-0 max-w-full justify-self-end">
          <.account_menu current_user={@current_user} />
        </div>
      </header>

      <div
        class={[
          "min-h-0 flex-1",
          if(@content_scroll?, do: "overflow-y-auto", else: "overflow-hidden")
        ]}
        id="application-content"
      >
        {render_slot(@inner_block)}
      </div>
    </div>

    <.flash_group flash={@flash} />
    """
  end

  @doc """
  Shows the flash group with standard titles and content.

  ## Examples

      <.flash_group flash={@flash} />
  """
  attr :flash, :map, required: true, doc: "the map of flash messages"
  attr :id, :string, default: "flash-group", doc: "the optional id of flash container"

  def flash_group(assigns) do
    ~H"""
    <div id={@id} aria-live="polite">
      <.flash kind={:info} flash={@flash} />
      <.flash kind={:error} flash={@flash} />

      <.flash
        id="client-error"
        kind={:error}
        title={gettext("We can't find the internet")}
        phx-disconnected={
          show(".phx-client-error #client-error")
          |> JS.remove_attribute("hidden", to: ".phx-client-error #client-error")
        }
        phx-connected={hide("#client-error") |> JS.set_attribute({"hidden", ""})}
        hidden
      >
        {gettext("Attempting to reconnect")}
        <.icon name="hero-arrow-path" class="ml-1 size-3 motion-safe:animate-spin" />
      </.flash>

      <.flash
        id="server-error"
        kind={:error}
        title={gettext("Something went wrong!")}
        phx-disconnected={
          show(".phx-server-error #server-error")
          |> JS.remove_attribute("hidden", to: ".phx-server-error #server-error")
        }
        phx-connected={hide("#server-error") |> JS.set_attribute({"hidden", ""})}
        hidden
      >
        {gettext("Attempting to reconnect")}
        <.icon name="hero-arrow-path" class="ml-1 size-3 motion-safe:animate-spin" />
      </.flash>
    </div>
    """
  end
end
