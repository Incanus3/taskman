defmodule TaskmanWeb.ProjectLive.Tasks.Messages do
  @moduledoc false

  @destination_unavailable "That destination is no longer available."

  @spec destination_unavailable() :: String.t()
  def destination_unavailable, do: @destination_unavailable

  @spec destination_unavailable_with_guidance() :: String.t()
  def destination_unavailable_with_guidance,
    do: @destination_unavailable <> " Choose another destination."

  @spec task_unavailable() :: String.t()
  def task_unavailable, do: "This Task is no longer available."

  @spec parent_unavailable() :: String.t()
  def parent_unavailable, do: "That parent Task is no longer available."
end
