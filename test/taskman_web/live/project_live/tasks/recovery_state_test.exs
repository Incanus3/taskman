defmodule TaskmanWeb.ProjectLive.Recovery.StateTest do
  use ExUnit.Case, async: true

  alias TaskmanWeb.ProjectLive.Recovery.State

  test "first capture assigns the next recovery identity" do
    captured = State.capture(State.empty(), %{editing: :detail, value: "first"})

    assert captured.sequence == 1
    assert captured.snapshot == %{id: 1, editing: :detail, value: "first"}
    assert State.active?(captured)
  end

  test "repeated capture retains the first recoverable input" do
    captured = State.capture(State.empty(), %{editing: :detail, value: "first"})

    assert State.capture(captured, %{editing: :detail, value: "replacement"}) == captured
  end

  test "discard clears transient recovery data and preserves a monotonic identity" do
    first =
      State.empty()
      |> State.capture(%{editing: :detail})
      |> State.prepare(%{task_id: 12})
      |> State.put_error("retry")

    discarded = State.discard(first)

    assert discarded.sequence == 1
    assert discarded.snapshot == nil
    assert discarded.pending == nil
    assert discarded.error == nil

    second = State.capture(discarded, %{editing: :detail})
    assert second.sequence == 2
    assert second.snapshot.id == 2
  end

  test "only the active positive recovery identity matches" do
    state = State.capture(State.empty(), %{editing: :detail})

    assert State.matches?(state, "1")
    refute State.matches?(state, nil)
    refute State.matches?(state, "")
    refute State.matches?(state, "01")
    refute State.matches?(state, "0")
    refute State.matches?(state, "other")
    refute State.matches?(State.discard(state), "1")
  end
end
