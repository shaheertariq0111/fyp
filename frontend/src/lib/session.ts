const sessionKey = "pizza-agent-session-id";
const customerKey = "pizza-agent-customer-id";

export const createLocalSessionId = (): string => `web-${crypto.randomUUID()}`;
export const createLocalCustomerId = (): string => `cust-${crypto.randomUUID()}`;

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

export const saveLocalSessionId = (sessionId: string): void => {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(sessionKey, sessionId);
};

export const getLocalCustomerId = (): string => {
  if (typeof window === "undefined") return "server-user";
  const existing = window.localStorage.getItem(customerKey);
  if (existing) return existing;
  const created = createLocalCustomerId();
  window.localStorage.setItem(customerKey, created);
  return created;
};

export const saveLocalCustomerId = (customerId: string): void => {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(customerKey, customerId);
};

export const getLocalUserId = getLocalCustomerId;
