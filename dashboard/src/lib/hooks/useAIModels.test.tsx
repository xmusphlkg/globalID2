import type { PropsWithChildren } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { apiFetch } from "@/lib/api";
import { useAIModels, useDeleteAIModel } from "./useAIModels";

vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

const target = { provider_id: 1, model_name: "gpt-6.1-terra", model_key: "sub2api:gpt-6.1-terra" };
const sibling = { ...target, provider_id: 2, model_key: "other:gpt-6.1-terra" };
const request = vi.mocked(apiFetch);

function mountModelList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  client.setQueryData(["ai-models"], [target, sibling]);
  const wrapper = ({ children }: PropsWithChildren) =>
    <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => ({ list: useAIModels(), remove: useDeleteAIModel() }), { wrapper });
  return { result, client };
}

afterEach(() => { cleanup(); request.mockReset(); });

it("evicts only the successfully deleted model while its list refresh is pending", async () => {
  const refresh = Promise.withResolvers<typeof sibling[]>();
  request.mockResolvedValueOnce({ ok: true }).mockReturnValueOnce(refresh.promise);
  const { result, client } = mountModelList();
  await act(() => result.current.remove.mutateAsync(target.model_key));

  expect(request).toHaveBeenNthCalledWith(1, "/ai/models/sub2api%3Agpt-6.1-terra", { method: "DELETE" });
  expect(request).toHaveBeenNthCalledWith(2, "/ai/models");
  expect(client.getQueryState(["ai-models"])?.fetchStatus).toBe("fetching");
  expect(client.getQueryData(["ai-models"])).toEqual([sibling]);
  await waitFor(() => expect(result.current.list.data).toEqual([sibling]));

  refresh.resolve([sibling]);
  await waitFor(() => expect(result.current.list.isFetching).toBe(false));
});

it("retains the model and its sibling when deletion fails", async () => {
  request.mockRejectedValueOnce(new Error("Delete failed"));
  const { result, client } = mountModelList();
  await act(async () => {
    await expect(result.current.remove.mutateAsync(target.model_key)).rejects.toThrow("Delete failed");
  });
  expect(client.getQueryData(["ai-models"])).toEqual([target, sibling]);
  expect(result.current.list.data).toEqual([target, sibling]);
});
