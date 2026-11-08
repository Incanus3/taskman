defmodule Taskman.Tasks.Activity.EventTask do
  alias Taskman.Tasks.Activity.Event
  alias Taskman.Tasks.Task

  @moduledoc false
  use Ecto.Schema

  @primary_key false
  schema "task_activity_event_tasks" do
    belongs_to :event, Event, primary_key: true
    belongs_to :task, Task, primary_key: true
  end
end
