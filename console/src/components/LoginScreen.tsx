import { useState, type FormEvent } from "react";
import { ScanLine, Loader2 } from "lucide-react";
import { login, saveToken } from "../lib/api";
import { IS_MOCK } from "../lib/mock";
import type { AuthState } from "../lib/types";
import {
  APP_TITLE, LOGIN_TITLE, LOGIN_USERNAME, LOGIN_PASSWORD,
  LOGIN_SUBMIT, LOGIN_ERROR,
} from "../lib/uz";

interface Props {
  onLogin: (auth: AuthState) => void;
}

export function LoginScreen({ onLogin }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error,    setError]    = useState<string | null>(null);
  const [loading,  setLoading]  = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    if (IS_MOCK) {
      // Dev shortcut — bypass auth
      onLogin({
        token: "mock-token",
        operatorId: "00000000-0000-0000-0000-000000000001",
        username: username || "operator1",
        role: "operator",
        laneIds: ["1-yo'lak"],
      });
      setLoading(false);
      return;
    }

    try {
      const resp = await login(username, password);
      saveToken(resp.access_token);
      onLogin({
        token:      resp.access_token,
        operatorId: resp.operator_id,
        username:   resp.username,
        role:       resp.role,
        laneIds:    resp.lane_ids,
      });
    } catch {
      setError(LOGIN_ERROR);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative min-h-screen flex items-center justify-center px-4 overflow-hidden">
      <div className="pointer-events-none absolute inset-0 bg-grid-fine opacity-50" aria-hidden="true" />
      <div className="relative z-10 w-full max-w-sm space-y-6 scene">
        {/* Logo */}
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="p-3 rounded-xl bg-blue-900/40 border border-blue-700/60 shadow-glow-blue">
            <ScanLine size={28} className="text-blue-300" aria-hidden="true" />
          </div>
          <h1 className="text-xl font-bold text-content-primary">{APP_TITLE}</h1>
        </div>

        {/* Card */}
        <div className="card-3d glass border border-white/10 rounded-xl2 p-6 space-y-4 shadow-elev-4">
          <h2 className="text-sm font-semibold text-content-secondary">{LOGIN_TITLE}</h2>

          <form onSubmit={handleSubmit} noValidate className="space-y-3">
            <div>
              <label htmlFor="username" className="block text-xs text-content-secondary mb-1">
                {LOGIN_USERNAME}
              </label>
              <input
                id="username"
                type="text"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full bg-surface border border-surface-border rounded-lg px-3 py-2 text-sm text-content-primary placeholder-content-muted focus:outline-none focus:ring-2 focus:ring-blue-600"
              />
            </div>

            <div>
              <label htmlFor="password" className="block text-xs text-content-secondary mb-1">
                {LOGIN_PASSWORD}
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-surface border border-surface-border rounded-lg px-3 py-2 text-sm text-content-primary placeholder-content-muted focus:outline-none focus:ring-2 focus:ring-blue-600"
              />
            </div>

            {error && (
              <p className="text-sm text-red-400 animate-fade-in" role="alert">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading || !username}
              className="press w-full py-2.5 rounded-lg text-sm font-semibold bg-gradient-to-b from-blue-600 to-blue-700 hover:from-blue-500 hover:to-blue-600 disabled:from-surface-border disabled:to-surface-border disabled:text-content-muted text-white shadow-elev-2 hover:shadow-glow-blue transition-all flex items-center justify-center gap-2"
              aria-busy={loading}
            >
              {loading && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
              {LOGIN_SUBMIT}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-content-muted">
          Ushbu tizim faqat vakolatli xodimlar uchun.
        </p>
      </div>
    </div>
  );
}
