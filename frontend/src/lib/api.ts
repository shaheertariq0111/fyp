const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

if (!apiBaseUrl) {
  throw new Error("NEXT_PUBLIC_API_BASE_URL is required");
}

export const apiUrl = (path: string): string =>
  `${apiBaseUrl.replace(/\/$/, "")}/${path.replace(/^\//, "")}`;
