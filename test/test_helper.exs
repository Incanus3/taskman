database_pool_size = Application.fetch_env!(:taskman, Taskman.Repo) |> Keyword.fetch!(:pool_size)
max_cases = min(System.schedulers_online() * 2, database_pool_size)

ExUnit.start(max_cases: max_cases)
ExUnit.configure(exclude: [browser: true])
Ecto.Adapters.SQL.Sandbox.mode(Taskman.Repo, :manual)
