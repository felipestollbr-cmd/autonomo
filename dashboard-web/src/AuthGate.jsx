import React, { useEffect, useState } from "react";
import { getCurrentUser, signOut } from "aws-amplify/auth";
import { authConfigured } from "./auth.js";
import Login from "./Login.jsx";

export const SignOutContext = React.createContext(() => {});

export default function AuthGate({ children }) {
  const [status, setStatus] = useState("checking"); // checking | signedOut | signedIn

  useEffect(() => {
    if (!authConfigured) {
      setStatus("signedIn"); // fail open only if login isn't configured at all (e.g. local dev)
      return;
    }
    getCurrentUser()
      .then(() => setStatus("signedIn"))
      .catch(() => setStatus("signedOut"));
  }, []);

  if (status === "checking") return null;

  if (status === "signedOut") {
    return <Login onSignedIn={() => setStatus("signedIn")} />;
  }

  const doSignOut = async () => {
    await signOut();
    setStatus("signedOut");
  };

  return <SignOutContext.Provider value={doSignOut}>{children}</SignOutContext.Provider>;
}
