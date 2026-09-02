defmodule Mix.Tasks.Taskman.Accounts.CreateAdmin do
  use Mix.Task

  alias Taskman.{Accounts, LocalTerminal}

  @shortdoc "Creates an active, confirmed Taskman administrator"
  @requirements ["app.start"]

  @impl Mix.Task
  def run([]) do
    terminal = Application.get_env(:taskman, :terminal, LocalTerminal)

    with email when is_binary(email) <- terminal.prompt("Email: "),
         password when is_binary(password) <- terminal.prompt_secret("Password: "),
         password_confirmation when is_binary(password_confirmation) <-
           terminal.prompt_secret("Confirm password: "),
         {:ok, _user} <-
           Accounts.bootstrap_admin(%{
             email: email,
             password: password,
             password_confirmation: password_confirmation
           }) do
      Mix.shell().info("Administrator created.")
    else
      _result -> Mix.raise("Unable to create administrator.")
    end
  end

  def run(_args), do: Mix.raise("Unable to create administrator.")
end
