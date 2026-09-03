defmodule Mix.Tasks.Taskman.Accounts.CreateAdmin do
  use Mix.Task

  alias Taskman.{Accounts, CredentialPrompts, LocalTerminal}

  @shortdoc "Creates an active, confirmed Taskman administrator"
  @requirements ["app.start"]

  @impl Mix.Task
  def run([]) do
    terminal = Application.get_env(:taskman, :terminal, LocalTerminal)

    case create_admin(terminal) do
      {:ok, _user} ->
        Mix.shell().info("Administrator created.")

      {:error, _reason} ->
        Mix.raise("Unable to create administrator.")
    end
  end

  def run(_args), do: Mix.raise("Unable to create administrator.")

  defp create_admin(terminal) do
    with {:ok, email} <- CredentialPrompts.prompt_for_email(terminal),
         {:ok, password} <- CredentialPrompts.prompt_for_password(terminal) do
      Accounts.bootstrap_admin(email, password)
    end
  rescue
    _error -> {:error, :bootstrap_failed}
  catch
    _kind, _reason -> {:error, :bootstrap_failed}
  end
end
