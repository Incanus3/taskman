defmodule TaskmanWeb.ProjectSelector do
  use TaskmanWeb, :html

  alias Taskman.Projects.Project
  alias TaskmanWeb.ProjectLive.Paths
  alias TaskmanWeb.ProjectLive.ProjectEdit

  @presets [
    {"Indigo", "6366F1"},
    {"Blue", "3B82F6"},
    {"Cyan", "06B6D4"},
    {"Emerald", "10B981"},
    {"Amber", "F59E0B"},
    {"Orange", "F97316"},
    {"Rose", "F43F5E"},
    {"Fuchsia", "D946EF"}
  ]

  attr :selected_project, Project, default: nil
  attr :projects, :any, required: true
  attr :projects_empty?, :boolean, required: true
  attr :open?, :boolean, required: true
  attr :inert?, :boolean, default: false

  def selector(assigns) do
    ~H"""
    <div
      id="project-selector"
      class="relative mb-5 shrink-0"
      inert={@inert?}
      phx-click-away={@open? && "close_project_selector"}
    >
      <div class="flex min-w-0 items-center gap-3">
        <div
          class="grid size-10 shrink-0 place-items-center rounded-xl shadow-lg shadow-indigo-950/30"
          style={tile_style(@selected_project)}
        >
          <.icon name={icon_name(@selected_project)} class="size-6" />
        </div>
        <button
          id="project-selector-identity"
          type="button"
          phx-click="toggle_project_selector"
          aria-label={if(@open?, do: "Close Project choices", else: "Open Project choices")}
          class="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
        >
          <span
            id="project-selector-name"
            class="block truncate text-base font-semibold tracking-tight"
          >
            {if(@selected_project, do: @selected_project.name, else: "Choose a Project")}
          </span>
          <span
            :if={@selected_project && @selected_project.description != ""}
            id="project-selector-description"
            class="block truncate text-xs text-slate-400"
          >
            {@selected_project.description}
          </span>
        </button>
        <div class="flex shrink-0 items-center gap-1">
          <button
            :if={@selected_project}
            id="project-edit-button"
            type="button"
            phx-click="open_project_edit"
            aria-label="Edit Project"
            data-tooltip=""
            class="grid size-8 place-items-center rounded-lg text-slate-400 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            <.icon name="hero-pencil-square" class="size-4" />
          </button>
          <button
            id="project-selector-toggle"
            type="button"
            phx-click="toggle_project_selector"
            aria-expanded={to_string(@open?)}
            aria-controls="project-selector-menu"
            aria-label="Choose Project"
            data-tooltip=""
            class="grid size-8 place-items-center rounded-lg text-slate-400 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            <.icon name={if(@open?, do: "hero-chevron-up", else: "hero-chevron-down")} class="size-4" />
          </button>
        </div>
      </div>
      <nav
        :if={@open?}
        id="project-selector-menu"
        aria-label="Projects"
        phx-window-keydown="close_project_selector"
        phx-key="escape"
        class="absolute left-0 right-0 top-full z-40 mt-3 max-h-[min(24rem,60dvh)] overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 p-2 shadow-2xl shadow-black/40"
      >
        <p :if={@projects_empty?} id="project-selector-empty" class="px-3 py-4 text-sm text-slate-400">
          No Projects yet
        </p>
        <div id="project-selector-choices" phx-update="stream" class="grid grid-cols-1 gap-1">
          <div :for={{dom_id, project} <- @projects} id={dom_id}>
            <.link
              id={"select-project-#{project.id}"}
              patch={Paths.browse_path(project, nil, false)}
              aria-current={@selected_project && @selected_project.id == project.id && "page"}
              class={[
                "flex min-w-0 items-center gap-3 rounded-lg px-2 py-2 transition hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400",
                @selected_project && @selected_project.id == project.id && "bg-white/10"
              ]}
            >
              <span
                class="grid size-9 shrink-0 place-items-center rounded-lg"
                style={tile_style(project)}
              >
                <.icon name={icon_name(project)} class="size-5" />
              </span>
              <span class="min-w-0 flex-1">
                <span class="block truncate text-sm font-medium">{project.name}</span>
                <span :if={project.description != ""} class="block truncate text-xs text-slate-400">
                  {project.description}
                </span>
              </span>
            </.link>
          </div>
        </div>
      </nav>
    </div>
    """
  end

  attr :edit, ProjectEdit, required: true
  attr :inert?, :boolean, default: false

  def editor(assigns) do
    assigns = assign(assigns, :presets, @presets)

    ~H"""
    <.modal
      :if={@edit.mode && !@inert?}
      id="project-modal"
      show
      on_cancel={JS.push("cancel_project_edit")}
      initial_focus="#project-name"
    >
      <h2 id="project-modal-title" class="pr-10 text-xl font-semibold text-slate-100">
        {ProjectEdit.title(@edit)}
      </h2>
      <%= if @edit.available? do %>
        <.form
          for={@edit.form}
          id="project-form"
          data-theme="dark"
          phx-change="validate_project"
          phx-submit="save_project"
          class="mt-5 space-y-3 bg-transparent"
        >
          <.input
            field={@edit.form[:name]}
            id="project-name"
            type="text"
            label="Name"
            autocomplete="off"
          />
          <.input
            field={@edit.form[:description]}
            id="project-description"
            type="textarea"
            label="Description"
            maxlength="160"
          />
          <fieldset id="project-icons" class="space-y-2">
            <legend class="text-sm font-medium text-slate-200">Icon</legend>
            <div class="grid grid-cols-4 gap-2 sm:grid-cols-8">
              <label :for={icon <- Project.icons()} data-tooltip={icon} class="cursor-pointer">
                <input
                  id={"project-icon-#{icon}"}
                  type="radio"
                  name={@edit.form[:icon].name}
                  value={icon}
                  checked={@edit.form[:icon].value == icon}
                  class="peer sr-only"
                />
                <span class="grid size-10 place-items-center rounded-lg border border-slate-700 text-slate-300 transition hover:border-indigo-400 peer-checked:border-indigo-400 peer-checked:bg-indigo-400/20 peer-focus-visible:ring-2 peer-focus-visible:ring-indigo-400">
                  <.icon name={icon_name(icon)} class="size-5" />
                </span>
                <span class="sr-only">{icon}</span>
              </label>
            </div>
            <p
              :for={error <- @edit.form[:icon].errors}
              data-role="field-error"
              class="text-xs text-rose-300"
            >
              {TaskmanWeb.CoreComponents.translate_error(error)}
            </p>
          </fieldset>
          <fieldset id="project-colors" class="space-y-2">
            <legend class="text-sm font-medium text-slate-200">Color</legend>
            <div class="flex flex-wrap gap-2">
              <button
                :for={{name, color} <- @presets}
                id={"project-color-preset-#{String.downcase(name)}"}
                type="button"
                phx-click="select_project_color"
                phx-value-color={color}
                aria-label={"#{name} color"}
                data-tooltip=""
                aria-pressed={to_string(String.upcase(to_string(@edit.form[:color].value)) == color)}
                class="grid size-9 place-items-center rounded-lg border-2 border-white/20 transition hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                style={"background-color: ##{color}"}
              >
                <.icon
                  :if={String.upcase(to_string(@edit.form[:color].value)) == color}
                  name="hero-check"
                  class="size-4 text-white"
                />
              </button>
            </div>
            <.input
              field={@edit.form[:color]}
              id="project-color"
              type="text"
              label="Hex color"
              prefix="#"
              maxlength="6"
              autocomplete="off"
            />
          </fieldset>
          <div class="flex justify-end gap-2 pt-3">
            <button
              id="project-cancel-button"
              type="button"
              phx-click="cancel_project_edit"
              class="rounded-lg px-4 py-2 text-sm text-slate-300 hover:bg-white/10"
            >Cancel</button>
            <button
              id="project-save-button"
              type="submit"
              class="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-400"
            >{ProjectEdit.submit_label(@edit)}</button>
          </div>
        </.form>
      <% else %>
        <p id="project-unavailable" class="mt-5 text-sm text-slate-300">
          This Project is no longer available.
        </p>
      <% end %>
    </.modal>
    """
  end

  defp icon_name(%Project{icon: icon}), do: icon_name(icon)

  defp icon_name(icon) when is_binary(icon) do
    if icon in Project.icons(), do: "hero-" <> icon, else: "hero-briefcase"
  end

  defp icon_name(_), do: "hero-briefcase"

  defp tile_style(%Project{color: color}) when is_binary(color) do
    if String.match?(color, ~r/\A#[0-9A-Fa-f]{6}\z/) do
      <<"#", r::binary-size(2), g::binary-size(2), b::binary-size(2)>> = color
      {red, ""} = Integer.parse(r, 16)
      {green, ""} = Integer.parse(g, 16)
      {blue, ""} = Integer.parse(b, 16)

      luminance =
        0.2126 * linear_channel(red) +
          0.7152 * linear_channel(green) +
          0.0722 * linear_channel(blue)

      foreground = if luminance > 0.179, do: "#000000", else: "#FFFFFF"
      "background-color: #{color}; color: #{foreground}"
    else
      tile_style(nil)
    end
  end

  defp tile_style(_), do: "background-color: #6366F1; color: #FFFFFF"

  defp linear_channel(value) do
    channel = value / 255
    if channel <= 0.04045, do: channel / 12.92, else: :math.pow((channel + 0.055) / 1.055, 2.4)
  end
end
