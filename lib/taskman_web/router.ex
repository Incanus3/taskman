defmodule TaskmanWeb.Router do
  use TaskmanWeb, :router
  use AshAuthentication.Phoenix.Router

  import AshAdmin.Router
  import TaskmanWeb.LiveUserAuth, only: [capture_return_path: 2]

  pipeline :browser do
    plug :accepts, ["html"]
    plug :fetch_session
    plug :load_from_session
    plug :capture_return_path
    plug :fetch_live_flash
    plug :put_root_layout, html: {TaskmanWeb.Layouts, :root}
    plug :protect_from_forgery
    plug :put_secure_browser_headers
  end

  pipeline :api do
    plug :accepts, ["json"]
    plug TaskmanWeb.Plugs.ApiAuthentication
  end

  scope "/", TaskmanWeb do
    pipe_through :browser

    sign_in_route reset_path: "/reset-password",
                  auth_routes_prefix: "/auth",
                  overrides: [
                    TaskmanWeb.AuthOverrides,
                    AshAuthentication.Phoenix.Overrides.Default
                  ]

    delete "/sign-out", AuthController, :sign_out
    post "/account/settings/password", AuthController, :update_password
    post "/account/settings/delete", AuthController, :delete_account
    post "/auth/user/password/reset", AuthController, :reset_password
    post "/auth/user/password/reset_request", AuthController, :request_password_reset

    auth_routes AuthController, Taskman.Accounts.User

    reset_route path: "/reset-password",
                auth_routes_prefix: "/auth",
                overrides: [TaskmanWeb.AuthOverrides, AshAuthentication.Phoenix.Overrides.Default]

    confirm_route Taskman.Accounts.User, :setup,
      path: "/setup",
      auth_routes_prefix: "/auth",
      as: :setup,
      overrides: [TaskmanWeb.AuthOverrides, AshAuthentication.Phoenix.Overrides.Default]

    confirm_route Taskman.Accounts.User, :email_change,
      path: "/confirm-email",
      auth_routes_prefix: "/auth",
      as: :email_change,
      overrides: [TaskmanWeb.AuthOverrides, AshAuthentication.Phoenix.Overrides.Default]

    get "/healthz", HealthController, :show

    ash_authentication_live_session :authenticated,
      on_mount: [{TaskmanWeb.LiveUserAuth, :require_authenticated}],
      session: {TaskmanWeb.LiveUserAuth, :generate_session, []} do
      live "/", ProjectLive, :index
      live "/account/settings", AccountSettingsLive, :index
      live "/projects/:project_id", ProjectLive, :show
      live "/projects/:project_id/tasks/new", ProjectLive, :new_task
      live "/projects/:project_id/tasks/:task_id", ProjectLive, :show_task
      live "/projects/:project_id/lists/:list_id", ProjectLive, :show
      live "/projects/:project_id/lists/:list_id/tasks/new", ProjectLive, :new_task
      live "/projects/:project_id/lists/:list_id/tasks/:task_id", ProjectLive, :show_task
    end

    ash_authentication_live_session :admin_user_inspection,
      on_mount: [{TaskmanWeb.LiveUserAuth, :admin_required}],
      session: {TaskmanWeb.LiveUserAuth, :generate_session, []} do
      live "/admin/users/:id", AdminUserLive, :show
    end
  end

  scope "/" do
    pipe_through :browser

    ash_admin "/admin",
              AshAuthentication.Phoenix.LiveSession.opts(
                on_mount: [{TaskmanWeb.LiveUserAuth, :admin_required}]
              )
  end

  scope "/api/v1", TaskmanWeb.API do
    pipe_through :api

    get "/projects", ProjectController, :index
    post "/projects", ProjectController, :create
    get "/projects/:project_id", ProjectController, :show
    get "/projects/:project_id/lists", ListController, :index
    post "/projects/:project_id/lists", ListController, :create
    get "/projects/:project_id/lists/:list_id", ListController, :show
    patch "/projects/:project_id/lists/:list_id", ListController, :update
    get "/projects/:project_id/tasks", TaskController, :index
    post "/projects/:project_id/tasks", TaskController, :create
    get "/projects/:project_id/tasks/:task_id", TaskController, :show
    get "/projects/:project_id/tasks/:task_id/hierarchy", TaskController, :hierarchy
    patch "/projects/:project_id/tasks/:task_id", TaskController, :update
    post "/projects/:project_id/tasks/:task_id/move", TaskController, :move
  end

  # Other scopes may use custom stacks.
  # scope "/api", TaskmanWeb do
  #   pipe_through :api
  # end

  # Enable LiveDashboard and Swoosh mailbox preview in development
  if Application.compile_env(:taskman, :dev_routes) do
    # If you want to use the LiveDashboard in production, you should put
    # it behind authentication and allow only admins to access it.
    # If your application does not have an admins-only section yet,
    # you can use Plug.BasicAuth to set up some basic authentication
    # as long as you are also using SSL (which you should anyway).
    import Phoenix.LiveDashboard.Router

    scope "/dev" do
      pipe_through :browser

      live_dashboard "/dashboard", metrics: TaskmanWeb.Telemetry
      forward "/mailbox", Plug.Swoosh.MailboxPreview
    end
  end
end
