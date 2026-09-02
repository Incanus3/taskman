defmodule Taskman.LocalTerminal do
  @behaviour Taskman.Terminal

  @impl Taskman.Terminal
  def prompt(message) do
    case IO.gets(message) do
      :eof -> ""
      input -> String.trim(input)
    end
  end

  @impl Taskman.Terminal
  def prompt_secret(message) do
    case :io.get_password(String.to_charlist(message)) do
      :eof -> ""
      password -> List.to_string(password)
    end
  end
end
