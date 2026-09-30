defmodule TaskmanWeb.Tasks.RelatedTasks do
  use TaskmanWeb, :html

  alias TaskmanWeb.ProjectLive.Tasks.RelatedTasksPicker
  alias TaskmanWeb.Tasks.Table

  attr :related_tasks, :map, required: true
  attr :picker, RelatedTasksPicker, required: true
  attr :current_project_id, :integer, required: true
  attr :link_path, :any, required: true

  def related_tasks(assigns) do
    ~H"""
    <section
      id="related-tasks"
      aria-labelledby="related-tasks-title"
      class="mt-7 min-w-0 border-t border-slate-700 pt-6"
    >
      <div class="mb-5 flex items-center gap-2">
        <.icon name="hero-link" class="size-4 text-indigo-300" />
        <h3
          id="related-tasks-title"
          class="text-sm font-semibold uppercase tracking-wider text-slate-200"
        >
          Related Tasks
        </h3>
      </div>
      <.group
        direction={:blocked_by}
        label="Blocked by"
        picker={@picker}
        rows={@related_tasks.blocked_by}
        current_project_id={@current_project_id}
        link_path={@link_path}
      />
      <.group
        direction={:blocks}
        label="Blocks"
        picker={@picker}
        rows={@related_tasks.blocks}
        current_project_id={@current_project_id}
        link_path={@link_path}
      />
      <div
        :if={@picker.direction}
        id="related-picker"
        popover="manual"
        role="dialog"
        aria-labelledby="related-picker-title"
        data-anchor={"related-add-#{direction_key(@picker.direction)}"}
        class="fixed inset-auto m-0 min-w-0 overflow-y-auto rounded-xl border border-indigo-400/30 bg-slate-900 p-4 text-slate-100 shadow-2xl shadow-black/40"
      >
        <span
          id={"related-picker-positioner-#{@picker.direction}"}
          phx-hook="RelatedTaskPicker"
          phx-update="ignore"
          hidden
        />
        <div class="mb-3 flex items-center justify-between gap-3">
          <h4 id="related-picker-title" class="text-sm font-semibold text-slate-100">
            Add to {if(@picker.direction == :blocks, do: "Blocks", else: "Blocked by")}
          </h4>
          <button
            id="related-picker-close"
            type="button"
            phx-click={
              JS.push("close_related_picker")
              |> JS.focus(to: "#related-add-#{direction_key(@picker.direction)}")
            }
            aria-label="Close related Task picker"
            class="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-800 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            <.icon name="hero-x-mark" class="size-4" />
          </button>
        </div>
        <.form
          for={@picker.project_form}
          id="related-picker-project-form"
          phx-change="select_related_project"
        >
          <.input
            field={@picker.project_form[:project_id]}
            id="related-picker-project"
            type="select"
            label="Project"
            options={Enum.map(@picker.projects, &{&1.name, &1.id})}
          />
        </.form>
        <.form
          for={@picker.search_form}
          id="related-picker-search-form"
          phx-change="search_related_tasks"
          phx-submit="search_related_tasks"
          class="mt-3"
        >
          <.input
            field={@picker.search_form[:query]}
            id="related-picker-search"
            type="search"
            label="Find a Task"
            autocomplete="off"
            placeholder="ID or title"
          />
        </.form>
        <p
          :if={@picker.candidates == []}
          id="related-picker-empty"
          class="px-2 py-3 text-sm text-slate-400"
        >
          No Tasks match this search.
        </p>
        <ul
          :if={@picker.candidates != []}
          id="related-picker-results"
          aria-label="Related Task candidates"
          class="mt-3 max-h-56 space-y-1 overflow-y-auto"
        >
          <li :for={candidate <- @picker.candidates}>
            <button
              id={"related-candidate-#{candidate.id}"}
              type="button"
              phx-click="add_related_task"
              phx-value-task-id={candidate.id}
              class="block w-full min-w-0 rounded-lg px-3 py-2 text-left transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            >
              <span class="block truncate text-sm font-medium text-slate-100">{candidate.title}</span>
              <span class="block truncate text-xs text-slate-400">Task #{candidate.id} · {candidate.project_name}{location_suffix(
                candidate.location
              )}</span>
            </button>
          </li>
        </ul>
        <.picker_error error={@picker.error} />
      </div>
      <.picker_error :if={is_nil(@picker.direction)} error={@picker.error} />
    </section>
    """
  end

  attr :direction, :atom, required: true
  attr :label, :string, required: true
  attr :picker, RelatedTasksPicker, required: true
  attr :rows, :list, required: true
  attr :current_project_id, :integer, required: true
  attr :link_path, :any, required: true

  defp group(assigns) do
    assigns = assign(assigns, :key, direction_key(assigns.direction))

    ~H"""
    <div id={"related-#{@key}"} class="mt-4 min-w-0">
      <div class="mb-2 flex items-center justify-between gap-2">
        <h4 class="text-sm font-semibold text-slate-200">
          {@label}
          <span
            id={"related-#{@key}-count"}
            class="ml-1 rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-300"
          >{length(@rows)}</span>
        </h4>
        <button
          id={"related-add-#{@key}"}
          type="button"
          phx-click="open_related_picker"
          phx-value-direction={@key}
          data-related-picker-trigger
          aria-label={"Add to #{@label}"}
          title={"Add to #{@label}"}
          aria-haspopup="dialog"
          aria-controls="related-picker"
          aria-expanded={to_string(@picker.direction == @direction)}
          class="grid size-8 shrink-0 place-items-center rounded-lg text-indigo-300 transition hover:bg-indigo-400/10 hover:text-indigo-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
        >
          <.icon name="hero-plus" class="size-4" />
        </button>
      </div>
      <p
        :if={@rows == []}
        id={"related-#{@key}-empty"}
        class="rounded-lg border border-dashed border-slate-700 px-3 py-3 text-sm text-slate-400"
      >
        No Tasks in this group.
      </p>
      <ul :if={@rows != []} class="space-y-2">
        <li
          :for={row <- @rows}
          id={"related-#{@key}-row-#{row.id}"}
          class="flex min-w-0 items-start gap-2 rounded-lg border border-slate-700/80 bg-slate-950/50 p-2.5"
        >
          <.link
            id={"related-#{@key}-link-#{row.id}"}
            patch={@link_path.(row)}
            class="min-w-0 flex-1 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            <span class="block truncate text-sm font-medium text-slate-100">{row.title}</span>
            <span class="mt-0.5 block truncate text-xs text-slate-400">Task #{row.id} · {Table.status_label(
              row.status
            )} · {Table.priority_label(row.priority)}<span :if={row.project_id != @current_project_id}> · {row.project_name}</span>{location_suffix(
              row.location
            )}</span>
          </.link>
          <button
            id={"related-remove-#{@key}-#{row.id}"}
            type="button"
            phx-click="remove_related_task"
            phx-value-direction={@key}
            phx-value-task-id={row.id}
            aria-label={"Remove #{row.title} from #{@label}"}
            title={"Remove #{row.title} from #{@label}"}
            class="grid size-8 shrink-0 self-center place-items-center rounded-lg text-slate-400 transition hover:bg-rose-400/10 hover:text-rose-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-400"
          >
            <.icon name="hero-x-mark" class="size-4" />
          </button>
        </li>
      </ul>
    </div>
    """
  end

  attr :error, :string, default: nil

  defp picker_error(assigns) do
    ~H"""
    <p
      :if={@error}
      id="related-picker-error"
      role="alert"
      aria-live="assertive"
      class="mt-3 rounded-lg border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-sm text-rose-200"
    >
      {@error}
    </p>
    """
  end

  defp direction_key(direction), do: direction |> Atom.to_string() |> String.replace("_", "-")

  defp location_suffix(%{path: []}), do: " · Project root"
  defp location_suffix(%{path: path}), do: " · " <> Enum.join(path, " / ")
end
