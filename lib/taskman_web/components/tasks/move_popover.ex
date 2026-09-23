defmodule TaskmanWeb.Tasks.MovePopover do
  use TaskmanWeb, :html

  alias TaskmanWeb.ProjectLive.Tasks.Move

  attr :task_id, :integer, required: true
  attr :task_move, Move, required: true
  attr :window_escape?, :boolean, default: true
  attr :restore_focus?, :boolean, default: true
  attr :recovery?, :boolean, default: false

  def popover(assigns) do
    ~H"""
    <section
      id={"move-task-#{@task_id}"}
      role="dialog"
      aria-labelledby={"move-task-title-#{@task_id}"}
      phx-click-away={!@recovery? && "cancel_move_task"}
      phx-window-keydown={!@recovery? && @window_escape? && "cancel_move_task"}
      phx-key={!@recovery? && @window_escape? && "escape"}
      phx-mounted={!@recovery? && JS.focus(to: "#move-task-search-#{@task_id}")}
      phx-remove={!@recovery? && @restore_focus? && JS.pop_focus()}
      tabindex="-1"
      class="pointer-events-auto relative z-20 mt-3 rounded-xl border border-slate-700 bg-slate-950 shadow-xl shadow-black/30"
    >
      <header class="border-b border-slate-700 px-4 py-3.5">
        <h3 id={"move-task-title-#{@task_id}"} class="text-sm font-semibold text-slate-100">
          Move Task
        </h3>
      </header>

      <div class="px-4 py-4">
        <label
          for={"move-task-search-#{@task_id}"}
          class="block text-xs font-semibold text-slate-300"
        >
          Destination
        </label>
        <div
          id={"move-task-destination-picker-#{@task_id}"}
          phx-click-away={!@recovery? && @task_move.options_open? && "close_move_destinations"}
          class="relative mt-1"
        >
          <input
            id={"move-task-search-#{@task_id}"}
            type="search"
            name="query"
            value={@task_move.query}
            role="combobox"
            aria-controls={"move-task-options-#{@task_id}"}
            aria-expanded={to_string(@task_move.options_open?)}
            autocomplete="off"
            phx-click={!@recovery? && "open_move_destinations"}
            phx-keyup={!@recovery? && "search_move_destinations"}
            disabled={@recovery?}
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

          <div
            :if={@task_move.options_open?}
            id={"move-task-options-#{@task_id}"}
            role="listbox"
            aria-label="Move Task destinations"
            class="absolute inset-x-0 top-full z-50 mt-2 max-h-52 space-y-1 overflow-y-auto rounded-lg border border-slate-600 bg-slate-900 p-1 shadow-xl shadow-black/40"
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
              aria-selected={to_string(@task_move.destination == option.key)}
              data-current-location={to_string(option.current?)}
              phx-click={!@recovery? && "select_move_destination"}
              disabled={@recovery?}
              phx-value-destination={option.key}
              class={[
                "flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm transition focus:outline-none focus:ring-2 focus:ring-indigo-400/50",
                @task_move.destination == option.key && "bg-indigo-400/15 text-indigo-100",
                @task_move.destination != option.key && "text-slate-200 hover:bg-slate-800"
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
        </div>

        <.inline_error
          :if={@task_move.error}
          id={"move-task-error-#{@task_id}"}
          role="alert"
          class="mt-3 text-rose-300"
        >
          {@task_move.error}
        </.inline_error>
        <p
          :if={is_nil(@task_move.error) && Move.at_selected_destination?(@task_move)}
          id={"move-task-current-location-#{@task_id}"}
          role="status"
          class="mt-3 text-xs text-orange-300"
        >
          This Task is already at the selected destination.
        </p>
      </div>

      <footer :if={!@recovery?} class="flex justify-end gap-2 border-t border-slate-700 px-4 py-3.5">
        <button
          id={"cancel-move-task-#{@task_id}"}
          type="button"
          phx-click="cancel_move_task"
          class="rounded-lg border border-slate-600 bg-slate-800/70 px-3 py-2 text-sm font-semibold text-slate-100 transition hover:border-slate-500 hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-400/50"
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
      </footer>
    </section>
    """
  end

  defp option_id(%{key: "project", id: project_id}),
    do: "move-task-option-project-#{project_id}"

  defp option_id(%{key: "list:" <> _list_id, id: list_id}),
    do: "move-task-option-list-#{list_id}"
end
