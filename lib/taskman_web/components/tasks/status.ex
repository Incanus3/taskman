defmodule TaskmanWeb.Tasks.Status do
  @moduledoc "Shared Task status labels and presentation."

  @labels %{
    icebox: "Icebox",
    pending: "Pending",
    in_progress: "In Progress",
    in_review: "In Review",
    done: "Done",
    will_not_do: "Will Not Do"
  }

  @text_classes %{
    icebox: "text-sky-300",
    pending: "text-amber-300",
    in_progress: "text-red-300",
    in_review: "text-orange-300",
    done: "text-emerald-300",
    will_not_do: "text-slate-400"
  }

  @background_classes %{
    icebox: "bg-sky-400/10",
    pending: "bg-amber-400/10",
    in_progress: "bg-red-400/10",
    in_review: "bg-orange-400/10",
    done: "bg-emerald-400/10",
    will_not_do: "bg-slate-700/60"
  }

  @spec label(atom()) :: String.t()
  def label(status), do: Map.fetch!(@labels, status)

  @spec text_class(atom()) :: String.t()
  def text_class(status), do: Map.fetch!(@text_classes, status)

  @spec badge_classes(atom()) :: String.t()
  def badge_classes(status),
    do: Map.fetch!(@background_classes, status) <> " " <> text_class(status)
end
