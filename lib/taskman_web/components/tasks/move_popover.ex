defmodule TaskmanWeb.Tasks.MovePopover do
  use TaskmanWeb, :html

  alias TaskmanWeb.ProjectLive.Tasks.Move

  attr :task_id, :integer, required: true
  attr :task_move, Move, required: true
  attr :window_escape?, :boolean, default: true
  attr :restore_focus?, :boolean, default: true

  def popover(assigns) do
    ~H"""
    <section
      id={"move-task-#{@task_id}"}
      role="dialog"
      aria-labelledby={"move-task-title-#{@task_id}"}
      phx-click-away="cancel_move_task"
      phx-window-keydown={@window_escape? && "cancel_move_task"}
      phx-key={@window_escape? && "escape"}
      phx-mounted={JS.focus(to: "#move-task-search-#{@task_id}")}
      phx-remove={@restore_focus? && JS.pop_focus()}
      tabindex="-1"
      class="pointer-events-auto relative z-20 mt-3 rounded-xl border border-slate-700 bg-slate-950 p-4 shadow-xl shadow-black/30"
    >
      <div class="flex items-center justify-between gap-3">
        <h3 id={"move-task-title-#{@task_id}"} class="text-sm font-semibold text-slate-100">
          Move Task
        </h3>
        <button
          id={"cancel-move-task-#{@task_id}"}
          type="button"
          phx-click="cancel_move_task"
          class="rounded-lg px-2 py-1 text-xs font-semibold text-slate-400 transition hover:bg-slate-800 hover:text-white"
        >
          Cancel
        </button>
      </div>

      <label
        for={"move-task-search-#{@task_id}"}
        class="mt-4 block text-xs font-semibold text-slate-300"
      >
        Destination
      </label>
      <div class="relative mt-1">
        <input
          id={"move-task-search-#{@task_id}"}
          type="search"
          name="query"
          value={@task_move.query}
          role="combobox"
          aria-controls={"move-task-options-#{@task_id}"}
          aria-expanded={to_string(@task_move.options_open?)}
          autocomplete="off"
          phx-click="open_move_destinations"
          phx-keyup="search_move_destinations"
          class="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 pr-9 text-sm text-slate-100 outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15"
          placeholder="Search locations"
        />
        <.icon
          name="hero-chevron-down"
          class={[
            "pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-slate-400 transition",
            @task_move.options_open? && "rotate-180"
          ]}
        />
      </div>

      <div
        :if={@task_move.options_open?}
        id={"move-task-options-#{@task_id}"}
        role="listbox"
        aria-label="Move Task destinations"
        class="mt-2 max-h-52 space-y-1 overflow-y-auto"
      >
        <p
          :if={@task_move.options == []}
          id={"move-task-no-results-#{@task_id}"}
          class="rounded-lg px-3 py-2 text-sm text-slate-400"
        >
          No locations match your search.
        </p>
        <button
          :for={option <- @task_move.options}
          id={option_id(option)}
          type="button"
          role="option"
          aria-label={option.label}
          aria-selected={to_string(@task_move.destination == option.value)}
          data-current-location={to_string(option.current?)}
          phx-click="select_move_destination"
          phx-value-destination={option.value}
          class={[
            "flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm transition focus:outline-none focus:ring-2 focus:ring-indigo-400/50",
            @task_move.destination == option.value && "bg-indigo-400/15 text-indigo-100",
            @task_move.destination != option.value && "text-slate-200 hover:bg-slate-800"
          ]}
        >
          <span class="truncate">{option.label}</span>
          <span
            :if={option.current?}
            class="shrink-0 text-xs font-medium text-slate-400"
          >
            Current location
          </span>
        </button>
      </div>

      <p
        :if={@task_move.error}
        id={"move-task-error-#{@task_id}"}
        role="alert"
        class="mt-3 text-sm text-rose-300"
      >
        {@task_move.error}
      </p>

      <div class="mt-4 flex justify-end gap-2">
        <button
          type="button"
          phx-click="cancel_move_task"
          class="rounded-lg px-3 py-2 text-sm font-semibold text-slate-300 transition hover:bg-slate-800 hover:text-white"
        >
          Cancel
        </button>
        <button
          id={"move-task-submit-#{@task_id}"}
          type="button"
          phx-click="submit_move_task"
          disabled={@task_move.destination in [nil, Move.current_destination(@task_move)]}
          class="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Move Task
        </button>
      </div>
    </section>
    """
  end

  defp option_id(%{value: "project", id: project_id}),
    do: "move-task-option-project-#{project_id}"

  defp option_id(%{value: "list:" <> _list_id, id: list_id}),
    do: "move-task-option-list-#{list_id}"
end
