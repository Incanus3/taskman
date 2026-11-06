defmodule Taskman.Tasks.Status do
  @moduledoc "Canonical Task state definitions and lifecycle classifications."

  @definitions [
    icebox: %{
      label: "Icebox",
      description: "Intentionally deferred work that is not currently ready to begin.",
      color: :sky,
      additional?: false,
      initially_selected?: true,
      open?: true,
      resolves_blocker?: false
    },
    pending: %{
      label: "Pending",
      description: "Work that is ready to be started.",
      color: :amber,
      additional?: false,
      initially_selected?: true,
      open?: true,
      resolves_blocker?: false
    },
    in_progress: %{
      label: "In Progress",
      description: "Work actively being performed by a person, an external agent, or both.",
      color: :red,
      additional?: false,
      initially_selected?: true,
      open?: true,
      resolves_blocker?: false
    },
    in_review: %{
      label: "In Review",
      description: "Work awaiting human assessment before it can be completed.",
      color: :orange,
      additional?: false,
      initially_selected?: true,
      open?: true,
      resolves_blocker?: false
    },
    done: %{
      label: "Done",
      description:
        "The terminal state for work a human has reviewed and explicitly accepted as complete.",
      color: :emerald,
      additional?: false,
      initially_selected?: false,
      open?: false,
      resolves_blocker?: true
    },
    not_planned: %{
      label: "Not Planned",
      description:
        "Work we currently consider unnecessary, retained for reconsideration if the need arises.",
      color: :stone,
      additional?: true,
      initially_selected?: false,
      open?: false,
      resolves_blocker?: false
    },
    will_not_do: %{
      label: "Will Not Do",
      description:
        "The terminal state for work explicitly abandoned or declined by the operator.",
      color: :slate,
      additional?: false,
      initially_selected?: false,
      open?: false,
      resolves_blocker?: false
    }
  ]

  @values Keyword.keys(@definitions)
  @default_values for {state, %{additional?: false}} <- @definitions, do: state
  @additional_values for {state, %{additional?: true}} <- @definitions, do: state
  @initial_listing_values for {state, %{initially_selected?: true}} <- @definitions, do: state
  @open_values for {state, %{open?: true}} <- @definitions, do: state
  @ranks @values |> Enum.with_index() |> Map.new()
  @by_state Map.new(@definitions)

  @spec values() :: [atom()]
  def values, do: @values

  @spec default_values() :: [atom()]
  def default_values, do: @default_values

  @spec additional_values() :: [atom()]
  def additional_values, do: @additional_values

  @spec initial_listing_values() :: [atom()]
  def initial_listing_values, do: @initial_listing_values

  @spec open_values() :: [atom()]
  def open_values, do: @open_values

  @spec definition(atom()) :: %{
          label: String.t(),
          description: String.t(),
          color: atom(),
          additional?: boolean(),
          initially_selected?: boolean(),
          open?: boolean(),
          resolves_blocker?: boolean()
        }
  def definition(state), do: Map.fetch!(@by_state, state)

  @spec rank(atom()) :: non_neg_integer()
  def rank(state), do: Map.fetch!(@ranks, state)

  @spec resolves_blocker?(atom()) :: boolean()
  def resolves_blocker?(state), do: definition(state).resolves_blocker?
end
