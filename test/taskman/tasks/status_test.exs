defmodule Taskman.Tasks.StatusTest do
  use ExUnit.Case, async: true

  alias Taskman.Tasks.{Status, Task}

  @values [:icebox, :pending, :in_progress, :in_review, :done, :not_planned, :will_not_do]

  test "catalogue defines the canonical order and independent lifecycle classifications" do
    assert Status.values() == @values
    assert Task.statuses() == @values

    assert Status.default_values() == [
             :icebox,
             :pending,
             :in_progress,
             :in_review,
             :done,
             :will_not_do
           ]

    assert Status.additional_values() == [:not_planned]
    assert Status.initial_listing_values() == [:icebox, :pending, :in_progress, :in_review]
    assert Status.open_values() == [:icebox, :pending, :in_progress, :in_review]
    assert %Task{}.status == :pending

    for {state, rank} <- Enum.with_index(@values) do
      definition = Status.definition(state)
      assert Status.rank(state) == rank
      assert definition.additional? == (state == :not_planned)

      assert definition.initially_selected? ==
               state in [:icebox, :pending, :in_progress, :in_review]

      assert definition.open? == state in [:icebox, :pending, :in_progress, :in_review]
      assert definition.resolves_blocker? == (state == :done)
      assert Status.resolves_blocker?(state) == (state == :done)
    end

    refute Status.definition(:not_planned).open?
    refute Status.resolves_blocker?(:will_not_do)
    assert Status.resolves_blocker?(:done)
  end

  test "definitions preserve usage descriptions and presentation tokens" do
    expected = [
      {:icebox, "Icebox", "Intentionally deferred work that is not currently ready to begin.",
       :sky},
      {:pending, "Pending", "Work that is ready to be started.", :amber},
      {:in_progress, "In Progress",
       "Work actively being performed by a person, an external agent, or both.", :red},
      {:in_review, "In Review", "Work awaiting human assessment before it can be completed.",
       :orange},
      {:done, "Done",
       "The terminal state for work a human has reviewed and explicitly accepted as complete.",
       :emerald},
      {:not_planned, "Not Planned",
       "Work we currently consider unnecessary, retained for reconsideration if the need arises.",
       :stone},
      {:will_not_do, "Will Not Do",
       "The terminal state for work explicitly abandoned or declined by the operator.", :slate}
    ]

    for {state, label, description, color} <- expected do
      assert %{label: ^label, description: ^description, color: ^color} = Status.definition(state)
    end

    assert Status.definition(:not_planned).label == "Not Planned"

    assert Status.definition(:not_planned).description ==
             "Work we currently consider unnecessary, retained for reconsideration if the need arises."
  end
end
