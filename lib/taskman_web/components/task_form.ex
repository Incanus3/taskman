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
        class="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-3 text-sm text-slate-950 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-indigo-500 focus:ring-4 focus:ring-indigo-500/10"
        error_class="border-rose-400 focus:border-rose-500 focus:ring-rose-500/10"
      />
      <div class="mt-6 flex justify-end gap-3">
        <.link
          id="cancel-task"
          patch={@cancel}
          class="rounded-xl px-4 py-2.5 text-sm font-semibold text-slate-600 transition hover:bg-slate-100 hover:text-slate-950"
        >
          Cancel
        </.link>
        <button
          type="submit"
          phx-disable-with="Creating…"
          class="rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-60"
        >
          Create task
        </button>
      </div>
    </Phoenix.Component.form>
    """
  end
end
