defmodule TaskmanWeb.ProjectLive.ShareTaskView do
  use TaskmanWeb, :html

  alias Taskman.Tasks.Task

  attr :id, :string, required: true
  attr :include_children?, :boolean, required: true
  attr :visible_statuses, :list, required: true
  attr :hydrated?, :boolean, required: true
  attr :share_path, :string, default: nil
  attr :label, :string, default: "Share current view"

  def control(assigns) do
    ~H"""
    <div class="relative">
      <button
        id={@id}
        type="button"
        phx-hook="TaskmanWeb.ProjectLive.ShareTaskView"
        disabled={not @hydrated?}
        data-share-path={@share_path}
        data-include-children={to_string(@include_children?)}
        data-statuses={
          Task.statuses()
          |> Enum.filter(&(&1 in @visible_statuses))
          |> Enum.map_join(",", &Atom.to_string/1)
        }
        aria-label={@label}
        data-tooltip=""
        class="inline-flex size-[34px] items-center justify-center rounded-lg border border-slate-700 bg-slate-900/60 text-slate-300 transition hover:border-slate-600 hover:bg-slate-800 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <.icon name="hero-share" class="size-4" />
      </button>
      <p id={"#{@id}-status"} role="status" aria-live="polite" class="sr-only"></p>
      <p
        id={"#{@id}-toast"}
        hidden
        class="fixed right-4 top-4 z-[100] rounded-xl border border-emerald-500/40 bg-slate-950 px-4 py-2.5 text-sm font-medium text-emerald-200 shadow-xl"
      >
        Link copied
      </p>
      <div
        id={"#{@id}-feedback"}
        hidden
        class="fixed inset-x-4 top-20 z-30 rounded-xl border border-slate-700 bg-slate-950 p-3 text-sm text-slate-200 shadow-xl sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-72"
      >
        <p id={"#{@id}-message"}></p>
        <label id={"#{@id}-url-label"} for={"#{@id}-url"} hidden class="mt-2 block">
          Link to copy
        </label>
        <input
          id={"#{@id}-url"}
          type="text"
          readonly
          hidden
          class="mt-1 w-full rounded-lg border border-slate-600 bg-slate-900 px-2 py-1 text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
        />
      </div>
    </div>
    """
  end
end
