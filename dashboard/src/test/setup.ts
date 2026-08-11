import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { setupServer } from "msw/node";

export const apiMockServer = setupServer();

beforeAll(() => apiMockServer.listen({ onUnhandledRequest: "error" }));
afterEach(() => apiMockServer.resetHandlers());
afterAll(() => apiMockServer.close());
