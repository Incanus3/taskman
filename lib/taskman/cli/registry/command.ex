defmodule Taskman.CLI.Registry.Command do
  @moduledoc "Declarative metadata for one Taskman CLI command."

  alias Taskman.CLI.Registry.{Argument, Option}

  @type t :: %__MODULE__{
          path: [String.t()],
          summary: String.t(),
          usage: String.t(),
          handler: term(),
          context_role:
            :local
            | :project_required
            | :project_filter
            | :host_only
            | :bind
            | :effective_inspection,
          arguments: [Argument.t()],
          options: [Option.t()],
          constraints: [term()],
          examples: [String.t()]
        }

  defstruct [
    :path,
    :summary,
    :usage,
    :handler,
    context_role: :local,
    arguments: [],
    options: [],
    constraints: [],
    examples: []
  ]

  @doc "Whether a command forbids the named global option."
  @spec forbidden_global?(t(), atom()) :: boolean()
  def forbidden_global?(%__MODULE__{constraints: constraints}, option_name) do
    Enum.any?(constraints, fn
      {:forbid_global, names} -> option_name in names
      _constraint -> false
    end)
  end
end
