export const getLocalSessionId = (): string => {
  if (typeof window === "undefined") return "server-session";
  const key = "pizza-agent-session-id";
  const existing = window.localStorage.getItem(key);
  if (existing) return existing;
  const created = `web-${crypto.randomUUID()}`;
  window.localStorage.setItem(key, created);
  return created;
};

export const getLocalUserId = (): string => {
  if (typeof window === "undefined") return "server-user";
  const key = "pizza-agent-user-id";
  const existing = window.localStorage.getItem(key);
  if (existing) return existing;
  const created = `user-${crypto.randomUUID()}`;
  window.localStorage.setItem(key, created);
  return created;
};
