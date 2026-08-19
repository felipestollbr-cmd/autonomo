import React, { useState } from "react";
import { signIn, confirmSignIn } from "aws-amplify/auth";

export default function Login({ onSignedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [needsNewPassword, setNeedsNewPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSignIn(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const result = await signIn({ username: email, password });
      if (result.nextStep?.signInStep === "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED") {
        setNeedsNewPassword(true);
      } else if (result.isSignedIn) {
        onSignedIn();
      }
    } catch (err) {
      setError(err.message || "Falha ao entrar");
    } finally {
      setBusy(false);
    }
  }

  async function handleNewPassword(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const result = await confirmSignIn({ challengeResponse: newPassword });
      if (result.isSignedIn) onSignedIn();
    } catch (err) {
      setError(err.message || "Falha ao definir senha");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-box" onSubmit={needsNewPassword ? handleNewPassword : handleSignIn}>
        <h1>Autonomo</h1>
        <div className="sub" style={{ marginBottom: 20 }}>
          {needsNewPassword ? "Defina sua senha definitiva" : "Entrar no painel"}
        </div>

        {!needsNewPassword ? (
          <>
            <input
              type="email"
              placeholder="E-mail"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
            />
            <input
              type="password"
              placeholder="Senha"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </>
        ) : (
          <input
            type="password"
            placeholder="Nova senha (mín. 10 caracteres)"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            autoComplete="new-password"
            minLength={10}
            required
          />
        )}

        {error && <div className="error">{error}</div>}

        <button type="submit" disabled={busy}>
          {busy ? "Aguarde…" : needsNewPassword ? "Salvar senha e entrar" : "Entrar"}
        </button>
      </form>
    </div>
  );
}
