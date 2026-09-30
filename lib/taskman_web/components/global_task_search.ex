defmodule TaskmanWeb.GlobalTaskSearch do
  use TaskmanWeb, :live_component
  require Logger

  alias Taskman.Lists
  alias Taskman.Projects
  alias Taskman.Tasks
  alias TaskmanWeb.ProjectLive.Paths
  alias TaskmanWeb.ProjectLive.Tasks.LocationScope
  alias TaskmanWeb.Tasks.Table

  @impl true
  def update(assigns, socket) do
    socket =
      if socket.assigns[:initialized?] do
        socket
      else
        socket
        |> assign(:initialized?, true)
        |> assign(:query, "")
        |> assign(:results, [])
        |> assign(:open?, false)
        |> assign(:mobile_open?, false)
        |> assign(:active_index, nil)
        |> assign(:status, :prompt)
      end

    {:ok,
     socket
     |> assign(:id, assigns.id)
     |> assign(:enabled?, Map.get(assigns, :enabled?, Map.get(socket.assigns, :enabled?, true)))
     |> assign(:workspace, assigns[:workspace])
     |> assign(
       :search_tasks,
       assigns[:search_tasks] || socket.assigns[:search_tasks] || (&Tasks.search_tasks/1)
     )
     |> assign(
       :get_project,
       assigns[:get_project] || socket.assigns[:get_project] || (&Projects.get_project/1)
     )
     |> assign(:form, to_form(%{"query" => socket.assigns.query}, as: :search))}
  end

  @impl true
  def handle_event("open", _, socket) do
    {:noreply, socket |> assign(:open?, true) |> assign(:mobile_open?, true)}
  end

  def handle_event("focus", %{"mobile" => mobile}, socket) do
    {:noreply, socket |> assign(:open?, true) |> assign(:mobile_open?, mobile == "true")}
  end

  def handle_event("close", _, socket), do: {:noreply, reset(socket)}

  def handle_event("search", %{"search" => %{"query" => query}}, socket)
      when is_binary(query) do
    socket =
      socket
      |> assign(:query, query)
      |> assign(:form, to_form(%{"query" => query}, as: :search))
      |> assign(:open?, true)
      |> assign(:active_index, nil)

    if String.trim(query) == "" do
      {:noreply, socket |> assign(:results, []) |> assign(:status, :prompt)}
    else
      case search(socket, query) do
        {:ok, results} ->
          {:noreply,
           socket
           |> assign(:results, results)
           |> assign(:status, if(results == [], do: :no_match, else: :results))}

        :error ->
          {:noreply, search_error(socket)}
      end
    end
  end

  def handle_event("retry", _, socket) do
    handle_event("search", %{"search" => %{"query" => socket.assigns.query}}, socket)
  end

  def handle_event("key", %{"key" => key}, socket) do
    case key do
      "Escape" -> {:noreply, reset(socket)}
      "ArrowDown" -> {:noreply, move_active(socket, 1)}
      "ArrowUp" -> {:noreply, move_active(socket, -1)}
      "Enter" -> open_active(socket)
      _ -> {:noreply, socket}
    end
  end

  def handle_event("select", %{"id" => id}, socket) do
    case Integer.parse(id) do
      {task_id, ""} when task_id > 0 -> open_result(socket, task_id)
      _ -> {:noreply, socket}
    end
  end

  def handle_event("select", _, socket), do: {:noreply, socket}

  defp search(socket, query) do
    {:ok, socket.assigns.search_tasks.(query)}
  rescue
    error ->
      Logger.error(
        "Global Task search query failed: #{Exception.format(:error, error, __STACKTRACE__)}"
      )

      :error
  end

  defp search_error(socket) do
    socket
    |> assign(:results, [])
    |> assign(:active_index, nil)
    |> assign(:status, :error)
  end

  defp reset(socket) do
    socket
    |> assign(:query, "")
    |> assign(:form, to_form(%{"query" => ""}, as: :search))
    |> assign(:results, [])
    |> assign(:status, :prompt)
    |> assign(:open?, false)
    |> assign(:mobile_open?, false)
    |> assign(:active_index, nil)
  end

  defp move_active(%{assigns: %{results: []}} = socket, _step), do: socket

  defp move_active(socket, step) do
    length = length(socket.assigns.results)
    previous = socket.assigns.active_index
    next = rem((previous || if(step > 0, do: -1, else: 0)) + step + length, length)
    assign(socket, :active_index, next)
  end

  defp open_active(%{assigns: %{active_index: nil}} = socket), do: {:noreply, socket}

  defp open_active(socket) do
    socket.assigns.results
    |> Enum.at(socket.assigns.active_index)
    |> then(&open_result(socket, &1.id))
  end

  defp open_result(socket, id) do
    displayed = Enum.find(socket.assigns.results, &(&1.id == id))

    if displayed do
      # A search row is only a suggestion. Resolve its current Project and List before routing.
      case search(socket, Integer.to_string(id)) do
        {:ok, summaries} ->
          current = Enum.find(summaries, &(&1.id == id))
          location = current || displayed

          with project when not is_nil(project) <-
                 socket.assigns.get_project.(location.project_id),
               task when not is_nil(task) <- Tasks.get_task_for_project(project, id) do
            lists = Lists.list_lists_for_project(project)
            actual = Lists.get_list_for_project(project, task.list_id)

            selected =
              case socket.assigns.workspace do
                %{selected_project: %{id: project_id}} = workspace
                when project_id == project.id ->
                  LocationScope.backdrop(workspace, actual, lists)

                _ ->
                  actual
              end

            path = Paths.task_detail_path(project, selected, task, false)
            {:noreply, socket |> reset() |> push_navigate(to: path)}
          else
            _ -> stale_result(socket, location, id)
          end

        :error ->
          {:noreply, search_error(socket)}
      end
    else
      {:noreply, socket}
    end
  rescue
    error ->
      Logger.error(
        "Global Task search selection failed: #{Exception.format(:error, error, __STACKTRACE__)}"
      )

      {:noreply, search_error(socket)}
  end

  defp stale_result(socket, location, id) do
    # The existing Task detail route renders the established not-found recovery.
    path = "/projects/#{location.project_id}/tasks/#{id}"
    {:noreply, socket |> reset() |> push_navigate(to: path)}
  end

  @impl true
  def render(assigns) do
    ~H"""
    <div id={@id} class="contents" inert={!@enabled?}>
      <div
        id="global-task-search-desktop"
        class="relative hidden min-w-0 lg:block"
        data-search-surface
        phx-click-away={@open? && "close"}
        phx-target={@myself}
      >
        <.search_form
          form={@form}
          myself={@myself}
          mobile?={false}
          open?={@open? && !@mobile_open?}
          status={@status}
          results={@results}
          active_index={@active_index}
        />
      </div>

      <button
        id="global-task-search-mobile-trigger"
        type="button"
        aria-label="Search Tasks"
        aria-expanded={to_string(@mobile_open?)}
        phx-click="open"
        phx-target={@myself}
        class="mx-auto grid size-9 place-items-center rounded-lg text-slate-300 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 lg:hidden"
      >
        <.icon name="hero-magnifying-glass" class="size-5" />
      </button>

      <div
        :if={@mobile_open?}
        id="global-task-search-mobile-panel"
        class="absolute inset-x-0 top-full z-40 border-b border-slate-700 bg-slate-950 p-4 shadow-2xl lg:hidden"
        data-search-surface
        phx-click-away="close"
        phx-target={@myself}
      >
        <div class="flex items-start gap-2">
          <div class="min-w-0 flex-1">
            <.search_form
              form={@form}
              myself={@myself}
              mobile?={true}
              open?={@open?}
              status={@status}
              results={@results}
              active_index={@active_index}
            />
          </div>
          <button
            id="global-task-search-mobile-close"
            type="button"
            aria-label="Close Task search"
            phx-click={
              JS.push("close", target: @myself) |> JS.focus(to: "#global-task-search-mobile-trigger")
            }
            class="grid size-10 shrink-0 place-items-center rounded-lg text-slate-300 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            <.icon name="hero-x-mark" class="size-5" />
          </button>
        </div>
      </div>
    </div>
    """
  end

  attr :form, :any, required: true
  attr :myself, :any, required: true
  attr :mobile?, :boolean, required: true
  attr :open?, :boolean, required: true
  attr :status, :atom, required: true
  attr :results, :list, required: true
  attr :active_index, :any, required: true

  defp search_form(assigns) do
    assigns =
      assigns
      |> assign(
        :input_id,
        if(assigns.mobile?,
          do: "global-task-search-mobile-input",
          else: "global-task-search-input"
        )
      )
      |> assign(
        :form_id,
        if(assigns.mobile?, do: "global-task-search-mobile-form", else: "global-task-search-form")
      )
      |> assign(
        :results_id,
        if(assigns.mobile?,
          do: "global-task-search-mobile-results",
          else: "global-task-search-results"
        )
      )
      |> assign(
        :panel_id,
        if(assigns.mobile?,
          do: "global-task-search-mobile-suggestions",
          else: "global-task-search-suggestions"
        )
      )
      |> assign(
        :option_prefix,
        if(assigns.mobile?,
          do: "global-task-search-mobile-option",
          else: "global-task-search-option"
        )
      )
      |> assign(
        :loading_id,
        if(assigns.mobile?,
          do: "global-task-search-mobile-loading",
          else: "global-task-search-loading"
        )
      )

    ~H"""
    <.form
      for={@form}
      id={@form_id}
      phx-change="search"
      phx-target={@myself}
      class="group relative [&>.fieldset]:mb-0"
    >
      <.input
        field={@form[:query]}
        id={@input_id}
        type="search"
        aria-label="Search Tasks"
        placeholder="Search Tasks by ID or title"
        autocomplete="off"
        role="combobox"
        aria-autocomplete="list"
        aria-controls={@open? && @status == :results && @results_id}
        aria-expanded={to_string(@open? && @status == :results)}
        aria-activedescendant={
          @active_index && "#{@option_prefix}-#{Enum.at(@results, @active_index).id}"
        }
        phx-focus="focus"
        phx-value-mobile={to_string(@mobile?)}
        phx-keydown="key"
        phx-target={@myself}
        phx-hook=".GlobalTaskSearchKeyboard"
        class="w-full rounded-xl border border-slate-700 bg-slate-900 px-4 py-2.5 text-sm text-slate-100 outline-none transition placeholder:text-slate-500 hover:border-slate-600 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-400/30"
      />
      <div
        :if={@open?}
        id={@panel_id}
        class={[
          "mt-2 max-h-[min(60dvh,28rem)] overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 p-2 shadow-2xl shadow-black/40",
          !@mobile? && "absolute inset-x-0 top-full z-40"
        ]}
      >
        <p
          id={@loading_id}
          class="hidden px-3 py-2 text-sm text-slate-400 group-[.phx-change-loading]:block"
          role="status"
        >
          Searching Tasks…
        </p>
        <div class="group-[.phx-change-loading]:hidden">
          <p
            :if={@status == :prompt}
            id={
              if(@mobile?, do: "global-task-search-mobile-prompt", else: "global-task-search-prompt")
            }
            class="px-3 py-2 text-sm text-slate-400"
            role="status"
          >
            Search Tasks by ID or title
          </p>
          <p
            :if={@status == :no_match}
            id={
              if(@mobile?,
                do: "global-task-search-mobile-no-match",
                else: "global-task-search-no-match"
              )
            }
            class="px-3 py-2 text-sm text-slate-400"
            role="status"
          >
            No Tasks found
          </p>
          <div
            :if={@status == :error}
            id={if(@mobile?, do: "global-task-search-mobile-error", else: "global-task-search-error")}
            class="flex items-center justify-between gap-3 px-3 py-2 text-sm text-rose-200"
            role="alert"
          >
            <span>Task search failed. Try again.</span>
            <button
              id={
                if(@mobile?, do: "global-task-search-mobile-retry", else: "global-task-search-retry")
              }
              type="button"
              phx-click={JS.focus(to: "##{@input_id}") |> JS.push("retry", target: @myself)}
              phx-target={@myself}
              class="rounded-lg px-2 py-1 font-semibold underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            >Retry</button>
          </div>
          <div
            :if={@status == :results}
            id={@results_id}
            role="listbox"
            aria-label="Task search results"
          >
            <button
              :for={result <- @results}
              id={"#{@option_prefix}-#{result.id}"}
              type="button"
              role="option"
              aria-selected={
                to_string(@active_index == Enum.find_index(@results, &(&1.id == result.id)))
              }
              phx-click="select"
              phx-value-id={result.id}
              phx-target={@myself}
              class="flex w-full flex-col gap-1 rounded-lg px-3 py-2 text-left transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 aria-selected:bg-indigo-400/15 aria-selected:ring-1 aria-selected:ring-inset aria-selected:ring-indigo-400/40"
            >
              <span class="text-sm font-medium text-slate-100">{result.title}
              <span class="text-slate-500">#{result.id}</span></span>
              <span class="flex flex-wrap gap-x-2 text-xs text-slate-400">
                <span>{result.project_name}{if(result.location.path == [],
                  do: " / Project root",
                  else: " / " <> Enum.join(result.location.path, " / ")
                )}</span>
                <span>{Table.status_label(result.status)}</span>
                <span>{Table.priority_label(result.priority)}</span>
              </span>
            </button>
          </div>
        </div>
      </div>
    </.form>

    <script :type={Phoenix.LiveView.ColocatedHook} name=".GlobalTaskSearchKeyboard">
      export default {
        mounted() {
          this.activeResultId = this.el.getAttribute("aria-activedescendant")
          this.surface = this.el.closest("[data-search-surface]")
          this.onFocusOut = event => {
            if (!this.surface.contains(event.relatedTarget)) {
              this.pushEventTo(this.el, "close", {})
            }
          }
          this.surface.addEventListener("focusout", this.onFocusOut)
          if (this.el.id === "global-task-search-mobile-input") this.el.focus()
          this.onKeydown = event => {
            if (["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) {
              event.preventDefault()
              event.stopPropagation()
              this.pushEventTo(this.el, "key", {key: event.key})
            }
          }
          this.el.addEventListener("keydown", this.onKeydown)
          this.onSurfaceKeydown = event => {
            if (event.key !== "Escape") return
            event.preventDefault()
            event.stopPropagation()
            this.pushEventTo(this.el, "key", {key: "Escape"})
            if (this.el.id === "global-task-search-mobile-input") {
              document.getElementById("global-task-search-mobile-trigger")?.focus()
            }
          }
          this.surface.addEventListener("keydown", this.onSurfaceKeydown)
        },
        updated() {
          const activeResultId = this.el.getAttribute("aria-activedescendant")
          if (activeResultId === this.activeResultId) return
          this.activeResultId = activeResultId
          cancelAnimationFrame(this.scrollFrame)
          if (!activeResultId) return

          // Wait until the result rows have also received the LiveView patch.
          this.scrollFrame = requestAnimationFrame(() => {
            if (document.activeElement === this.el) {
              document.getElementById(activeResultId)?.scrollIntoView({
                block: "nearest", inline: "nearest", behavior: "instant"
              })
            }
          })
        },
        destroyed() {
          cancelAnimationFrame(this.scrollFrame)
          this.surface.removeEventListener("focusout", this.onFocusOut)
          this.surface.removeEventListener("keydown", this.onSurfaceKeydown)
          this.el.removeEventListener("keydown", this.onKeydown)
        }
      }
    </script>
    """
  end
end
