defmodule Mix.Tasks.Taskman.Accounts.CreateAdmin do
  use Mix.Task

  alias Taskman.{Accounts, LocalTerminal}

  @shortdoc "Creates an active, confirmed Taskman administrator"
  @requirements ["app.start"]

  @impl Mix.Task
  def run([]) do
    terminal = Application.get_env(:taskman, :terminal, LocalTerminal)

    case Accounts.bootstrap_admin(%{
           email: terminal.prompt("Email: "),
           password: terminal.prompt_secret("Password: "),
           password_confirmation: terminal.prompt_secret("Confirm password: ")
         }) do
      {:ok, _user} ->
        Mix.shell().info("Administrator created.")

      {:error, _reason} ->
        Mix.raise("Unable to create administrator.")
    end
  end

  def run(_args), do: Mix.raise("Unable to create administrator.")
end
