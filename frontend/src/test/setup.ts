import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeEach, vi } from "vitest";

const originalApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
const originalBranchId = process.env.NEXT_PUBLIC_BRANCH_ID;

function restoreEnvironmentValue(
  name: "NEXT_PUBLIC_API_BASE_URL" | "NEXT_PUBLIC_BRANCH_ID",
  value: string | undefined,
) {
  if (value === undefined) {
    delete process.env[name];
    return;
  }
  process.env[name] = value;
}

function applySyntheticEnvironment() {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.test.invalid";
  process.env.NEXT_PUBLIC_BRANCH_ID = "test";
}

applySyntheticEnvironment();

beforeEach(() => {
  applySyntheticEnvironment();
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(
    new Error("Unexpected test network request. Stub fetch explicitly."),
  ));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

afterAll(() => {
  restoreEnvironmentValue("NEXT_PUBLIC_API_BASE_URL", originalApiBaseUrl);
  restoreEnvironmentValue("NEXT_PUBLIC_BRANCH_ID", originalBranchId);
});
