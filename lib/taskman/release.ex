defmodule Taskman.Release do
  @moduledoc """
  Release-local tasks that run without Mix or Phoenix endpoint startup.
  """

  alias Taskman.{Accounts, CredentialPrompts, LocalTerminal, Repo}

  @app :taskman

  @spec migrate() :: :ok | no_return()
  def migrate do
    case migrate([]) do
      :ok -> :ok
      {:error, _reason} -> release_failure("Unable to run database migrations.")
    end
  end

  @doc false
  @spec migrate(keyword()) :: :ok | {:error, term()}
  def migrate(options) when is_list(options) do
    app = Keyword.get(options, :app, @app)

    with :ok <- load_app(app, options),
         {:ok, repos} <- configured_repos(app, options) do
      Enum.reduce_while(repos, :ok, fn repo, :ok ->
        case migrate_repo(repo, options) do
          :ok -> {:cont, :ok}
          {:error, reason} -> {:halt, {:error, {:migration_failed, repo, reason}}}
        end
      end)
    end
  end

  @spec create_admin() :: :ok | no_return()
  def create_admin do
    create_admin(Application.get_env(@app, :terminal, LocalTerminal))
  end

  @spec create_admin(module()) :: :ok | no_return()
  def create_admin(terminal) when is_atom(terminal) do
    result =
      with_repo(fn ->
        with {:ok, email} <- CredentialPrompts.prompt_for_email(terminal),
             {:ok, password} <- CredentialPrompts.prompt_for_password(terminal) do
          Accounts.bootstrap_admin(email, password)
        end
      end)

    case result do
      {:ok, {:ok, _user}} ->
        IO.puts("Administrator created.")
        :ok

      _result ->
        release_failure("Unable to create administrator.")
    end
  end

  def create_admin(_terminal), do: release_failure("Unable to create administrator.")

  defp configured_repos(app, options) do
    repo_provider =
      Keyword.get(options, :repo_provider, fn configured_app ->
        Application.fetch_env!(configured_app, :ecto_repos)
      end)

    case repo_provider.(app) do
      repos when is_list(repos) -> {:ok, repos}
      {:error, _reason} = error -> error
      _result -> {:error, :invalid_repo_configuration}
    end
  rescue
    _error -> {:error, :invalid_repo_configuration}
  catch
    _kind, _reason -> {:error, :invalid_repo_configuration}
  end

  defp load_app(app, options) do
    app_loader = Keyword.get(options, :app_loader, &load_application/1)

    case app_loader.(app) do
      :ok -> :ok
      {:error, _reason} = error -> error
      _result -> {:error, :application_load_failed}
    end
  rescue
    _error -> {:error, :application_load_failed}
  catch
    _kind, _reason -> {:error, :application_load_failed}
  end

  defp load_application(app) do
    with {:ok, _started} <- Application.ensure_all_started(:ssl),
         :ok <- Application.ensure_loaded(app) do
      :ok
    else
      {:error, _reason} = error -> error
    end
  end

  defp migrate_repo(repo, options) do
    migrator = Keyword.get(options, :migrator, &run_migrations/1)

    case migrator.(repo) do
      :ok -> :ok
      {:error, _reason} = error -> error
      _result -> {:error, :invalid_migration_result}
    end
  rescue
    _error -> {:error, :migration_failed}
  catch
    _kind, _reason -> {:error, :migration_failed}
  end

  defp run_migrations(repo) do
    case Ecto.Migrator.with_repo(repo, &Ecto.Migrator.run(&1, :up, all: true)) do
      {:ok, _result, _started} -> :ok
      {:error, _reason} -> {:error, :migration_failed}
    end
  end

  defp with_repo(operation) do
    with :ok <- load_application(@app) do
      case Ecto.Migrator.with_repo(Repo, fn _repo -> operation.() end) do
        {:ok, result, _started} -> {:ok, result}
        {:error, _reason} -> {:error, :repo_unavailable}
      end
    end
  rescue
    _error -> {:error, :repo_unavailable}
  catch
    _kind, _reason -> {:error, :repo_unavailable}
  end

  defp release_failure(message), do: raise(message)
end
