defmodule Taskman.CLI.Execution.Result do
  @moduledoc "A value returned by the CLI before IO is performed."

  alias Taskman.CLI.Presentation.Output

  @type t :: %__MODULE__{
          status: non_neg_integer(),
          stdout: String.t(),
          stderr: String.t()
        }

  defstruct status: 0, stdout: "", stderr: ""

  @doc "Render a validated Client outcome using the command's resource presentation."
  @spec from_response({:ok, term()} | {:error, non_neg_integer(), map()}, term(), boolean()) ::
          t()
  def from_response({:ok, data}, command, json?),
    do: %__MODULE__{stdout: Output.success(command, data, json?)}

  def from_response({:error, status, envelope}, _command, json?),
    do: error(status, envelope, json?)

  @doc "Render local command data and readable text, or a local/Client error."
  @spec from_local(
          {:ok, term(), String.t()}
          | {:error, atom(), String.t()}
          | {:error, non_neg_integer(), map()},
          boolean()
        ) :: t()
  def from_local({:ok, data, _text}, true),
    do: %__MODULE__{stdout: Output.success(nil, data, true)}

  def from_local({:ok, _data, text}, false), do: %__MODULE__{stdout: text <> "\n"}

  def from_local({:error, status, envelope}, json?) when is_integer(status),
    do: error(status, envelope, json?)

  def from_local({:error, code, message}, json?) do
    status = if code == :input_unavailable, do: 5, else: 2
    envelope = %{"error" => %{"code" => Atom.to_string(code), "message" => message}}
    error(status, envelope, json?)
  end

  @doc "Render a failed outcome on stderr, preserving its status and envelope."
  @spec error(non_neg_integer(), map(), boolean()) :: t()
  def error(status, envelope, json?),
    do: %__MODULE__{status: status, stderr: Output.error(envelope, json?)}
end
