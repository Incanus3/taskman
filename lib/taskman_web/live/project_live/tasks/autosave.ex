defmodule TaskmanWeb.ProjectLive.Tasks.Autosave do
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Conflict
  alias Taskman.Tasks.Task

  @editable_fields ~w(title description status priority due_at)
  @debounced_fields ~w(title description)

  defstruct form: nil,
            baseline: nil,
            draft: %{},
            dirty_fields: MapSet.new(),
            revisions: %{},
            conflicts: %{},
            field_states: %{},
            sequence: 0,
            save_failed?: false,
            saved?: false,
            save_state: :idle

  @type save_state :: :idle | :saving | :saved | :not_saved | :failed | :conflicted

  @type t :: %__MODULE__{
          form: Phoenix.HTML.Form.t() | nil,
          baseline: Task.t() | nil,
          draft: map(),
          dirty_fields: MapSet.t(String.t()),
          revisions: %{optional(String.t()) => non_neg_integer()},
          conflicts: %{optional(String.t()) => term()},
          field_states: %{optional(String.t()) => field_state()},
          sequence: non_neg_integer(),
          save_failed?: boolean(),
          saved?: boolean(),
          save_state: save_state()
        }

  @type field_state :: :saving | :saved | :not_saved | :failed

  def empty, do: %__MODULE__{}

  def load(%__MODULE__{sequence: sequence}, %Task{} = task, opts) do
    saved? = Keyword.fetch!(opts, :saved?)

    %__MODULE__{
      sequence: sequence,
      form: task |> Tasks.change_task() |> Phoenix.Component.to_form(),
      baseline: task,
      saved?: saved?,
      save_state: if(saved?, do: :saved, else: :idle)
    }
  end

  def clear(%__MODULE__{sequence: sequence}), do: %__MODULE__{sequence: sequence}

  def editable_fields, do: @editable_fields

  @spec field_state(t(), String.t()) :: field_state() | nil
  def field_state(%__MODULE__{} = autosave, field) when field in @editable_fields,
    do: Map.get(autosave.field_states, field)

  def field_state(%__MODULE__{}, _field), do: nil

  @doc "Reconciles captured input against fresh persistence without scheduling or saving it."
  @spec resume(t(), Task.t()) :: t()
  def resume(%__MODULE__{} = autosave, %Task{} = persisted_task) do
    autosave
    |> Map.put(:revisions, %{})
    |> Map.put(:field_states, %{})
    |> Map.put(:save_failed?, false)
    |> Map.put(:saved?, false)
    |> reconcile(persisted_task)
    |> then(fn resumed ->
      resumed
      |> set_dirty_field_states(:not_saved)
      |> clear_conflicted_field_states()
      |> refresh_save_state()
    end)
  end

  @doc "Restarts eligible reconciled saves under fresh Task authority."
  @spec restart(t(), Project.t(), Task.t()) ::
          {:ok, t(), Task.t(), [{non_neg_integer(), tuple()}]} | {:not_found, t()}
  def restart(%__MODULE__{} = autosave, %Project{} = project, %Task{} = task) do
    autosave.dirty_fields
    |> MapSet.to_list()
    |> Enum.sort()
    |> Enum.reduce_while({:ok, autosave, task, []}, fn field, {:ok, autosave, task, schedules} ->
      cond do
        Map.has_key?(autosave.conflicts, field) ->
          {:cont, {:ok, clear_field_state(autosave, field), task, schedules}}

        valid_field?(autosave, task, field) and field in @debounced_fields ->
          {:schedule, autosave, task, delay_ms, message} = schedule_field(autosave, task, field)
          {:cont, {:ok, autosave, task, [{delay_ms, message} | schedules]}}

        valid_field?(autosave, task, field) ->
          case persist_field(autosave, project, task, field) do
            {:ok, autosave, task} -> {:cont, {:ok, autosave, task, schedules}}
            {:conflict, autosave, task} -> {:cont, {:ok, autosave, task, schedules}}
            {:not_found, autosave} -> {:halt, {:not_found, autosave}}
          end

        true ->
          {:cont, {:ok, put_field_state(autosave, field, :not_saved), task, schedules}}
      end
    end)
    |> case do
      {:ok, autosave, task, schedules} ->
        {:ok, refresh_save_state(autosave), task, Enum.reverse(schedules)}

      {:not_found, autosave} ->
        {:not_found, autosave}
    end
  end

  @spec reconcile(t(), Task.t()) :: t()
  def reconcile(%__MODULE__{} = autosave, %Task{} = persisted_task) do
    baseline = autosave.baseline || persisted_task
    draft = Map.take(autosave.draft, MapSet.to_list(autosave.dirty_fields))

    {conflicts, revisions, field_states} =
      Enum.reduce(
        autosave.dirty_fields,
        {autosave.conflicts, autosave.revisions, autosave.field_states},
        fn field, {conflicts, revisions, field_states} ->
          field_atom = editable_field_atom(field)

          if Map.get(baseline, field_atom) != Map.get(persisted_task, field_atom) do
            {Map.put(conflicts, field, Map.get(persisted_task, field_atom)),
             Map.delete(revisions, field), Map.delete(field_states, field)}
          else
            {conflicts, revisions, field_states}
          end
        end
      )

    autosave
    |> Map.put(:baseline, persisted_task)
    |> Map.put(:draft, draft)
    |> Map.put(:conflicts, conflicts)
    |> Map.put(:revisions, revisions)
    |> Map.put(:field_states, field_states)
    |> put_form(persisted_task)
  end

  @spec resolve_conflict(t(), Project.t(), Task.t(), String.t(), :use_latest | :keep_mine) ::
          {:ok, t(), Task.t()}
          | {:conflict, t(), Task.t()}
          | {:not_found, t()}
          | {:error, t(), Task.t()}
          | {:ignored, t(), Task.t()}
  def resolve_conflict(
        %__MODULE__{} = autosave,
        %Project{} = project,
        %Task{} = persisted_task,
        field,
        resolution
      )
      when field in @editable_fields and resolution in [:use_latest, :keep_mine] do
    if Map.has_key?(autosave.conflicts, field) do
      case resolution do
        :use_latest ->
          canonical_task = autosave.baseline || persisted_task

          {:ok, clear_field(autosave, field, canonical_task), canonical_task}

        :keep_mine ->
          retry_conflicted_field(autosave, project, persisted_task, field)
      end
    else
      {:ignored, autosave, persisted_task}
    end
  end

  def resolve_conflict(%__MODULE__{} = autosave, _project, %Task{} = task, _field, _resolution),
    do: {:ignored, autosave, task}

  @spec conflict_value(t(), String.t()) :: term() | nil
  def conflict_value(%__MODULE__{} = autosave, field) when field in @editable_fields,
    do: Map.get(autosave.conflicts, field)

  def conflict_value(%__MODULE__{}, _field), do: nil

  def change(
        %__MODULE__{} = autosave,
        %Project{} = project,
        %Task{} = task,
        task_params,
        field
      )
      when field in @editable_fields do
    draft =
      case Map.fetch(task_params, field) do
        {:ok, value} -> Map.put(autosave.draft, field, value)
        :error -> autosave.draft
      end

    autosave =
      autosave
      |> Map.put(:draft, draft)
      |> clear_field_state(field)
      |> Map.update!(:dirty_fields, &MapSet.put(&1, field))
      |> put_form(autosave.baseline || task)

    if Map.has_key?(autosave.conflicts, field) do
      autosave =
        autosave
        |> Map.update!(:revisions, &Map.delete(&1, field))
        |> clear_field_state(field)

      {:ok, refresh_save_state(autosave), task}
    else
      if field in @debounced_fields do
        schedule_field(autosave, task, field)
      else
        persist_field(autosave, project, task, field)
      end
    end
  end

  def change(%__MODULE__{} = autosave, _project, %Task{} = task, _task_params, _field) do
    {:ignored, autosave, task}
  end

  def handle_scheduled_save(
        %__MODULE__{} = autosave,
        %Project{} = project,
        %Task{} = task,
        task_id,
        field,
        revision
      ) do
    if task.id == task_id and not is_nil(revision) and
         Map.get(autosave.revisions, field) == revision do
      persist_field(autosave, project, task, field)
    else
      {:ignored, autosave, task}
    end
  end

  def flush(%__MODULE__{} = autosave, %Project{} = project, %Task{} = task) do
    if autosave.conflicts != %{} do
      {:error, refresh_save_state(autosave), task}
    else
      {result, autosave, task, save_failed?} =
        Enum.reduce_while(autosave.dirty_fields, {:ok, autosave, task, false}, fn field,
                                                                                  {:ok, autosave,
                                                                                   task,
                                                                                   save_failed?} ->
          autosave = %{autosave | save_failed?: false}

          case persist_field(autosave, project, task, field) do
            {:ok, autosave, task} ->
              {:cont, {:ok, autosave, task, save_failed? || autosave.save_failed?}}

            {:conflict, autosave, task} ->
              {:halt, {:conflict, autosave, task, save_failed?}}

            {:not_found, autosave} ->
              {:halt, {:not_found, autosave, task, save_failed?}}
          end
        end)

      case result do
        :not_found ->
          {:not_found, autosave}

        :conflict ->
          {:error, refresh_save_state(autosave), task}

        :ok ->
          autosave =
            autosave
            |> Map.put(:save_failed?, save_failed?)
            |> refresh_save_state()

          if save_failed? do
            {:error, autosave, task}
          else
            {:ok, autosave, task}
          end
      end
    end
  end

  def message(%__MODULE__{save_state: :idle}), do: "Autosaves changes"
  def message(%__MODULE__{save_state: :saving}), do: "Saving…"
  def message(%__MODULE__{save_state: :saved}), do: "Saved"
  def message(%__MODULE__{save_state: :not_saved}), do: "Not saved"
  def message(%__MODULE__{save_state: :failed}), do: "Couldn’t save changes"
  def message(%__MODULE__{save_state: :conflicted}), do: "Resolve conflicting changes"

  defp put_form(%__MODULE__{} = autosave, %Task{} = task) do
    form =
      task
      |> Tasks.change_task(autosave.draft)
      |> Map.put(:action, :validate)
      |> Phoenix.Component.to_form()

    %{autosave | form: form}
    |> refresh_save_state()
  end

  defp schedule_field(%__MODULE__{} = autosave, %Task{} = task, field) do
    if valid_field?(autosave, task, field) do
      revision = autosave.sequence + 1

      autosave =
        autosave
        |> Map.put(:sequence, revision)
        |> Map.update!(:revisions, &Map.put(&1, field, revision))
        |> put_field_state(field, :saving)
        |> refresh_save_state()

      {:schedule, autosave, task, autosave_delay_ms(),
       {:autosave_task_field, task.id, field, revision}}
    else
      {:ok, autosave |> put_field_state(field, :not_saved) |> refresh_save_state(), task}
    end
  end

  defp autosave_delay_ms do
    Application.get_env(:taskman, :task_autosave_delay_ms, 300)
  end

  defp persist_field(%__MODULE__{} = autosave, %Project{} = project, %Task{} = task, field) do
    value = Map.get(autosave.draft, field)

    if valid_field?(autosave, task, field) do
      case Tasks.update_task(project, task, %{editable_field_atom(field) => value}) do
        {:ok, updated_task} ->
          autosave =
            autosave
            |> clear_field(field, updated_task)

          {:ok, autosave, updated_task}

        {:error, %Conflict{task: current_task}} ->
          {:conflict, reconcile(autosave, current_task), current_task}

        {:error, %Ecto.Changeset{}} ->
          autosave =
            autosave
            |> put_field_state(field, :failed)
            |> refresh_save_state()

          {:ok, autosave, task}

        {:error, :not_found} ->
          {:not_found, clear(autosave)}
      end
    else
      {:ok, autosave |> put_field_state(field, :not_saved) |> refresh_save_state(), task}
    end
  end

  defp refresh_save_state(%__MODULE__{} = autosave) do
    save_failed? = Enum.any?(autosave.field_states, fn {_field, state} -> state == :failed end)

    save_state =
      cond do
        autosave.conflicts != %{} ->
          :conflicted

        save_failed? ->
          :failed

        Enum.any?(autosave.field_states, fn {_field, state} -> state == :not_saved end) ->
          :not_saved

        MapSet.size(autosave.dirty_fields) > 0 ->
          :saving

        Enum.any?(autosave.field_states, fn {_field, state} -> state == :saved end) ->
          :saved

        autosave.saved? ->
          :saved

        true ->
          :idle
      end

    %{autosave | save_failed?: save_failed?, save_state: save_state}
  end

  defp retry_conflicted_field(%__MODULE__{} = autosave, %Project{} = project, task, field) do
    canonical_task = autosave.baseline || task
    value = Map.get(autosave.draft, field)

    case Tasks.update_task(project, canonical_task, %{editable_field_atom(field) => value}) do
      {:ok, updated_task} ->
        {:ok, clear_field(autosave, field, updated_task), updated_task}

      {:error, %Conflict{task: current_task}} ->
        {:conflict, reconcile(autosave, current_task), current_task}

      {:error, %Ecto.Changeset{}} ->
        autosave =
          autosave
          |> put_field_state(field, :failed)
          |> refresh_save_state()

        {:error, autosave, canonical_task}

      {:error, :not_found} ->
        {:not_found, clear(autosave)}
    end
  end

  defp clear_field(%__MODULE__{} = autosave, field, %Task{} = task) do
    autosave
    |> Map.update!(:draft, &Map.delete(&1, field))
    |> Map.update!(:dirty_fields, &MapSet.delete(&1, field))
    |> Map.update!(:revisions, &Map.delete(&1, field))
    |> Map.update!(:conflicts, &Map.delete(&1, field))
    |> Map.put(:saved?, true)
    |> reconcile(task)
    |> put_field_state(field, :saved)
    |> refresh_save_state()
  end

  defp valid_field?(%__MODULE__{} = autosave, %Task{} = task, field) do
    Tasks.change_task(task, %{field => Map.get(autosave.draft, field)}).valid?
  end

  defp put_field_state(%__MODULE__{} = autosave, field, state),
    do: %{autosave | field_states: Map.put(autosave.field_states, field, state)}

  defp clear_field_state(%__MODULE__{} = autosave, field),
    do: %{autosave | field_states: Map.delete(autosave.field_states, field)}

  defp set_dirty_field_states(%__MODULE__{} = autosave, state) do
    Enum.reduce(autosave.dirty_fields, autosave, fn field, autosave ->
      put_field_state(autosave, field, state)
    end)
  end

  defp clear_conflicted_field_states(%__MODULE__{} = autosave) do
    Enum.reduce(Map.keys(autosave.conflicts), autosave, &clear_field_state(&2, &1))
  end

  defp editable_field_atom("title"), do: :title
  defp editable_field_atom("description"), do: :description
  defp editable_field_atom("status"), do: :status
  defp editable_field_atom("priority"), do: :priority
  defp editable_field_atom("due_at"), do: :due_at
end
