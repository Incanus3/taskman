defmodule Taskman.FakeTerminal do
  @behaviour Taskman.Terminal

  @responses_key {__MODULE__, :responses}
  @prompts_key {__MODULE__, :prompts}

  def set_responses(visible, secret) do
    Process.put(@responses_key, %{visible: visible, secret: secret})
    Process.put(@prompts_key, [])
  end

  @impl Taskman.Terminal
  def prompt(message), do: respond(:visible, message)

  @impl Taskman.Terminal
  def prompt_secret(message), do: respond(:secret, message)

  def prompts do
    @prompts_key
    |> Process.get([])
    |> Enum.reverse()
  end

  defp respond(kind, message) do
    Process.put(@prompts_key, [{kind, message} | Process.get(@prompts_key, [])])

    case Process.get(@responses_key, %{visible: [], secret: []}) do
      %{^kind => [response | rest]} = responses ->
        Process.put(@responses_key, %{responses | kind => rest})
        response

      _ ->
        raise "no #{kind} response configured"
    end
  end
end
