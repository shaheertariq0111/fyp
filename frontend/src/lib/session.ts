const sessionKey = "pizza-agent-session-id";
const userKey = "pizza-agent-user-id";

export const createLocalSessionId = (): string => `web-${crypto.randomUUID()}`;

export const getLocalSessionId = (): string => {
  if (typeof window === "undefined") return "server-session";
  const existing = window.localStorage.getItem(sessionKey);
  if (existing) return existing;
  const created = createLocalSessionId();
  window.localStorage.setItem(sessionKey, created);
  return created;
};

export const resetLocalSessionId = (): string => {
  if (typeof window === "undefined") return "server-session";
  const created = createLocalSessionId();
  window.localStorage.setItem(sessionKey, created);
  return created;
};

export const getLocalUserId = (): string => {
  if (typeof window === "undefined") return "server-user";
  const existing = window.localStorage.getItem(userKey);
  if (existing) return existing;
  const created = `user-${crypto.randomUUID()}`;
  window.localStorage.setItem(userKey, created);
  return created;
};
