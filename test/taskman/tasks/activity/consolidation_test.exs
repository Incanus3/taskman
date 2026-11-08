defmodule Taskman.Tasks.Activity.ConsolidationTest do
  use ExUnit.Case, async: true

  alias Taskman.Tasks.Activity.Consolidation

  @now ~U[2030-01-01 10:00:00.000000Z]

  setup do
    %{
      candidate: %{
        actor_user_id: "actor",
        before_value: %{"value" => "low"},
        after_value: %{"value" => "medium"},
        last_changed_at: @now
      }
    }
  end

  test "the sliding minute is inclusive to a microsecond", %{candidate: candidate} do
    actor = %{id: "actor"}
    at_60_seconds = DateTime.add(@now, 60, :second)
    after_60_seconds = DateTime.add(at_60_seconds, 1, :microsecond)
    assert Consolidation.action(candidate, actor.id, "medium", "high", at_60_seconds) == :update

    assert Consolidation.action(
             candidate,
             actor.id,
             "medium",
             "high",
             after_60_seconds
           ) == :insert

    assert Consolidation.action(candidate, actor.id, "medium", "low", at_60_seconds) == :delete
  end

  test "different actors do not consolidate", %{candidate: candidate} do
    assert Consolidation.action(candidate, "other", "medium", "high", @now) == :insert
  end

  test "deleted actors do not consolidate", %{candidate: candidate} do
    assert Consolidation.action(
             %{candidate | actor_user_id: nil},
             nil,
             "medium",
             "high",
             @now
           ) == :insert
  end

  test "value discontinuity does not consolidate", %{candidate: candidate} do
    assert Consolidation.action(candidate, "actor", "urgent", "high", @now) == :insert
  end

  test "a backward clock starts a new event", %{candidate: candidate} do
    assert Consolidation.action(
             candidate,
             "actor",
             "medium",
             "high",
             DateTime.add(@now, -1, :microsecond)
           ) == :insert
  end

  test "canonical relationship IDs ignore historical label changes" do
    candidate = %{
      actor_user_id: "actor",
      before_value: %{"value" => nil},
      after_value: %{"value" => %{"id" => 12, "title" => "Original"}},
      last_changed_at: @now
    }

    assert Consolidation.action(
             candidate,
             "actor",
             %{"id" => 12, "title" => "Renamed"},
             nil,
             @now
           ) == :delete
  end

  test "continuous edits slide beyond one minute from the first change", %{candidate: candidate} do
    at_45 = DateTime.add(@now, 45, :second)
    assert Consolidation.action(candidate, "actor", "medium", "high", at_45) == :update
    updated = %{candidate | after_value: %{"value" => "high"}, last_changed_at: at_45}

    assert Consolidation.action(
             updated,
             "actor",
             "high",
             "urgent",
             DateTime.add(@now, 105, :second)
           ) == :update
  end

  test "cancelled other-user intervention exposes the original clock", %{candidate: a} do
    b = %{
      a
      | actor_user_id: "other",
        before_value: %{"value" => "medium"},
        after_value: %{"value" => "high"},
        last_changed_at: DateTime.add(@now, 10, :second)
    }

    assert Consolidation.action(a, "other", "medium", "high", b.last_changed_at) == :insert

    assert Consolidation.action(
             b,
             "other",
             "high",
             "medium",
             DateTime.add(@now, 20, :second)
           ) == :delete

    assert Consolidation.action(
             a,
             "actor",
             "medium",
             "urgent",
             DateTime.add(@now, 30, :second)
           ) == :update

    assert Consolidation.action(
             a,
             "actor",
             "medium",
             "urgent",
             DateTime.add(@now, 61, :second)
           ) == :insert
  end

  test "no surviving candidate starts an event" do
    assert Consolidation.action(nil, "actor", false, true, @now) == :insert
  end
end
