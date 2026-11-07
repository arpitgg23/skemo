import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api } from "../api";
import { Banner } from "../components/Feedback";
import { usePageTitle } from "../hooks";

/** The public demo's login, as "user / password", set at build time.
 * Only the hosted demo sets VITE_DEMO_LOGIN; your own site shows nothing. */
const demo = (import.meta.env.VITE_DEMO_LOGIN ?? "").split("/").map((part) => part.trim());
const demoLogin = demo.length === 2 && demo[0] && demo[1] ? { user: demo[0], password: demo[1] } : null;

/** Shown instead of the desk whenever nobody is signed in. */
export function Login() {
  usePageTitle("Sign in");
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");

  const signIn = useMutation({
    mutationFn: () => api.login(user.trim(), password),
    onSuccess: () => {
      setPassword("");
      // Reload rather than swap the session in place: the cookie is set, so the app comes
      // back as this user with nothing cached from before, and the URL is kept.
      window.location.reload();
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (user.trim() && password && !signIn.isPending) signIn.mutate();
  }

  return (
    <div className="login">
      <form className="panel" onSubmit={submit}>
        <h1>
          Skemo <span className="muted">desk</span>
        </h1>
        {signIn.error && <Banner>{signIn.error.message}</Banner>}
        <div className="field">
          <label htmlFor="login-user">Login</label>
          <input
            id="login-user"
            autoComplete="username"
            autoFocus
            placeholder="you@example.com"
            value={user}
            onChange={(event) => setUser(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        <button
          type="submit"
          className="primary"
          disabled={signIn.isPending || !user.trim() || !password}
        >
          {signIn.isPending ? "Signing in…" : "Sign in"}
        </button>
        {demoLogin && (
          <p className="demo-hint">
            This is a public demo.{" "}
            <button
              type="button"
              className="link"
              onClick={() => {
                setUser(demoLogin.user);
                setPassword(demoLogin.password);
              }}
            >
              Use the demo login
            </button>{" "}
            <span className="muted">
              ({demoLogin.user} / {demoLogin.password}) — anyone can change the data here.
            </span>
          </p>
        )}
      </form>
    </div>
  );
}
