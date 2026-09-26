ExUnit.start()
ExUnit.configure(exclude: [browser: true])
Ecto.Adapters.SQL.Sandbox.mode(Taskman.Repo, :manual)
