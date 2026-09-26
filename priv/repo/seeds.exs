alias AshAuthentication.Argon2Provider
alias Taskman.Accounts
alias Taskman.Accounts.User
alias Taskman.Lists
alias Taskman.Lists.TaskList
alias Taskman.Projects
alias Taskman.Projects.Project
alias Taskman.Repo
alias Taskman.Tasks
alias Taskman.Tasks.Task

if Application.get_env(:taskman, :seed_development_users, Mix.env() == :dev) do
  seed_user = fn email, admin? ->
    if Repo.get_by(User, email: email) == nil do
      {:ok, hashed_password} = Argon2Provider.hash("taskman-dev")

      {:ok, _user} =
        Accounts.bootstrap_user(
          %{
            admin?: admin?,
            confirmed_at: DateTime.utc_now(),
            email: email,
            hashed_password: hashed_password,
            status: :active
          },
          actor: %{accounts_bootstrap?: true}
        )
    end
  end

  seed_user.("admin@taskman.dev", true)
  seed_user.("user@taskman.dev", false)
end

{:ok, %{projects: project_count, lists: list_count, tasks: task_count}} =
  Repo.transaction(fn ->
    Repo.delete_all(Task)
    Repo.delete_all(TaskList)
    Repo.delete_all(Project)

    {:ok, project} = Projects.create_project(%{name: "Taskman Demo"})

    {:ok, _launch_project} =
      Projects.create_project(%{
        name: "Launch Planning",
        description: "Coordinate upcoming releases.",
        icon: "rocket-launch",
        color: "#F97316"
      })

    {:ok, _lab_project} =
      Projects.create_project(%{
        name: "Lab Notes",
        description: "Capture experiments and findings.",
        icon: "beaker",
        color: "#06B6D4"
      })

    {:ok, workstreams} = Lists.create_list(project, nil, %{name: "Workstreams"})
    {:ok, product} = Lists.create_list(project, workstreams, %{name: "Product"})
    {:ok, research} = Lists.create_list(project, product, %{name: "Research"})
    {:ok, engineering} = Lists.create_list(project, workstreams, %{name: "Engineering"})
    {:ok, backend} = Lists.create_list(project, engineering, %{name: "Backend"})
    {:ok, operations} = Lists.create_list(project, nil, %{name: "Operations"})
    {:ok, releases} = Lists.create_list(project, operations, %{name: "Releases"})

    locations = %{
      none: product,
      low: research,
      medium: engineering,
      high: backend,
      urgent: releases
    }

    task_specs = [
      {:icebox, nil},
      {:pending, :icebox},
      {:in_progress, :pending},
      {:in_review, :in_progress},
      {:done, :icebox},
      {:will_not_do, nil}
    ]

    task_count =
      Enum.reduce([:none, :low, :medium, :high, :urgent], 0, fn priority, count ->
        location = Map.fetch!(locations, priority)

        priority_label =
          priority
          |> Atom.to_string()
          |> String.capitalize()

        Enum.reduce(task_specs, %{}, fn {status, parent_status}, tasks_by_status ->
          status_label =
            status
            |> Atom.to_string()
            |> String.replace("_", " ")
            |> String.capitalize()

          parent = Map.get(tasks_by_status, parent_status)

          {:ok, task} =
            Tasks.create_task(
              project,
              location,
              %{
                title: "#{priority_label} priority · #{status_label}",
                description:
                  "Seeded example covering the #{priority} priority and #{status} status.",
                status: status,
                priority: priority
              },
              parent: parent
            )

          Map.put(tasks_by_status, status, task)
        end)

        count + length(task_specs)
      end)

    %{projects: 3, lists: map_size(locations) + 2, tasks: task_count}
  end)

IO.puts("Seeded #{project_count} Projects with #{list_count} lists and #{task_count} tasks.")
