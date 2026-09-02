import Config

# Configure your database
#
# The MIX_TEST_PARTITION environment variable can be used
# to provide built-in test partitioning in CI environment.
# Run `mix help test` for more information.
config :taskman, Taskman.Repo,
  username: "postgres",
  password: "postgres",
  hostname: System.get_env("POSTGRES_HOST", "localhost"),
  database: "taskman_test#{System.get_env("MIX_TEST_PARTITION")}",
  pool: Ecto.Adapters.SQL.Sandbox,
  pool_size: System.schedulers_online() * 2

# We don't run a server during test. If one is required,
# you can enable the server option below.
config :taskman, TaskmanWeb.Endpoint,
  http: [ip: {127, 0, 0, 1}, port: 4002],
  secret_key_base: "wiizx8YjTFa3W5GyO4F8FX1a1b5RXHqAPDRz9slkV8Kt2Eu3eCh9gGNuFgMtbAVO",
  server: false

# In test we don't send emails
config :taskman, Taskman.Mailer, adapter: Swoosh.Adapters.Test

config :taskman,
  mail_from: {"Taskman", "no-reply@taskman.example.test"},
  mailer_delivery: Taskman.Mailer,
  public_url: "https://taskman.example.test"

config :taskman, :task_autosave_delay_ms, 0

config :taskman, :token_signing_secret, "test-token-signing-secret"

# Disable swoosh api client as it is only required for production adapters
config :swoosh, :api_client, false

# Print only warnings and errors during test
config :logger, level: :warning

# Initialize plugs at runtime for faster test compilation
config :phoenix, :plug_init_mode, :runtime

# Enable helpful, but potentially expensive runtime checks
config :phoenix_live_view,
  enable_expensive_runtime_checks: true

# Sort query params output of verified routes for robust url comparisons
config :phoenix,
  sort_verified_routes_query_params: true
