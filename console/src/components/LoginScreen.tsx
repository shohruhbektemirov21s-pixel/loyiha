import { useState, type FormEvent } from "react";
import { ScanLine, Loader2, AlertTriangle } from "lucide-react";
import { login, saveToken } from "../lib/api";
import { IS_MOCK } from "../lib/mock";
import type { AuthState } from "../lib/types";
import {
  APP_TITLE, LOGIN_USERNAME, LOGIN_PASSWORD,
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
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "16px",
        fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
      }}
    >
      <div
        style={{
          width: 392,
          background: "rgba(255,255,255,0.06)",
          backdropFilter: "blur(18px)",
          WebkitBackdropFilter: "blur(18px)",
          border: "1px solid rgba(255,255,255,0.14)",
          borderRadius: 20,
          padding: 34,
          boxShadow: "0 30px 90px rgba(0,0,0,0.55)",
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", gap: 13, marginBottom: 28 }}>
          <div
            style={{
              width: 46,
              height: 46,
              borderRadius: 12,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "linear-gradient(135deg,#14b8a6,#0d9488)",
              boxShadow: "0 8px 22px rgba(20,184,166,0.4)",
            }}
          >
            <ScanLine size={24} color="#062a26" aria-hidden="true" />
          </div>
          <div>
            <div
              style={{
                fontSize: 10.5,
                textTransform: "uppercase",
                letterSpacing: "0.16em",
                color: "#7c8aa3",
                fontWeight: 600,
              }}
            >
              Bojxona · lane-1
            </div>
            <h1
              style={{
                fontSize: 20,
                fontWeight: 700,
                letterSpacing: "-0.02em",
                color: "#e2e8f0",
                margin: 0,
              }}
            >
              {APP_TITLE}
            </h1>
          </div>
        </div>

        <form onSubmit={handleSubmit} noValidate>
          <label
            htmlFor="username"
            style={{
              display: "block",
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: "0.1em",
              color: "#8595ad",
              fontWeight: 600,
              marginBottom: 6,
            }}
          >
            {LOGIN_USERNAME}
          </label>
          <input
            id="username"
            type="text"
            placeholder="admin"
            autoComplete="username"
            required
            aria-invalid={error ? true : undefined}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            style={{
              width: "100%",
              padding: "11px 13px",
              marginBottom: 16,
              borderRadius: 10,
              background: "rgba(0,0,0,0.25)",
              border: "1px solid rgba(255,255,255,0.12)",
              color: "#e2e8f0",
              fontSize: 14,
              boxSizing: "border-box",
            }}
          />

          <label
            htmlFor="password"
            style={{
              display: "block",
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: "0.1em",
              color: "#8595ad",
              fontWeight: 600,
              marginBottom: 6,
            }}
          >
            {LOGIN_PASSWORD}
          </label>
          <input
            id="password"
            type="password"
            placeholder="••••••••"
            autoComplete="current-password"
            required
            aria-invalid={error ? true : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{
              width: "100%",
              padding: "11px 13px",
              borderRadius: 10,
              background: "rgba(0,0,0,0.25)",
              border: "1px solid rgba(255,255,255,0.12)",
              color: "#e2e8f0",
              fontSize: 14,
              boxSizing: "border-box",
            }}
          />

          {error && (
            <div
              role="alert"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginTop: 14,
                padding: "10px 12px",
                borderRadius: 9,
                background: "rgba(239,68,68,0.12)",
                border: "1px solid rgba(239,68,68,0.4)",
                color: "#fca5a5",
                fontSize: 12.5,
              }}
            >
              <AlertTriangle size={16} aria-hidden="true" />
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !username}
            aria-busy={loading}
            style={{
              width: "100%",
              marginTop: 22,
              padding: 13,
              border: "none",
              borderRadius: 11,
              fontSize: 14.5,
              fontWeight: 600,
              cursor: "pointer",
              color: "#062a26",
              background: "linear-gradient(135deg,#2dd4bf,#14b8a6)",
              boxShadow: "0 10px 28px rgba(20,184,166,0.35)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
            }}
          >
            {loading && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
            {LOGIN_SUBMIT}
          </button>
        </form>

        {IS_MOCK && (
          <p
            style={{
              marginTop: 16,
              textAlign: "center",
              fontSize: 11.5,
              color: "#5b6679",
            }}
          >
            Demo: istalgan foydalanuvchi · parol
          </p>
        )}
      </div>
    </div>
  );
}
