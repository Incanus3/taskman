defmodule Taskman.Tasks.Activity.EventTask do
  @moduledoc false
  use Ecto.Schema

  @primary_key false
  schema "task_activity_event_tasks" do
    belongs_to :event, Taskman.Tasks.Activity.Event, primary_key: true
    belongs_to :task, Taskman.Tasks.Task, primary_key: true
  end
end
