import { Amplify } from "aws-amplify";

const USER_POOL_ID = import.meta.env.VITE_COGNITO_USER_POOL_ID || "";
const USER_POOL_CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID || "";

Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: USER_POOL_ID,
      userPoolClientId: USER_POOL_CLIENT_ID,
    },
  },
});

export const authConfigured = Boolean(USER_POOL_ID && USER_POOL_CLIENT_ID);
