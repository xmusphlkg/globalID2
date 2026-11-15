
import { useMemo, useState, type FormEvent } from "react";

import { useAppStore } from "@/stores/app-store";

import { type AIModelItem, type AIProviderItem, type ProviderModelDiscoveryResult, type ThinkingMode, useAIModels, useAIModelCallLogs, useAIProviders, useAIRuntimeRoutes, useCheckAllAIModels, useCreateAIModel, useCreateAIProvider, useDeleteAIModel, useDeleteAIProvider, useDiscoverAIProviderModels, useRebuildAIProviders, useTestAIModel, useTestAIProvider, useUpdateAIModel, useUpdateAIProvider } from "@/features/ai/api";

import { matchesQuery, providerMatchesFilter, modelMatchesFilter, routeMatchesFilter, type HealthFilter } from "../filters";
import { mutationErrorText } from "../presentation";
import { modelCenterCopy } from "../messages";
type DrawerMode = "create" | "edit" | null;
type DiscoveryImportResult = { status: string; message: string };
type WorkspaceView = "providers" | "models" | "runtime" | "logs";

export function useModelCenter() {
  const { lang } = useAppStore();
  const isZh = lang === "zh";

  const { data: providers, isLoading: loadingProviders } = useAIProviders();
  const { data: models, isLoading: loadingModels } = useAIModels();
  const { data: runtimeRoutes, isLoading: loadingRuntimeRoutes, error: runtimeRoutesError } = useAIRuntimeRoutes();

  const createProvider = useCreateAIProvider();
  const updateProvider = useUpdateAIProvider();
  const deleteProvider = useDeleteAIProvider();
  const testProvider = useTestAIProvider();
  const discoverProviderModels = useDiscoverAIProviderModels();
  const rebuildProviders = useRebuildAIProviders();

  const createModel = useCreateAIModel();
  const updateModel = useUpdateAIModel();
  const testModel = useTestAIModel();
  const deleteModel = useDeleteAIModel();
  const checkAll = useCheckAllAIModels();

  const [providerDrawerMode, setProviderDrawerMode] = useState<DrawerMode>(null);
  const [modelDrawerMode, setModelDrawerMode] = useState<DrawerMode>(null);
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null);
  const [editingModelId, setEditingModelId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [healthFilter, setHealthFilter] = useState<HealthFilter>("all");
  const [activeView, setActiveView] = useState<WorkspaceView>("providers");
  const [callLogProvider, setCallLogProvider] = useState("");
  const [callLogStatus, setCallLogStatus] = useState("");
  const [callLogPageNumber, setCallLogPageNumber] = useState(1);
  const { data: callLogPage, isLoading: loadingCallLogs, error: callLogsError } = useAIModelCallLogs(activeView === "logs", {
    page: callLogPageNumber,
    providerKey: callLogProvider,
    status: callLogStatus,
  });
  const callLogs = callLogPage?.items;
  const callLogTotalPages = Math.max(1, callLogPage?.totalPages ?? 1);
  const [deletingProviderId, setDeletingProviderId] = useState<string | null>(null);
  const [deletingModelId, setDeletingModelId] = useState<string | null>(null);
  const [discoveryResult, setDiscoveryResult] = useState<{ provider: AIProviderItem; result: ProviderModelDiscoveryResult } | null>(null);
  const [discoverySelection, setDiscoverySelection] = useState<string[]>([]);
  const [discoveryImportResults, setDiscoveryImportResults] = useState<Record<string, DiscoveryImportResult>>({});
  const [isImportingDiscoveredModels, setIsImportingDiscoveredModels] = useState(false);

  const [providerForm, setProviderForm] = useState({
    provider_key: "",
    provider_name: "openai",
    display_name: "",
    api_style: "openai_compatible",
    base_url: "",
    organization: "",
    priority: 100,
    api_key: "",
    clear_api_key: false,
    is_active: true,
  });

  const [modelForm, setModelForm] = useState({
    provider_id: 0,
    model_name: "",
    display_name: "",
    model_type: "chat",
    api_style: "",
    temperature: "",
    runtime_max_concurrency: "",
    thinking_mode: "default" as ThinkingMode,
    availability_timezone: "Asia/Shanghai",
    availability_windows: [] as { start: string; end: string }[],
    priority: 100,
    is_enabled: true,
    is_default: false,
  });

  const copy = modelCenterCopy(isZh);

  const modelCountByProvider = useMemo(() => {
    const counter = new Map<number, number>();
    (models ?? []).forEach((model) => counter.set(model.provider_id, (counter.get(model.provider_id) ?? 0) + 1));
    return counter;
  }, [models]);

  const providerOptions = useMemo(
    () =>
      (providers ?? [])
        .slice()
        .sort((a, b) => a.priority - b.priority || a.display_name.localeCompare(b.display_name))
        .map((provider) => ({ id: provider.id, label: `${provider.display_name} (${provider.provider_key})` })),
    [providers],
  );

  const summary = useMemo(() => {
    const totalProviders = providers?.length ?? 0;
    const activeProviders = (providers ?? []).filter((provider) => provider.is_active).length;
    const configuredKeys = (providers ?? []).filter((provider) => provider.has_api_key).length;
    const enabledModels = (models ?? []).filter((model) => model.is_enabled).length;
    const defaultModels = (models ?? []).filter((model) => model.is_default).length;
    const runtimeReady = (runtimeRoutes ?? []).filter((route) => route.available_for_routing).length;
    const totalRoutes = runtimeRoutes?.length ?? 0;
    return {
      totalProviders,
      activeProviders,
      configuredKeys,
      enabledModels,
      defaultModels,
      totalRoutes,
      runtimeReady,
      routeCoverage: totalRoutes > 0 ? Math.round((runtimeReady / totalRoutes) * 100) : 0,
    };
  }, [models, providers, runtimeRoutes]);

  const runtimeRouteByModelKey = useMemo(
    () => new Map((runtimeRoutes ?? []).map((route) => [route.model_key, route])),
    [runtimeRoutes],
  );

  const searchQuery = search.trim().toLowerCase();
  const filteredProviders = useMemo(
    () =>
      (providers ?? [])
        .filter((provider) => matchesQuery([provider.display_name, provider.provider_key, provider.provider_name, provider.base_url, provider.api_style], searchQuery))
        .filter((provider) => providerMatchesFilter(provider, healthFilter))
        .sort((a, b) => Number(b.is_active) - Number(a.is_active) || a.priority - b.priority || a.display_name.localeCompare(b.display_name)),
    [healthFilter, providers, searchQuery],
  );

  const filteredModels = useMemo(
    () =>
      (models ?? [])
        .filter((model) => matchesQuery([model.display_name, model.model_name, model.model_key, model.provider_key, model.provider_name, model.api_style], searchQuery))
        .filter((model) => modelMatchesFilter(model, healthFilter, runtimeRouteByModelKey.get(model.model_key)))
        .sort((a, b) => Number(b.is_default) - Number(a.is_default) || Number(b.is_enabled) - Number(a.is_enabled) || a.priority - b.priority || a.display_name.localeCompare(b.display_name)),
    [healthFilter, models, runtimeRouteByModelKey, searchQuery],
  );

  const filteredRuntimeRoutes = useMemo(
    () =>
      (runtimeRoutes ?? [])
        .filter((route) => matchesQuery([route.model_name, route.model_key, route.provider_key, route.provider_name, route.api_style, route.base_url], searchQuery))
        .filter((route) => routeMatchesFilter(route, healthFilter))
        .sort((a, b) => (a.priority ?? Number.MAX_SAFE_INTEGER) - (b.priority ?? Number.MAX_SAFE_INTEGER)),
    [healthFilter, runtimeRoutes, searchQuery],
  );

  const configuredDiscoveredModels = useMemo(
    () => new Set(
      discoveryResult
        ? (models ?? []).filter((model) => model.provider_id === discoveryResult.provider.id).map((model) => model.model_name)
        : [],
    ),
    [discoveryResult, models],
  );

  const selectableDiscoveredModels = useMemo(
    () => discoveryResult?.result.models.filter((model) => !configuredDiscoveredModels.has(model) && !discoveryImportResults[model]) ?? [],
    [configuredDiscoveredModels, discoveryImportResults, discoveryResult],
  );

  const operationError =
    createProvider.error ??
    updateProvider.error ??
    deleteProvider.error ??
    rebuildProviders.error ??
    testProvider.error ??
    discoverProviderModels.error ??
    createModel.error ??
    updateModel.error ??
    testModel.error ??
    checkAll.error;

  const resetProviderForm = () => {
    setProviderForm({
      provider_key: "",
      provider_name: "openai",
      display_name: "",
      api_style: "openai_compatible",
      base_url: "",
      organization: "",
      priority: 100,
      api_key: "",
      clear_api_key: false,
      is_active: true,
    });
    setEditingProviderId(null);
  };

  const resetModelForm = () => {
    setModelForm({
      provider_id: providerOptions[0]?.id ?? 0,
      model_name: "",
      display_name: "",
      model_type: "chat",
      api_style: "",
      temperature: "",
      runtime_max_concurrency: "",
      thinking_mode: "default",
      availability_timezone: "Asia/Shanghai",
      availability_windows: [],
      priority: 100,
      is_enabled: true,
      is_default: false,
    });
    setEditingModelId(null);
  };

  const openCreateProvider = () => {
    resetProviderForm();
    setProviderDrawerMode("create");
  };

  const openEditProvider = (provider: AIProviderItem) => {
    setEditingProviderId(provider.provider_key);
    setProviderForm({
      provider_key: provider.provider_key,
      provider_name: provider.provider_name,
      display_name: provider.display_name,
      api_style: provider.api_style || "openai_compatible",
      base_url: provider.base_url || "",
      organization: provider.organization || "",
      priority: provider.priority,
      api_key: "",
      clear_api_key: false,
      is_active: provider.is_active,
    });
    setProviderDrawerMode("edit");
  };

  const openCreateModel = () => {
    resetModelForm();
    setModelDrawerMode("create");
  };

  const openEditModel = (model: AIModelItem) => {
    setEditingModelId(model.model_key);
    setModelForm({
      provider_id: model.provider_id,
      model_name: model.model_name,
      display_name: model.display_name,
      model_type: model.model_type || "chat",
      api_style: model.api_style || "",
      temperature: model.temperature == null ? "" : String(model.temperature),
      runtime_max_concurrency: model.runtime_max_concurrency == null ? "" : String(model.runtime_max_concurrency),
      thinking_mode: model.thinking_mode ?? "default",
      availability_timezone: model.availability_timezone || "Asia/Shanghai",
      availability_windows: (model.availability_windows ?? []).map((window) => ({ ...window })),
      priority: model.priority,
      is_enabled: model.is_enabled,
      is_default: model.is_default,
    });
    setModelDrawerMode("edit");
  };

  const submitProvider = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (providerDrawerMode === "edit" && editingProviderId) {
      updateProvider.mutate(
        {
          providerKey: editingProviderId,
          payload: {
            display_name: providerForm.display_name.trim(),
            api_style: providerForm.api_style,
            base_url: providerForm.base_url.trim() || null,
            organization: providerForm.organization.trim() || null,
            priority: Number(providerForm.priority) || 100,
            is_active: providerForm.is_active,
            clear_api_key: providerForm.clear_api_key,
            ...(providerForm.api_key.trim() ? { api_key: providerForm.api_key.trim() } : {}),
          },
        },
        { onSuccess: () => setProviderDrawerMode(null) },
      );
      return;
    }

    createProvider.mutate(
      {
        provider_key: providerForm.provider_key.trim(),
        provider_name: providerForm.provider_name.trim(),
        display_name: providerForm.display_name.trim() || providerForm.provider_key.trim(),
        api_style: providerForm.api_style,
        base_url: providerForm.base_url.trim() || null,
        api_key: providerForm.api_key.trim() || null,
        organization: providerForm.organization.trim() || null,
        priority: Number(providerForm.priority) || 100,
        is_active: providerForm.is_active,
      },
      { onSuccess: () => setProviderDrawerMode(null) },
    );
  };

  const submitModel = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!modelForm.provider_id) return;

    const payload = {
      display_name: modelForm.display_name.trim() || modelForm.model_name.trim(),
      model_type: modelForm.model_type.trim() || "chat",
      api_style: modelForm.api_style.trim() || null,
      temperature: modelForm.temperature.trim() === "" ? null : Number(modelForm.temperature),
      runtime_max_concurrency: modelForm.runtime_max_concurrency.trim() === "" ? null : Number(modelForm.runtime_max_concurrency),
      thinking_mode: modelForm.thinking_mode,
      availability_timezone: modelForm.availability_timezone.trim() || "Asia/Shanghai",
      availability_windows: modelForm.availability_windows,
      priority: Number(modelForm.priority) || 100,
      is_enabled: modelForm.is_enabled,
      is_default: modelForm.is_default,
    };

    if (modelDrawerMode === "edit" && editingModelId) {
      updateModel.mutate({ modelKey: editingModelId, payload }, { onSuccess: () => setModelDrawerMode(null) });
      return;
    }

    const selectedProvider = providers?.find((provider) => provider.id === Number(modelForm.provider_id));
    if (!selectedProvider) return;
    createModel.mutate(
      {
        provider_key: selectedProvider.provider_key,
        model_name: modelForm.model_name.trim(),
        ...payload,
      },
      { onSuccess: () => setModelDrawerMode(null) },
    );
  };

  const onDeleteProvider = (provider: AIProviderItem) => {
    const modelCount = modelCountByProvider.get(provider.id) ?? 0;
    const confirmText = isZh
      ? `确认删除提供商 ${provider.display_name}？${modelCount > 0 ? `\n\n该提供商下 ${modelCount} 个模型也会被删除。` : ""}`
      : `Delete provider ${provider.display_name}?${modelCount > 0 ? `\n\n${modelCount} models under this provider will also be removed.` : ""}`;
    if (!window.confirm(confirmText)) return;
    setDeletingProviderId(provider.provider_key);
    deleteProvider.mutate(provider.provider_key, { onSettled: () => setDeletingProviderId(null) });
  };

  const onDeleteModel = (model: AIModelItem) => {
    const confirmText = isZh
      ? `确认删除模型 ${model.display_name}？${model.is_default ? "\n\n这是默认模型，删除后系统会自动选择下一个模型作为默认。" : ""}`
      : `Delete model ${model.display_name}?${model.is_default ? "\n\nThis is the default model. A replacement default will be selected automatically." : ""}`;
    if (!window.confirm(confirmText)) return;
    setDeletingModelId(model.model_key);
    deleteModel.mutate(model.model_key, { onSettled: () => setDeletingModelId(null) });
  };

  const onRebuildFromEnv = () => {
    if (!window.confirm(copy.rebuildConfirm)) return;
    rebuildProviders.mutate({ force: true });
  };

  const onDiscoverProviderModels = (provider: AIProviderItem) => {
    setDiscoveryResult(null);
    setDiscoverySelection([]);
    setDiscoveryImportResults({});
    discoverProviderModels.mutate(provider.provider_key, {
      onSuccess: (result) => {
        const configuredModels = new Set(
          (models ?? []).filter((model) => model.provider_id === provider.id).map((model) => model.model_name),
        );
        setDiscoverySelection(result.models.filter((model) => !configuredModels.has(model)));
        setDiscoveryResult({ provider, result });
      },
    });
  };

  const onAddDiscoveredModels = async () => {
    if (!discoveryResult || !discoverySelection.length) return;

    const { provider } = discoveryResult;
    const selectedModels = [...discoverySelection];
    setIsImportingDiscoveredModels(true);
    setDiscoveryImportResults({});

    for (const modelName of selectedModels) {
      setDiscoveryImportResults((current) => ({ ...current, [modelName]: { status: "checking", message: copy.importing } }));
      try {
        const createdModel = await createModel.mutateAsync({
          provider_key: provider.provider_key,
          model_name: modelName,
          display_name: modelName,
          model_type: "chat",
          is_enabled: true,
          priority: provider.priority,
        });
        const checkResult = await testModel.mutateAsync(createdModel.model_key) as { success?: boolean; status?: string; message?: string };
        setDiscoveryImportResults((current) => ({
          ...current,
          [modelName]: {
            status: checkResult.status || (checkResult.success ? "available" : "failed"),
            message: checkResult.message || (checkResult.success ? "" : "Model test failed"),
          },
        }));
      } catch (error) {
        setDiscoveryImportResults((current) => ({
          ...current,
          [modelName]: { status: "failed", message: mutationErrorText(error, lang) },
        }));
      }
    }

    setDiscoverySelection([]);
    setIsImportingDiscoveredModels(false);
  };

  return { lang, isZh, providers, loadingProviders, models, loadingModels, runtimeRoutes, loadingRuntimeRoutes, runtimeRoutesError, createProvider, updateProvider, deleteProvider, testProvider, discoverProviderModels, rebuildProviders, createModel, updateModel, testModel, deleteModel, checkAll, providerDrawerMode, setProviderDrawerMode, modelDrawerMode, setModelDrawerMode, editingProviderId, setEditingProviderId, editingModelId, setEditingModelId, search, setSearch, healthFilter, setHealthFilter, activeView, setActiveView, callLogProvider, setCallLogProvider, callLogStatus, setCallLogStatus, callLogPageNumber, setCallLogPageNumber, callLogPage, loadingCallLogs, callLogsError, callLogs, callLogTotalPages, deletingProviderId, setDeletingProviderId, deletingModelId, setDeletingModelId, discoveryResult, setDiscoveryResult, discoverySelection, setDiscoverySelection, discoveryImportResults, setDiscoveryImportResults, isImportingDiscoveredModels, setIsImportingDiscoveredModels, providerForm, setProviderForm, modelForm, setModelForm, copy, modelCountByProvider, providerOptions, summary, runtimeRouteByModelKey, searchQuery, filteredProviders, filteredModels, filteredRuntimeRoutes, configuredDiscoveredModels, selectableDiscoveredModels, operationError, resetProviderForm, resetModelForm, openCreateProvider, openEditProvider, openCreateModel, openEditModel, submitProvider, submitModel, onDeleteProvider, onDeleteModel, onRebuildFromEnv, onDiscoverProviderModels, onAddDiscoveredModels };
}

export type ModelCenterController = ReturnType<typeof useModelCenter>;
