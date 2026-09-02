defmodule TaskmanWeb.AuthOverrides do
  @moduledoc "Taskman presentation overrides for AshAuthenticationPhoenix pages."

  use AshAuthentication.Phoenix.Overrides

  alias AshAuthentication.Phoenix.{Components, ConfirmLive, ResetLive, SignInLive, SignOutLive}

  override SignInLive do
    set :root_class, "grid min-h-screen place-items-center bg-slate-950 px-6 py-12 text-slate-100"
  end

  override SignOutLive do
    set :root_class, "grid min-h-screen place-items-center bg-slate-950 px-6 py-12 text-slate-100"
  end

  override ResetLive do
    set :root_class, "grid min-h-screen place-items-center bg-slate-950 px-6 py-12 text-slate-100"
  end

  override ConfirmLive do
    set :root_class, "grid min-h-screen place-items-center bg-slate-950 px-6 py-12 text-slate-100"
  end

  override Components.SignIn do
    set :root_class,
        "w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-8 shadow-2xl shadow-black/30"
  end

  override Components.SignOut do
    set :root_class,
        "w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-8 shadow-2xl shadow-black/30"
  end

  override Components.Reset do
    set :root_class,
        "w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-8 shadow-2xl shadow-black/30"
  end

  override Components.Confirm do
    set :root_class,
        "w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-8 shadow-2xl shadow-black/30"
  end

  override Components.Password do
    set :register_toggle_text, nil
  end
end
