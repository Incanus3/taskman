defmodule TaskmanWeb.TaskForm do
  use TaskmanWeb, :html

  import Phoenix.Component, except: [form: 1]

  attr :form, Phoenix.HTML.Form, required: true
  attr :cancel, :string, required: true

  def form(assigns) do
    ~H"""
    <Phoenix.Component.form
      for={@form}
      id="task-form"
      phx-change="validate_task"
      phx-submit="save_task"
    >
      <.input
        field={@form[:title]}
        id="task-title"
        type="text"
        label="Task title"
        autocomplete="off"
        class="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-sm text-slate-100 shadow-sm shadow-black/20 outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15"
        error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
      />
      <div class="mt-6 flex justify-end gap-3">
        <.link
          id="cancel-task"
          patch={@cancel}
          class="rounded-xl px-4 py-2.5 text-sm font-semibold text-slate-300 transition hover:bg-slate-800 hover:text-white"
        >
          Cancel
        </.link>
        <button
          type="submit"
          phx-disable-with="Creating…"
          class="rounded-xl bg-indigo-500 px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-indigo-950/30 transition hover:bg-indigo-400 disabled:cursor-wait disabled:opacity-60"
        >
          Create task
        </button>
      </div>
    </Phoenix.Component.form>
    """
  end
end
