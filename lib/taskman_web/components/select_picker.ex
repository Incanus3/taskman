defmodule TaskmanWeb.SelectPicker do
  @moduledoc "Renders selection controls with keyboard interaction."

  use TaskmanWeb, :html

  attr :field, Phoenix.HTML.FormField, required: true
  attr :id, :string, required: true
  attr :label, :string, required: true
  attr :options, :list, required: true
  attr :visible_values, :list, default: nil
  attr :option_metadata, :map, default: %{}
  attr :action, :map, default: nil
  attr :disabled, :boolean, default: false
  attr :context_id, :any, default: nil

  def select_picker(assigns) do
    assigns =
      assigns
      |> assign(:invalid?, used_input?(assigns.field) && assigns.field.errors != [])
      |> assign(
        :option_data,
        Enum.map(assigns.options, fn {label, value} ->
          %{label: label, value: to_string(value)}
        end)
      )

    ~H"""
    <div class="fieldset mb-2 select-picker">
      <div
        :if={!@disabled}
        id={@id <> "-picker"}
        phx-hook="SelectPicker"
        phx-update="ignore"
        data-input-id={@id}
        data-value={to_string(@field.value)}
        data-options={Jason.encode!(@option_data)}
        data-visible-values={
          Jason.encode!(@visible_values && Enum.map(@visible_values, &to_string/1))
        }
        data-option-metadata={Jason.encode!(@option_metadata)}
        data-action={Jason.encode!(@action)}
        data-context-id={@context_id}
        data-invalid={to_string(@invalid?)}
        hidden
      >
        <span id={@id <> "-label"} class="label mb-1">{@label}</span>
        <button
          id={@id <> "-trigger"}
          type="button"
          aria-labelledby={@id <> "-label"}
          aria-describedby={@id <> "-value"}
          aria-haspopup="listbox"
          aria-expanded="false"
          aria-controls={@id <> "-options"}
          class="flex w-full items-center justify-between gap-3 rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-left text-sm text-slate-100 shadow-sm shadow-black/20 outline-none transition hover:border-slate-600 hover:bg-slate-900 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15"
        >
          <span id={@id <> "-value"} data-picker-label class="min-w-0 truncate font-medium"></span>
          <.icon name="hero-chevron-down" class="size-4 shrink-0 text-slate-400" />
        </button>
        <div
          id={@id <> "-popup"}
          popover="manual"
          class="select-picker-popup fixed m-0 rounded-xl border border-slate-700 bg-slate-950 p-1 text-slate-200 shadow-xl shadow-black/30 outline-none"
        >
          <div
            id={@id <> "-options"}
            role="listbox"
            aria-labelledby={@id <> "-label"}
            tabindex="-1"
            class="group/select-options space-y-1 outline-none"
          >
          </div>
        </div>
        <template data-picker-option>
          <button
            type="button"
            role="option"
            tabindex="-1"
            class="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/50"
          >
            <span data-option-label></span>
            <.icon name="hero-check" class="size-4 shrink-0 self-center text-indigo-300" />
          </button>
        </template>
      </div>
      <div class="select-picker-native">
        <.input
          field={@field}
          id={@id}
          type="select"
          label={@label}
          options={@options}
          disabled={@disabled}
          class="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-sm text-slate-100 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15 [color-scheme:dark]"
          error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
        />
      </div>
    </div>
    """
  end
end
