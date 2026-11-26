import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FormEvent } from "react";
import type { AIModelItem, AIProviderItem } from "@/features/ai/api";
import { DiscoveryPanel } from "../components/discovery-panel";
import { ModelForm } from "../components/model-form";
import { useModelCenter } from "./use-model-center";

const state = vi.hoisted(() => ({
  providers: [] as AIProviderItem[],
  models: [] as AIModelItem[],
  createProvider: { mutate: vi.fn() },
  updateProvider: { mutate: vi.fn() },
  createModel: { mutate: vi.fn(), mutateAsync: vi.fn() },
  updateModel: { mutate: vi.fn(), mutateAsync: vi.fn() },
  deleteModel: { mutate: vi.fn() },
  testModel: { mutateAsync: vi.fn() },
  discoverProviderModels: { mutate: vi.fn() },
}));
vi.mock("@/stores/app-store", () => ({ useAppStore: () => ({ lang: "en" }) }));
vi.mock("@/features/ai/api", () => ({
  useAIProviders: () => ({ data: state.providers }), useAIModels: () => ({ data: state.models }), useAIRuntimeRoutes: () => ({ data: [] }), useAIModelCallLogs: () => ({ data: undefined }),
  useCreateAIProvider: () => state.createProvider, useUpdateAIProvider: () => state.updateProvider,
  useCreateAIModel: () => state.createModel, useUpdateAIModel: () => state.updateModel, useDeleteAIModel: () => state.deleteModel,
  useDeleteAIProvider: () => ({}), useTestAIProvider: () => ({}), useDiscoverAIProviderModels: () => state.discoverProviderModels, useRebuildAIProviders: () => ({}), useTestAIModel: () => state.testModel, useCheckAllAIModels: () => ({}),
}));
beforeEach(() => {
  vi.clearAllMocks();
  state.providers = [{ id: 1, provider_key: "provider-one", display_name: "Provider One", provider_name: "openai", priority: 100, is_active: true } as AIProviderItem];
  state.models = [];
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const submit = () => ({ preventDefault: vi.fn() }) as unknown as FormEvent<HTMLFormElement>;

function setupDisabledModel() {
  const provider = { ...state.providers[0], id: 2, provider_key: "sub2api", display_name: "Sub2API" };
  const modelName = "gpt-6.1-terra";
  const disabledModel = {
    provider_id: provider.id, provider_key: provider.provider_key, model_key: "legacy-sub2api-terra", model_name: modelName, display_name: "Terra",
    is_enabled: false, runtime_max_concurrency: 4,
  } as AIModelItem;
  state.providers.push(provider);
  state.models = [
    { ...disabledModel, provider_id: 1, provider_key: "provider-one", model_key: "provider-one:terra", is_enabled: true },
    disabledModel,
  ];
  return { provider, modelName, disabledModel };
}

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

  it("re-enables the selected provider's existing model when manually added", () => {
    const { provider, modelName, disabledModel } = setupDisabledModel();
    const { result } = renderHook(useModelCenter);
    act(() => result.current.openCreateModel());
    act(() => result.current.setModelForm(form => ({ ...form, provider_id: provider.id, model_name: ` ${modelName} ` })));
    render(<ModelForm controller={result.current} />);
    fireEvent.click(screen.getByRole("button", { name: "Re-enable" }));
    expect(state.updateModel.mutate).toHaveBeenCalledWith(
      { modelKey: disabledModel.model_key, payload: { is_enabled: true } }, expect.any(Object),
    );
    expect(state.createModel.mutate).not.toHaveBeenCalled();
  });

  it("keeps disabled models out of bulk import and restores only an explicitly selected route", async () => {
    const { provider, modelName, disabledModel } = setupDisabledModel();
    state.discoverProviderModels.mutate.mockImplementationOnce((_, options) => {
      options.onSuccess({ success: true, status: "available", message: "", models: [modelName, "new-model"] });
    });
    state.testModel.mutateAsync.mockResolvedValueOnce({ success: true, status: "available" });
    const { result } = renderHook(useModelCenter);
    act(() => result.current.onDiscoverProviderModels(provider));
    expect(result.current.discoverySelection).toEqual(["new-model"]);
    expect(result.current.selectableDiscoveredModels).toEqual(["new-model"]);
    expect(state.updateModel.mutateAsync).not.toHaveBeenCalled();

    render(<DiscoveryPanel controller={result.current} />);
    expect(screen.getByRole("checkbox", { name: modelName })).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Re-enable and test" }));
    expect(state.updateModel.mutateAsync).toHaveBeenCalledWith({ modelKey: disabledModel.model_key, payload: { is_enabled: true } });
    await waitFor(() => expect(state.testModel.mutateAsync).toHaveBeenCalledWith(disabledModel.model_key));
    expect(state.createModel.mutateAsync).not.toHaveBeenCalled();
  });

  it("allows a hard-deleted provider model to be discovered and created again", () => {
    const { provider, modelName, disabledModel } = setupDisabledModel();
    const { result, rerender } = renderHook(useModelCenter);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    act(() => result.current.onDeleteModel(disabledModel));
    expect(state.deleteModel.mutate).toHaveBeenCalledWith(disabledModel.model_key, expect.any(Object));
    state.models = state.models.filter(model => model.provider_id !== provider.id);
    rerender();

    state.discoverProviderModels.mutate.mockImplementationOnce((_, options) => {
      options.onSuccess({ success: true, status: "available", message: "", models: [modelName] });
    });
    act(() => result.current.onDiscoverProviderModels(provider));
    expect(result.current.selectableDiscoveredModels).toEqual([modelName]);
    act(() => result.current.openCreateModel());
    act(() => result.current.setModelForm(form => ({ ...form, provider_id: provider.id, model_name: modelName })));
    act(() => result.current.submitModel(submit()));
    expect(state.createModel.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ provider_key: provider.provider_key, model_name: modelName }), expect.any(Object),
    );
    expect(state.updateModel.mutate).not.toHaveBeenCalled();
  });
});
