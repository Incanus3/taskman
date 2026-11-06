defmodule TaskmanWeb.Tasks.Status do
  @moduledoc "Shared Task status labels and presentation."

  alias Taskman.Tasks.Status

  @text_classes %{
    sky: "text-sky-300",
    amber: "text-amber-300",
    violet: "text-violet-300",
    red: "text-red-300",
    orange: "text-orange-300",
    emerald: "text-emerald-300",
    stone: "text-stone-400",
    slate: "text-slate-400"
  }

  @background_classes %{
    sky: "bg-sky-400/10",
    amber: "bg-amber-400/10",
    violet: "bg-violet-400/10",
    red: "bg-red-400/10",
    orange: "bg-orange-400/10",
    emerald: "bg-emerald-400/10",
    stone: "bg-stone-700/40",
    slate: "bg-slate-700/60"
  }

  @spec label(atom()) :: String.t()
  def label(status), do: Status.definition(status).label

  def description(status), do: Status.definition(status).description

  @spec text_class(atom()) :: String.t()
  def text_class(status), do: Map.fetch!(@text_classes, Status.definition(status).color)

  @spec badge_classes(atom()) :: String.t()
  def badge_classes(status),
    do:
      Map.fetch!(@background_classes, Status.definition(status).color) <>
        " " <> text_class(status)
end
