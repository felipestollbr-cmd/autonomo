import React, { useState } from "react";
import { signIn, confirmSignIn, resetPassword, confirmResetPassword } from "aws-amplify/auth";

export default function Login({ onSignedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [code, setCode] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [mode, setMode] = useState("signIn"); // signIn | newPasswordRequired | forgotRequest | forgotConfirm
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSignIn(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const result = await signIn({ username: email, password });
      if (result.nextStep?.signInStep === "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED") {
        setMode("newPasswordRequired");
      } else if (result.nextStep?.signInStep === "RESET_PASSWORD") {
        await resetPassword({ username: email });
        setInfo("Enviamos um código de verificação para seu e-mail. Digite o código e a nova senha abaixo.");
        setMode("forgotConfirm");
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

  async function handleForgotRequest(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await resetPassword({ username: email });
      setInfo("Enviamos um código de verificação para seu e-mail. Digite o código e a nova senha abaixo.");
      setMode("forgotConfirm");
    } catch (err) {
      setError(err.message || "Não foi possível enviar o código");
    } finally {
      setBusy(false);
    }
  }

  async function handleForgotConfirm(e) {
    e.preventDefault();
    setError("");
    if (newPassword !== confirmNewPassword) {
      setError("As senhas não coincidem");
      return;
    }
    setBusy(true);
    try {
      await confirmResetPassword({ username: email, confirmationCode: code, newPassword });
      setInfo("Senha alterada com sucesso. Entre com sua nova senha.");
      setPassword("");
      setNewPassword("");
      setConfirmNewPassword("");
      setCode("");
      setMode("signIn");
    } catch (err) {
      setError(err.message || "Não foi possível confirmar o código");
    } finally {
      setBusy(false);
    }
  }

  const title =
    mode === "newPasswordRequired"
      ? "Defina sua senha definitiva"
      : mode === "forgotRequest"
      ? "Recuperar senha"
      : mode === "forgotConfirm"
      ? "Digite o código e a nova senha"
      : "Entrar no painel";

  const onSubmit =
    mode === "newPasswordRequired"
      ? handleNewPassword
      : mode === "forgotRequest"
      ? handleForgotRequest
      : mode === "forgotConfirm"
      ? handleForgotConfirm
      : handleSignIn;

  return (
    <div className="login-page">
      <form className="login-box" onSubmit={onSubmit}>
        <h1>Autonomo</h1>
        <div className="sub" style={{ marginBottom: 20 }}>
          {title}
        </div>

        {mode === "signIn" && (
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
        )}

        {mode === "newPasswordRequired" && (
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

        {mode === "forgotRequest" && (
          <input
            type="email"
            placeholder="E-mail"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
          />
        )}

        {mode === "forgotConfirm" && (
          <>
            <input
              type="text"
              placeholder="Código recebido por e-mail"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoComplete="one-time-code"
              required
            />
            <input
              type="password"
              placeholder="Nova senha (mín. 10 caracteres)"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              minLength={10}
              required
            />
            <input
              type="password"
              placeholder="Confirme a nova senha"
              value={confirmNewPassword}
              onChange={(e) => setConfirmNewPassword(e.target.value)}
              autoComplete="new-password"
              minLength={10}
              required
            />
          </>
        )}

        {info && !error && <div className="info">{info}</div>}
        {error && <div className="error">{error}</div>}

        <button type="submit" disabled={busy}>
          {busy
            ? "Aguarde…"
            : mode === "newPasswordRequired"
            ? "Salvar senha e entrar"
            : mode === "forgotRequest"
            ? "Enviar código"
            : mode === "forgotConfirm"
            ? "Confirmar nova senha"
            : "Entrar"}
        </button>

        {mode === "signIn" && (
          <button
            type="button"
            className="link-button"
            onClick={() => {
              setError("");
              setInfo("");
              setMode("forgotRequest");
            }}
          >
            Esqueci minha senha
          </button>
        )}

        {(mode === "forgotRequest" || mode === "forgotConfirm") && (
          <button
            type="button"
            className="link-button"
            onClick={() => {
              setError("");
              setInfo("");
              setMode("signIn");
            }}
          >
            Voltar para o login
          </button>
        )}
      </form>
    </div>
  );
}
