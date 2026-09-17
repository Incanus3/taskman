defmodule TaskmanWeb.PasswordResetRequestForm do
  use TaskmanWeb, :live_component

  @impl true
  def update(assigns, socket) do
    {:ok,
     socket
     |> assign(assigns)
     |> assign(:form, to_form(%{"email" => ""}, as: :user))
     |> assign_new(:inner_block, fn -> [] end)}
  end

  @impl true
  def render(assigns) do
    ~H"""
    <div>
      <.form
        for={@form}
        id="password-reset-request-form"
        action={~p"/auth/user/password/reset_request"}
        method="post"
      >
        <.input
          field={@form[:email]}
          type="email"
          label="Email"
          autocomplete="email"
          required
        />
        <button id="password-reset-request-submit" type="submit">Send reset link</button>
      </.form>
      {render_slot(@inner_block, @form)}
    </div>
    """
  end
end
