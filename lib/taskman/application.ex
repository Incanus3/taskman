defmodule Taskman.Application do
  # See https://elixir.hexdocs.pm/Application.html
  # for more information on OTP Applications
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      TaskmanWeb.Telemetry,
      Taskman.Repo,
      {DNSCluster, query: Application.get_env(:taskman, :dns_cluster_query) || :ignore},
      {Phoenix.PubSub, name: Taskman.PubSub},
      {Taskman.Accounts.RateLimit, clean_period: :timer.minutes(1)},
      # Start a worker by calling: Taskman.Worker.start_link(arg)
      # {Taskman.Worker, arg},
      # Start to serve requests, typically the last entry
      TaskmanWeb.Endpoint
    ]

    # See https://elixir.hexdocs.pm/Supervisor.html
    # for other strategies and supported options
    opts = [strategy: :one_for_one, name: Taskman.Supervisor]
    Supervisor.start_link(children, opts)
  end

  # Tell Phoenix to update the endpoint configuration
  # whenever the application is updated.
  @impl true
  def config_change(changed, _new, removed) do
    TaskmanWeb.Endpoint.config_change(changed, removed)
    :ok
  end
end
