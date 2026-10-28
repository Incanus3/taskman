defmodule TaskmanWeb.DateTimePicker do
  use TaskmanWeb, :html

  alias TaskmanWeb.DateTimeInput

  attr :field, Phoenix.HTML.FormField, required: true
  attr :id, :string, required: true
  attr :label, :string, required: true
  attr :trigger_label, :string, default: "Choose date and time"

  attr :changed_message, :string,
    default: "Date and time changed. Reopen the picker to edit the latest value."

  attr :context_id, :any, default: nil
  attr :readonly, :boolean, default: false

  def date_time_picker(assigns) do
    assigns =
      assigns
      |> assign(:value, DateTimeInput.format(assigns.field.value))
      |> assign(:canonical, DateTimeInput.canonical(assigns.field.value))

    ~H"""
    <div class="fieldset mb-2 date-time-picker-field relative">
      <.input
        field={@field}
        id={@id}
        type="text"
        label={@label}
        placeholder="DD.MM.YYYY HH:mm"
        aria-describedby={@id <> "-format"}
        autocomplete="off"
        spellcheck="false"
        value={@value}
        readonly={@readonly}
        class="w-full min-w-0 rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 pr-12 text-sm text-slate-100 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15 [color-scheme:dark]"
        error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
      />
      <span id={@id <> "-format"} class="sr-only">Format: DD.MM.YYYY HH:mm, using 24-hour time.</span>
      <div
        :if={!@readonly}
        id={@id <> "-picker"}
        phx-hook="DateTimePicker"
        phx-update="ignore"
        data-value={@canonical}
        data-input-id={@id}
        data-context-id={@context_id}
        data-changed-message={@changed_message}
      >
        <button
          id={@id <> "-trigger"}
          data-picker-role="trigger"
          type="button"
          aria-label={@trigger_label}
          aria-haspopup="dialog"
          aria-controls={@id <> "-popup"}
          aria-expanded="false"
          hidden
          class="absolute right-1.5 top-8 rounded-lg bg-slate-950 p-2 text-slate-400 transition hover:bg-slate-800 hover:text-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <.icon name="hero-calendar-days" class="size-5" />
        </button>
        <div
          id={@id <> "-popup"}
          data-picker-role="popup"
          popover="manual"
          role="dialog"
          aria-label={@label}
          tabindex="-1"
          class="date-time-picker-popup fixed m-0 rounded-xl border border-slate-700 bg-slate-950 text-slate-100 shadow-xl shadow-black/40"
        >
          <div data-calendar-scroll>
            <div data-calendar-host></div>
          </div>
          <div class="date-time-picker-actions flex items-center gap-2 border-t border-slate-700 p-2">
            <button
              id={@id <> "-clear"}
              data-picker-role="clear"
              type="button"
              class="mr-auto rounded-lg px-3 py-2 text-sm text-slate-300 hover:bg-slate-800"
            >Clear</button>
            <button
              id={@id <> "-cancel"}
              data-picker-role="cancel"
              type="button"
              class="rounded-lg px-3 py-2 text-sm text-slate-300 hover:bg-slate-800"
            >Cancel</button>
            <button
              id={@id <> "-apply"}
              data-picker-role="apply"
              type="button"
              disabled
              class="rounded-lg bg-indigo-500 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-400 disabled:opacity-40"
            >Apply</button>
          </div>
        </div>
        <span id={@id <> "-picker-notice"} role="status" aria-live="polite" class="sr-only"></span>
      </div>
    </div>
    """
  end
end
