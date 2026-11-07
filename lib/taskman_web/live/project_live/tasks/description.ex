defmodule TaskmanWeb.ProjectLive.Tasks.Description do
  @moduledoc "Routes Markdown snapshots to the currently authorized description owner."
  import Phoenix.Component, only: [assign: 3]

  alias TaskmanWeb.ProjectLive.Recovery
  alias TaskmanWeb.ProjectLive.Tasks.{Autosave, Comments, Creation, Editing}

  @type context :: {:new, pos_integer()} | {:edit, pos_integer(), pos_integer()}
  @type params :: %{optional(String.t()) => term()}
  @type capture_purpose ::
          {:submit, params()}
          | {:departure, {String.t(), params()}}
          | {:route, params(), String.t()}
          | {:reconcile, Taskman.ChangeNotifications.Event.t()}
  @type capture_metadata :: %{markdown: String.t(), dirty: boolean()}
  @type pending_capture :: %{
          ref: String.t(),
          purpose: capture_purpose(),
          metadata: capture_metadata() | nil,
          reply: String.t() | nil,
          timed_out?: boolean(),
          comment_received?: boolean()
        }

  @typedoc "Messages handled by the description capture protocol; other messages are ignored."
  @type message ::
          {:description_comment_captured,
           %{editor_id: String.t(), ref: String.t(), draft: String.t()}}
          | {:description_capture_timeout, String.t()}
          | {:leaf_flushed,
             %{
               :editor_id => String.t(),
               :ref => String.t(),
               :markdown => String.t(),
               optional(:html) => String.t()
             }}
          | {:leaf_changed,
             %{
               :editor_id => String.t(),
               :markdown => String.t(),
               :dirty => boolean(),
               optional(:html) => String.t()
             }}
  @type capture_result ::
          {:ready, Phoenix.LiveView.Socket.t()} | {:pending, Phoenix.LiveView.Socket.t()}
  @type completion_result ::
          {:noreply, Phoenix.LiveView.Socket.t()}
          | {:captured, Phoenix.LiveView.Socket.t(), capture_purpose(), String.t(), boolean()}

  defmodule State do
    defstruct generation: 0,
              context: nil,
              editor_id: nil,
              initial_value: "",
              edited?: false,
              pending_capture: nil,
              route_ready: nil,
              refresh_event: nil

    @type t :: %__MODULE__{
            generation: non_neg_integer(),
            context: TaskmanWeb.ProjectLive.Tasks.Description.context() | nil,
            editor_id: String.t() | nil,
            initial_value: String.t(),
            edited?: boolean(),
            pending_capture: TaskmanWeb.ProjectLive.Tasks.Description.pending_capture() | nil,
            route_ready: String.t() | nil,
            refresh_event: Taskman.ChangeNotifications.Event.t() | nil
          }
  end

  def open(socket, context) do
    state = socket.assigns.description

    if state.context == context and state.editor_id do
      socket
    else
      generation = state.generation + 1

      state = %State{
        generation: generation,
        context: context,
        editor_id: "task-description-editor-#{generation}",
        initial_value: current_value(socket, context)
      }

      assign(socket, :description, state)
    end
  end

  def clear(socket) do
    assign(socket, :description, %State{generation: socket.assigns.description.generation})
  end

  def replace(socket, markdown) when is_binary(markdown) do
    case socket.assigns.description do
      %State{editor_id: id} = state when is_binary(id) ->
        generation = state.generation + 1

        assign(socket, :description, %State{
          generation: generation,
          context: state.context,
          editor_id: "task-description-editor-#{generation}",
          initial_value: markdown
        })

      _ ->
        socket
    end
  end

  def pending?(socket), do: not is_nil(socket.assigns.description.pending_capture)

  @doc """
  Requests a browser snapshot before continuing the given purpose.

  Requires ProjectLive's description, creation, editing, recovery, workspace and
  route assigns. `:ready` means no capture is required; `:pending` retains or
  starts a capture. An existing reconciliation capture can adopt a submit,
  departure or route purpose while retaining its refresh event.
  """
  @spec capture(Phoenix.LiveView.Socket.t(), capture_purpose()) :: capture_result()
  def capture(socket, purpose) do
    state = socket.assigns.description

    cond do
      is_nil(state.editor_id) or not editable_mounted?(socket) or
          Recovery.State.active?(socket.assigns.recovery) ->
        {:ready, socket}

      state.pending_capture ->
        case {state.pending_capture.purpose, purpose} do
          {{:reconcile, event}, {kind, _}} when kind in [:departure, :submit] ->
            pending = %{state.pending_capture | purpose: purpose}

            {:pending,
             assign(socket, :description, %{
               state
               | pending_capture: pending,
                 refresh_event: state.refresh_event || event
             })}

          {{:reconcile, event}, {:route, _, _}} ->
            pending = %{state.pending_capture | purpose: purpose}

            {:pending,
             assign(socket, :description, %{
               state
               | pending_capture: pending,
                 refresh_event: state.refresh_event || event
             })}

          _ ->
            {:pending, socket}
        end

      true ->
        ref = Integer.to_string(System.unique_integer([:positive, :monotonic]))

        pending = %{
          ref: ref,
          purpose: purpose,
          metadata: nil,
          reply: nil,
          timed_out?: false,
          comment_received?: false
        }

        Process.send_after(
          self(),
          {:description_capture_timeout, ref},
          Application.get_env(:taskman, :description_capture_timeout_ms, 5_000)
        )

        Phoenix.LiveView.send_update(Leaf, id: state.editor_id, action: :flush, ref: ref)
        {:pending, assign(socket, :description, %{state | pending_capture: pending})}
    end
  end

  defp editable_mounted?(socket) do
    case socket.assigns.description.context do
      {:new, _} ->
        not is_nil(socket.assigns.creation.form)

      {:edit, _, _} ->
        route = Map.get(socket.assigns, :route_key)

        not is_nil(socket.assigns.editing.selected_task) and
          not socket.assigns.workspace.location_not_found? and
          (socket.assigns.live_action == :show_task or
             (is_binary(route) and String.contains?(route, "/tasks/")))

      _ ->
        false
    end
  end

  def retry(socket) do
    case socket.assigns.description.pending_capture do
      %{timed_out?: true, purpose: purpose} ->
        state = socket.assigns.description
        capture(assign(socket, :description, %{state | pending_capture: nil}), purpose)

      _ ->
        {:pending, socket}
    end
  end

  @doc """
  Handles `message/0` protocol messages and ignores unrelated or stale messages.

  Requires the same ProjectLive workflow assigns as `capture/2`. Completion
  requires matching snapshot text and metadata, plus a captured comment draft
  for edit departures and routes. The `:captured` result returns the retained
  purpose, Markdown and effective dirty flag for the caller to continue.
  """
  @spec handle_info(term(), Phoenix.LiveView.Socket.t()) :: completion_result()
  def handle_info(
        {:description_comment_captured, %{editor_id: id, ref: ref, draft: draft}},
        socket
      )
      when is_binary(draft) do
    case socket.assigns.description do
      %State{
        editor_id: ^id,
        context: {:edit, _, _},
        pending_capture: %{ref: ^ref, timed_out?: false, comment_received?: false} = pending
      } = state ->
        socket =
          socket
          |> Comments.set_draft(draft)
          |> assign(:description, %{state | pending_capture: %{pending | comment_received?: true}})

        finish_capture(socket)

      _ ->
        {:noreply, socket}
    end
  end

  def handle_info({:description_capture_timeout, ref}, socket) do
    case socket.assigns.description.pending_capture do
      %{ref: ^ref} = pending ->
        state = socket.assigns.description

        {:noreply,
         assign(socket, :description, %{state | pending_capture: %{pending | timed_out?: true}})}

      _ ->
        {:noreply, socket}
    end
  end

  def handle_info({:leaf_flushed, %{editor_id: id, ref: ref, markdown: markdown}}, socket) do
    case socket.assigns.description do
      %State{editor_id: ^id, pending_capture: %{ref: ^ref, timed_out?: false} = pending} = state
      when is_binary(markdown) ->
        finish_capture(
          assign(socket, :description, %{state | pending_capture: %{pending | reply: markdown}})
        )

      _ ->
        {:noreply, socket}
    end
  end

  def handle_info({:leaf_changed, %{editor_id: id, markdown: markdown, dirty: dirty}}, socket)
      when is_binary(markdown) and is_boolean(dirty) do
    state = socket.assigns.description

    cond do
      state.editor_id != id ->
        {:noreply, socket}

      state.pending_capture ->
        pending = %{state.pending_capture | metadata: %{markdown: markdown, dirty: dirty}}
        finish_capture(assign(socket, :description, %{state | pending_capture: pending}))

      (dirty or state.edited?) and authorized?(socket, state.context) and
        not Recovery.blocked?(socket) and not socket.assigns.workspace.location_not_found? and
          markdown != current_value(socket, state.context) ->
        socket = assign(socket, :description, %{state | edited?: true})

        socket =
          case state.context do
            {:new, _} -> Creation.change_description(socket, markdown)
            {:edit, _, _} -> Editing.change_description(socket, markdown)
          end

        {:noreply, socket}

      true ->
        {:noreply, socket}
    end
  end

  def handle_info(_message, socket), do: {:noreply, socket}

  defp finish_capture(socket) do
    state = socket.assigns.description

    case state.pending_capture do
      %{
        reply: markdown,
        metadata: %{markdown: markdown, dirty: dirty},
        purpose: purpose,
        timed_out?: false
      }
      when is_binary(markdown) ->
        if comment_required?(state.context, purpose) and
             not state.pending_capture.comment_received? do
          {:noreply, socket}
        else
          socket = assign(socket, :description, %{state | pending_capture: nil})
          socket = capture_value(socket, markdown, dirty or state.edited?)
          {:captured, socket, purpose, markdown, dirty or state.edited?}
        end

      _ ->
        {:noreply, socket}
    end
  end

  defp comment_required?({:edit, _, _}, {:departure, _}), do: true
  defp comment_required?({:edit, _, _}, {:route, _, _}), do: true
  defp comment_required?(_, _), do: false

  defp capture_value(socket, _markdown, false), do: socket

  defp capture_value(socket, markdown, true) do
    if markdown == current_value(socket, socket.assigns.description.context) do
      socket
    else
      capture_changed_value(socket, markdown)
    end
  end

  defp capture_changed_value(socket, markdown) do
    state = socket.assigns.description
    socket = assign(socket, :description, %{state | edited?: true})

    case state.context do
      {:new, _} ->
        Creation.change_description(socket, markdown)

      {:edit, _, _} ->
        editing = socket.assigns.editing
        autosave = Autosave.capture_description(editing.autosave, editing.selected_task, markdown)
        assign(socket, :editing, Editing.State.put_autosave(editing, autosave))
    end
  end

  defp authorized?(
         %{
           assigns: %{
             live_action: :new_task,
             workspace: %{selected_project: %{id: id}},
             creation: %{form: form}
           }
         },
         {:new, id}
       ),
       do: not is_nil(form)

  defp authorized?(
         %{
           assigns: %{
             live_action: :show_task,
             workspace: %{selected_project: %{id: id}},
             editing: %{selected_task: %{id: task_id}}
           }
         },
         {:edit, id, task_id}
       ),
       do: true

  defp authorized?(_socket, _context), do: false

  defp current_value(socket, {:new, _}),
    do: socket.assigns.creation.form[:description].value || ""

  defp current_value(socket, {:edit, _, _}),
    do: socket.assigns.editing.autosave.form[:description].value || ""
end
