defmodule TaskmanWeb.Tasks.Priority do
  @moduledoc "Shared Task priority labels and presentation."

  @labels %{
    none: "None",
    low: "Low",
    medium: "Medium",
    high: "High",
    urgent: "Urgent"
  }

  @text_classes %{
    none: "text-slate-300",
    low: "text-blue-300",
    medium: "text-emerald-300",
    high: "text-amber-300",
    urgent: "text-red-300"
  }

  @background_classes %{
    none: "bg-slate-800",
    low: "bg-blue-400/10",
    medium: "bg-emerald-400/10",
    high: "bg-amber-400/10",
    urgent: "bg-red-400/10"
  }

  @spec label(atom()) :: String.t()
  def label(priority), do: Map.fetch!(@labels, priority)

  @spec text_class(atom()) :: String.t()
  def text_class(priority), do: Map.fetch!(@text_classes, priority)

  @spec badge_classes(atom()) :: String.t()
  def badge_classes(priority),
    do: Map.fetch!(@background_classes, priority) <> " " <> text_class(priority)
end
