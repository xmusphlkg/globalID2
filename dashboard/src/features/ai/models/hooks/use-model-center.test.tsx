import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FormEvent } from "react";
import type { AIModelItem, AIProviderItem } from "@/features/ai/api";
import { useModelCenter } from "./use-model-center";

const state = vi.hoisted(() => ({ providers: [] as AIProviderItem[], models: [] as AIModelItem[], createProvider: { mutate: vi.fn() }, updateProvider: { mutate: vi.fn() }, createModel: { mutate: vi.fn() }, updateModel: { mutate: vi.fn() }, deleteModel: { mutate: vi.fn() } }));
vi.mock("@/stores/app-store", () => ({ useAppStore: () => ({ lang: "en" }) }));
vi.mock("@/features/ai/api", () => ({
  useAIProviders: () => ({ data: state.providers }), useAIModels: () => ({ data: state.models }), useAIRuntimeRoutes: () => ({ data: [] }), useAIModelCallLogs: () => ({ data: undefined }),
  useCreateAIProvider: () => state.createProvider, useUpdateAIProvider: () => state.updateProvider,
  useCreateAIModel: () => state.createModel, useUpdateAIModel: () => state.updateModel, useDeleteAIModel: () => state.deleteModel,
  useDeleteAIProvider: () => ({}), useTestAIProvider: () => ({}), useDiscoverAIProviderModels: () => ({}), useRebuildAIProviders: () => ({}), useTestAIModel: () => ({}), useCheckAllAIModels: () => ({}),
}));
beforeEach(() => {
  vi.clearAllMocks();
  state.providers = [{ id: 1, provider_key: "provider-one", display_name: "Provider One", provider_name: "openai", priority: 100, is_active: true } as AIProviderItem];
  state.models = [];
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const submit = () => ({ preventDefault: vi.fn() }) as unknown as FormEvent<HTMLFormElement>;

describe("model center editing", () => {
  it("preserves credential semantics and selected provider while query data refreshes", () => {
    const { result, rerender } = renderHook(useModelCenter);
    act(() => result.current.openEditProvider(state.providers[0]));
    act(() => result.current.setProviderForm(form => ({ ...form, display_name: " Updated ", is_active: false })));
    state.providers = [...state.providers]; rerender();
    expect(result.current.editingProviderId).toBe("provider-one");
    act(() => result.current.submitProvider(submit()));
    expect(state.updateProvider.mutate).toHaveBeenCalledWith({ providerKey: "provider-one", payload: { display_name: "Updated", api_style: "openai_compatible", base_url: null, organization: null, priority: 100, is_active: false, clear_api_key: false } }, expect.any(Object));
  });
  it("keeps zero temperature and explicit disabled model state in update payloads", () => {
    const { result } = renderHook(useModelCenter);
    act(() => result.current.openEditModel({ provider_id: 1, model_key: "model-one", model_name: "m", display_name: "Model", temperature: 0, runtime_max_concurrency: 1, priority: 100, is_enabled: false, is_default: false } as AIModelItem));
    act(() => result.current.submitModel(submit()));
    expect(state.updateModel.mutate).toHaveBeenCalledWith({ modelKey: "model-one", payload: expect.objectContaining({ temperature: 0, runtime_max_concurrency: 1, is_enabled: false, availability_timezone: "Asia/Shanghai", availability_windows: [] }) }, expect.any(Object));
    expect(state.createModel.mutate).not.toHaveBeenCalled();
  });
  it("uses the selected provider key when creating and requires delete confirmation", () => {
    const { result } = renderHook(useModelCenter);
    act(() => result.current.openCreateModel());
    act(() => result.current.setModelForm(form => ({ ...form, model_name: " new-model " })));
    act(() => result.current.submitModel(submit()));
    expect(state.createModel.mutate).toHaveBeenCalledWith(expect.objectContaining({ provider_key: "provider-one", model_name: "new-model", display_name: "new-model" }), expect.any(Object));
    const model = { model_key: "model-one", display_name: "Model", is_default: true } as AIModelItem;
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    act(() => result.current.onDeleteModel(model));
    expect(state.deleteModel.mutate).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    act(() => result.current.onDeleteModel(model));
    expect(state.deleteModel.mutate).toHaveBeenCalledWith("model-one", expect.objectContaining({ onSettled: expect.any(Function) }));
  });
});
