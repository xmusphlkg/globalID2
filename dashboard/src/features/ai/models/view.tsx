"use client";

import Link from "next/link";
import { useMemo, useState, type FormEvent, type MouseEvent, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Cpu,
  GitBranch,
  KeyRound,
  ListTodo,
  MessageSquareText,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import {
  type AIModelItem,
  type AIProviderItem,
  type AIRuntimeRoute,
  useAIModels,
  useAIProviders,
  useAIRuntimeRoutes,
  useCheckAllAIModels,
  useCreateAIModel,
  useCreateAIProvider,
  useDeleteAIModel,
  useDeleteAIProvider,
  useRebuildAIProviders,
  useTestAIModel,
  useTestAIProvider,
  useUpdateAIModel,
  useUpdateAIProvider,
} from "@/features/ai/api";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DetailDrawer } from "@/components/ui/DetailDrawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";

type HealthFilter = "all" | "healthy" | "attention" | "checking";
type DrawerMode = "create" | "edit" | null;

const inputClass =
  "h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted disabled:bg-tremor-background-subtle disabled:text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatDuration(seconds: number, lang: "en" | "zh"): string {
  if (!seconds || seconds <= 0) return lang === "zh" ? "0秒" : "0s";
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (lang === "zh") {
    if (mins > 0 && secs > 0) return `${mins}分 ${secs}秒`;
    if (mins > 0) return `${mins}分`;
    return `${secs}秒`;
  }
  if (mins > 0 && secs > 0) return `${mins}m ${secs}s`;
  if (mins > 0) return `${mins}m`;
  return `${secs}s`;
}

function queryErrorText(error: unknown, lang: "en" | "zh"): string {
  const fallback = lang === "zh" ? "接口返回失败，请检查模型中心后端服务。" : "Request failed. Please check the model-center backend service.";
  if (error instanceof ApiError) return `Request failed (${error.status}). ${error.message || fallback}`;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

function mutationErrorText(error: unknown, lang: "en" | "zh"): string {
  if (error instanceof Error && error.message) return error.message;
  return lang === "zh" ? "操作失败，请检查接口返回。" : "Action failed. Please review the API response.";
}

function matchesQuery(fields: Array<string | number | null | undefined>, query: string): boolean {
  if (!query) return true;
  return fields.some((field) => String(field ?? "").toLowerCase().includes(query));
}

function providerMatchesFilter(provider: AIProviderItem, filter: HealthFilter): boolean {
  const status = (provider.last_check_status || "").toLowerCase();
  if (filter === "all") return true;
  if (filter === "healthy") return provider.is_active && provider.has_api_key && status === "available" && !provider.rate_limit_active;
  if (filter === "attention") {
    return !provider.is_active || !provider.has_api_key || provider.rate_limit_active || ["unavailable", "failed", "rate_limited"].includes(status);
  }
  return status === "checking";
}

function modelMatchesFilter(model: AIModelItem, filter: HealthFilter): boolean {
  const status = (model.last_check_status || "").toLowerCase();
  if (filter === "all") return true;
  if (filter === "healthy") return model.is_enabled && status === "available" && !model.rate_limit_active;
  if (filter === "attention") {
    return !model.is_enabled || model.rate_limit_active || ["unavailable", "failed", "rate_limited"].includes(status);
  }
  return status === "checking";
}

function routeMatchesFilter(route: AIRuntimeRoute, filter: HealthFilter): boolean {
  if (filter === "all") return true;
  if (filter === "healthy") return route.available_for_routing;
  if (filter === "attention") return !route.available_for_routing || route.rate_limit_active || !route.has_api_key;
  return (route.last_check_status || "").toLowerCase() === "checking";
}

function statusTone(status: string): "neutral" | "info" | "success" | "warning" | "danger" | "primary" {
  const normalized = (status || "").toLowerCase();
  if (normalized === "available") return "success";
  if (normalized === "rate_limited" || normalized === "checking") return "warning";
  if (normalized === "unavailable" || normalized === "failed") return "danger";
  return "neutral";
}

function ActionButton({
  children,
  icon,
  tone = "neutral",
  disabled,
  onClick,
  type = "button",
  className,
}: {
  children: ReactNode;
  icon?: ReactNode;
  tone?: "neutral" | "primary" | "danger";
  disabled?: boolean;
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
  type?: "button" | "submit";
  className?: string;
}) {
  const toneClass =
    tone === "primary"
      ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted hover:bg-tremor-brand/90"
      : tone === "danger"
        ? "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300"
        : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle";

  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 items-center justify-center gap-2 rounded-tremor-default border px-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-55",
        toneClass,
        className,
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{label}</span>
      {children}
      {hint ? <span className="block text-xs text-tremor-content dark:text-dark-tremor-content">{hint}</span> : null}
    </label>
  );
}

function FormSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-b border-tremor-border pb-5 last:border-b-0 last:pb-0 dark:border-dark-tremor-border">
      <h3 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{title}</h3>
      {children}
    </section>
  );
}

function AlertBox({ tone, children }: { tone: "danger" | "warning"; children: ReactNode }) {
  const toneClass =
    tone === "danger"
      ? "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/50 dark:bg-rose-950/25 dark:text-rose-200"
      : "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/25 dark:text-amber-200";
  return <div className={cn("rounded-tremor-default border px-4 py-3 text-sm", toneClass)}>{children}</div>;
}

function LastCheckCell({ checkedAt, message }: { checkedAt?: string | null; message?: string | null }) {
  return (
    <div className="min-w-[220px] max-w-[360px]">
      <p className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {formatDateTime(checkedAt)}
      </p>
      <p
        className="mt-1 truncate text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong"
        title={message || undefined}
      >
        {message || "-"}
      </p>
    </div>
  );
}

export default function AIModelsPage() {
  const { lang } = useAppStore();
  const isZh = lang === "zh";

  const { data: providers, isLoading: loadingProviders } = useAIProviders();
  const { data: models, isLoading: loadingModels } = useAIModels();
  const { data: runtimeRoutes, isLoading: loadingRuntimeRoutes, error: runtimeRoutesError } = useAIRuntimeRoutes();

  const createProvider = useCreateAIProvider();
  const updateProvider = useUpdateAIProvider();
  const deleteProvider = useDeleteAIProvider();
  const testProvider = useTestAIProvider();
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
  const [deletingProviderId, setDeletingProviderId] = useState<string | null>(null);
  const [deletingModelId, setDeletingModelId] = useState<string | null>(null);

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
    max_tokens: "",
    priority: 100,
    is_enabled: true,
    is_default: false,
  });

  const copy = isZh
    ? {
        subtitle: "统一管理提供商、模型路由、运行时优先级和健康状态。",
        openTasks: "AI 任务",
        openInteractions: "AI 交互",
        checkAllModels: "对话检查全部模型",
        rebuildFromEnv: "重建 env 提供商",
        rebuildConfirm: "该操作会按 .env 配置重新初始化提供商及默认模型。已有自定义记录将被清空。",
        providers: "提供商",
        activeProviders: "活跃提供商",
        models: "模型",
        enabledModels: "启用模型",
        keys: "已配密钥",
        routes: "运行路由",
        defaults: "默认模型",
        searchPlaceholder: "搜索 provider、model、base url 或 route key",
        all: "全部",
        healthy: "健康",
        attention: "需处理",
        checking: "检查中",
        noProviders: "当前筛选下没有提供商。",
        noModels: "当前筛选下没有模型。",
        noRoutes: "当前筛选下没有运行时路由。",
        newProvider: "新建提供商",
        editProvider: "编辑提供商",
        newModel: "新建模型",
        editModel: "编辑模型",
        save: "保存",
        create: "创建",
        reset: "重置",
        test: "对话测试",
        edit: "编辑",
        delete: "删除",
        enable: "启用",
        disable: "停用",
        setDefault: "设为默认",
        credential: "凭证",
        noCredential: "未配置",
        priority: "优先级",
        lastCheck: "最近检查",
        lastMessage: "最近消息",
        cooldown: "冷却",
        routeCoverage: "可用路由覆盖率",
        providerKey: "Provider Key",
        providerName: "Provider Name",
        displayName: "显示名称",
        apiStyle: "API 风格",
        baseUrl: "Base URL",
        apiKey: "API Key",
        organization: "组织",
        clearApiKey: "清空当前 API Key",
        providerActive: "启用该提供商",
        selectProvider: "选择提供商",
        modelName: "模型名",
        modelType: "模型类型",
        temperature: "温度",
        maxTokens: "最大 Token",
        modelEnabled: "启用该模型",
        defaultModel: "默认模型",
      }
    : {
        subtitle: "Manage providers, model routes, runtime priority, and health in one workspace.",
        openTasks: "AI Tasks",
        openInteractions: "AI Interactions",
        checkAllModels: "Chat Test All",
        rebuildFromEnv: "Rebuild From .env",
        rebuildConfirm: "This will rebuild providers/models from .env and clear current custom records.",
        providers: "Providers",
        activeProviders: "Active Providers",
        models: "Models",
        enabledModels: "Enabled Models",
        keys: "Configured Keys",
        routes: "Runtime Routes",
        defaults: "Default Models",
        searchPlaceholder: "Search provider, model, base url, or route key",
        all: "All",
        healthy: "Healthy",
        attention: "Needs Attention",
        checking: "Checking",
        noProviders: "No providers match the current filter.",
        noModels: "No models match the current filter.",
        noRoutes: "No runtime routes match the current filter.",
        newProvider: "New Provider",
        editProvider: "Edit Provider",
        newModel: "New Model",
        editModel: "Edit Model",
        save: "Save",
        create: "Create",
        reset: "Reset",
        test: "Chat Test",
        edit: "Edit",
        delete: "Delete",
        enable: "Enable",
        disable: "Disable",
        setDefault: "Set Default",
        credential: "Credential",
        noCredential: "Not configured",
        priority: "Priority",
        lastCheck: "Last check",
        lastMessage: "Last message",
        cooldown: "Cooling",
        routeCoverage: "Ready route coverage",
        providerKey: "Provider Key",
        providerName: "Provider Name",
        displayName: "Display Name",
        apiStyle: "API Style",
        baseUrl: "Base URL",
        apiKey: "API Key",
        organization: "Organization",
        clearApiKey: "Clear current API Key",
        providerActive: "Provider is active",
        selectProvider: "Select Provider",
        modelName: "Model Name",
        modelType: "Model Type",
        temperature: "Temperature",
        maxTokens: "Max Tokens",
        modelEnabled: "Model is enabled",
        defaultModel: "Default model",
      };

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
        .filter((model) => modelMatchesFilter(model, healthFilter))
        .sort((a, b) => Number(b.is_default) - Number(a.is_default) || Number(b.is_enabled) - Number(a.is_enabled) || a.priority - b.priority || a.display_name.localeCompare(b.display_name)),
    [healthFilter, models, searchQuery],
  );

  const filteredRuntimeRoutes = useMemo(
    () =>
      (runtimeRoutes ?? [])
        .filter((route) => matchesQuery([route.model_name, route.model_key, route.provider_key, route.provider_name, route.api_style, route.base_url], searchQuery))
        .filter((route) => routeMatchesFilter(route, healthFilter))
        .sort((a, b) => (a.priority ?? Number.MAX_SAFE_INTEGER) - (b.priority ?? Number.MAX_SAFE_INTEGER)),
    [healthFilter, runtimeRoutes, searchQuery],
  );

  const operationError =
    createProvider.error ??
    updateProvider.error ??
    deleteProvider.error ??
    rebuildProviders.error ??
    testProvider.error ??
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
      max_tokens: "",
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
      max_tokens: model.max_tokens == null ? "" : String(model.max_tokens),
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
      max_tokens: modelForm.max_tokens.trim() === "" ? null : Number(modelForm.max_tokens),
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

  const providerColumns = useMemo<DataTableColumn<AIProviderItem>[]>(
    () => [
      {
        key: "status",
        header: isZh ? "状态" : "Status",
        render: (provider) => (
          <div className="space-y-1">
            <StatusBadge tone={provider.is_active ? "success" : "neutral"}>{provider.is_active ? "active" : "inactive"}</StatusBadge>
            <StatusBadge tone={statusTone(provider.last_check_status)}>{provider.last_check_status || "unknown"}</StatusBadge>
          </div>
        ),
      },
      {
        key: "provider",
        header: copy.providers,
        render: (provider) => (
          <div className="min-w-[230px] max-w-[380px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{provider.display_name}</p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{provider.provider_key}</p>
          </div>
        ),
      },
      {
        key: "endpoint",
        header: "Endpoint",
        render: (provider) => (
          <div className="min-w-[220px] max-w-[420px]">
            <p className="truncate text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">{provider.api_style}</p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{provider.base_url || "-"}</p>
          </div>
        ),
      },
      {
        key: "credential",
        header: copy.credential,
        render: (provider) => (
          <div className="min-w-[150px]">
            <StatusBadge tone={provider.has_api_key ? "success" : "danger"}>
              {provider.api_key_hint || copy.noCredential}
            </StatusBadge>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {modelCountByProvider.get(provider.id) ?? 0} {copy.models}
            </p>
          </div>
        ),
      },
      {
        key: "last-check",
        header: copy.lastCheck,
        render: (provider) => (
          <LastCheckCell checkedAt={provider.last_checked_at} message={provider.last_check_message} />
        ),
      },
      {
        key: "priority",
        header: copy.priority,
        render: (provider) => <span className="text-sm text-tremor-content dark:text-dark-tremor-content">P{provider.priority}</span>,
      },
      {
        key: "actions",
        header: "",
        className: "text-right",
        render: (provider) => (
          <div className="flex min-w-[310px] justify-end gap-2">
            <ActionButton disabled={testProvider.isPending} onClick={() => testProvider.mutate(provider.provider_key)}>{copy.test}</ActionButton>
            <ActionButton onClick={() => openEditProvider(provider)} icon={<Pencil className="h-4 w-4" />}>{copy.edit}</ActionButton>
            <ActionButton
              onClick={() => updateProvider.mutate({ providerKey: provider.provider_key, payload: { is_active: !provider.is_active } })}
            >
              {provider.is_active ? copy.disable : copy.enable}
            </ActionButton>
            <ActionButton
              tone="danger"
              disabled={deleteProvider.isPending && deletingProviderId === provider.provider_key}
              onClick={() => onDeleteProvider(provider)}
              icon={<Trash2 className="h-4 w-4" />}
            >
              {copy.delete}
            </ActionButton>
          </div>
        ),
      },
    ],
    [copy, deleteProvider.isPending, deletingProviderId, isZh, modelCountByProvider, testProvider.isPending, updateProvider],
  );

  const modelColumns = useMemo<DataTableColumn<AIModelItem>[]>(
    () => [
      {
        key: "status",
        header: isZh ? "状态" : "Status",
        render: (model) => (
          <div className="space-y-1">
            <StatusBadge tone={model.is_enabled ? "success" : "neutral"}>{model.is_enabled ? "enabled" : "disabled"}</StatusBadge>
            {model.is_default ? <StatusBadge tone="primary">default</StatusBadge> : null}
            <StatusBadge tone={statusTone(model.last_check_status)}>{model.last_check_status || "unknown"}</StatusBadge>
          </div>
        ),
      },
      {
        key: "model",
        header: copy.models,
        render: (model) => (
          <div className="min-w-[240px] max-w-[420px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{model.display_name}</p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{model.model_key}</p>
          </div>
        ),
      },
      {
        key: "provider",
        header: copy.providers,
        render: (model) => (
          <div className="min-w-[140px]">
            <p className="truncate text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{model.provider_key}</p>
            <p className="mt-1 truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{model.provider_name}</p>
          </div>
        ),
      },
      {
        key: "params",
        header: isZh ? "参数" : "Params",
        render: (model) => (
          <div className="min-w-[140px] text-sm text-tremor-content dark:text-dark-tremor-content">
            <p>{model.model_type || "chat"}</p>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              temp {model.temperature ?? "-"} / max {model.max_tokens ?? "-"}
            </p>
          </div>
        ),
      },
      {
        key: "last-check",
        header: copy.lastCheck,
        render: (model) => (
          <LastCheckCell checkedAt={model.last_checked_at} message={model.last_check_message} />
        ),
      },
      {
        key: "priority",
        header: copy.priority,
        render: (model) => <span className="text-sm text-tremor-content dark:text-dark-tremor-content">P{model.priority}</span>,
      },
      {
        key: "actions",
        header: "",
        className: "text-right",
        render: (model) => (
          <div className="flex min-w-[360px] justify-end gap-2">
            <ActionButton disabled={testModel.isPending} onClick={() => testModel.mutate(model.model_key)}>{copy.test}</ActionButton>
            <ActionButton onClick={() => openEditModel(model)} icon={<Pencil className="h-4 w-4" />}>{copy.edit}</ActionButton>
            <ActionButton onClick={() => updateModel.mutate({ modelKey: model.model_key, payload: { is_enabled: !model.is_enabled } })}>
              {model.is_enabled ? copy.disable : copy.enable}
            </ActionButton>
            {!model.is_default ? (
              <ActionButton onClick={() => updateModel.mutate({ modelKey: model.model_key, payload: { is_default: true } })}>{copy.setDefault}</ActionButton>
            ) : null}
            <ActionButton
              tone="danger"
              disabled={deleteModel.isPending && deletingModelId === model.model_key}
              onClick={() => onDeleteModel(model)}
              icon={<Trash2 className="h-4 w-4" />}
            >
              {copy.delete}
            </ActionButton>
          </div>
        ),
      },
    ],
    [copy, deleteModel.isPending, deletingModelId, isZh, testModel.isPending, updateModel],
  );

  const routeColumns = useMemo<DataTableColumn<AIRuntimeRoute>[]>(
    () => [
      {
        key: "rank",
        header: "#",
        render: (route) => <span className="font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">P{route.priority ?? "-"}</span>,
      },
      {
        key: "route",
        header: copy.routes,
        render: (route) => (
          <div className="min-w-[250px] max-w-[430px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{route.model_name}</p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{route.model_key}</p>
          </div>
        ),
      },
      {
        key: "provider",
        header: copy.providers,
        render: (route) => <span className="whitespace-nowrap text-sm text-tremor-content dark:text-dark-tremor-content">{route.provider_key}</span>,
      },
      {
        key: "ready",
        header: "Ready",
        render: (route) => (
          <div className="flex flex-wrap gap-1.5">
            <StatusBadge tone={route.has_api_key ? "success" : "danger"}>{route.has_api_key ? "key ready" : copy.noCredential}</StatusBadge>
            <StatusBadge tone={route.available_for_routing ? "success" : route.rate_limit_active ? "warning" : "neutral"}>
              {route.available_for_routing ? "routable" : route.rate_limit_active ? copy.cooldown : "disabled"}
            </StatusBadge>
          </div>
        ),
      },
      {
        key: "cooldown",
        header: "Cooldown",
        render: (route) => (
          <span className="whitespace-nowrap text-sm text-tremor-content dark:text-dark-tremor-content">
            {route.rate_limit_active ? formatDuration(route.rate_limit_remaining_seconds, lang) : "-"}
          </span>
        ),
      },
    ],
    [copy, lang],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_ai")}
        title={t(lang, "ai_models")}
        description={copy.subtitle}
        meta={
          <>
            <StatusBadge tone={summary.configuredKeys === summary.totalProviders && summary.totalProviders > 0 ? "success" : "warning"}>
              {summary.configuredKeys}/{summary.totalProviders} keys
            </StatusBadge>
            <StatusBadge tone={summary.routeCoverage >= 80 ? "success" : summary.routeCoverage >= 40 ? "warning" : "danger"}>
              {summary.routeCoverage}% {copy.routeCoverage}
            </StatusBadge>
          </>
        }
        actions={
          <>
            <Link
              href="/production/ai"
              className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              <ListTodo className="h-4 w-4" />
              {copy.openTasks}
            </Link>
            <Link
              href="/production/interactions"
              className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              <MessageSquareText className="h-4 w-4" />
              {copy.openInteractions}
            </Link>
            <ActionButton disabled={checkAll.isPending} onClick={() => checkAll.mutate()} icon={<ShieldCheck className="h-4 w-4" />}>
              {copy.checkAllModels}
            </ActionButton>
            <ActionButton disabled={rebuildProviders.isPending} onClick={onRebuildFromEnv} icon={<ArrowRight className="h-4 w-4" />}>
              {copy.rebuildFromEnv}
            </ActionButton>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label={copy.providers} value={summary.totalProviders} icon={<ShieldCheck className="h-4 w-4" />} tone="neutral" hint={`${summary.activeProviders} ${copy.activeProviders}`} />
        <MetricTile label={copy.enabledModels} value={summary.enabledModels} icon={<Cpu className="h-4 w-4" />} tone="primary" hint={`${summary.defaultModels} ${copy.defaults}`} />
        <MetricTile label={copy.keys} value={summary.configuredKeys} icon={<KeyRound className="h-4 w-4" />} tone={summary.configuredKeys > 0 ? "success" : "danger"} hint={`${summary.totalProviders} ${copy.providers}`} />
        <MetricTile label={copy.routes} value={summary.totalRoutes} icon={<GitBranch className="h-4 w-4" />} tone="info" hint={`${summary.runtimeReady} ready`} />
      </div>

      {operationError ? (
        <AlertBox tone="danger">
          <div className="flex gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{mutationErrorText(operationError, lang)}</span>
          </div>
        </AlertBox>
      ) : null}

      <FilterToolbar>
        <div className="relative min-w-[240px] flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle" />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={copy.searchPlaceholder}
            className={cn(inputClass, "pl-9")}
          />
        </div>
        <select
          className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          value={healthFilter}
          onChange={(event) => setHealthFilter(event.target.value as HealthFilter)}
          aria-label={isZh ? "健康状态筛选" : "Health filter"}
        >
          <option value="all">{copy.all}</option>
          <option value="healthy">{copy.healthy}</option>
          <option value="attention">{copy.attention}</option>
          <option value="checking">{copy.checking}</option>
        </select>
        <ActionButton tone="primary" onClick={openCreateProvider} icon={<Plus className="h-4 w-4" />}>{copy.newProvider}</ActionButton>
        <ActionButton tone="primary" onClick={openCreateModel} icon={<Plus className="h-4 w-4" />}>{copy.newModel}</ActionButton>
      </FilterToolbar>

      <section className="space-y-3">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{copy.providers}</h2>
            <p className="text-sm text-tremor-content dark:text-dark-tremor-content">Inspect connectivity, credentials, and base endpoints.</p>
          </div>
          <StatusBadge tone="neutral">{filteredProviders.length}</StatusBadge>
        </div>
        {loadingProviders ? (
          <SkeletonRows />
        ) : (
          <DataTable
            columns={providerColumns}
            rows={filteredProviders}
            getRowKey={(provider) => provider.id}
            emptyState={<EmptyState icon={<ShieldCheck className="h-10 w-10" />} title={copy.noProviders} />}
          />
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{copy.models}</h2>
            <p className="text-sm text-tremor-content dark:text-dark-tremor-content">Review default routes, enablement state, and runtime parameters.</p>
          </div>
          <StatusBadge tone="neutral">{filteredModels.length}</StatusBadge>
        </div>
        {loadingModels ? (
          <SkeletonRows />
        ) : (
          <DataTable
            columns={modelColumns}
            rows={filteredModels}
            getRowKey={(model) => model.id}
            emptyState={<EmptyState icon={<Cpu className="h-10 w-10" />} title={copy.noModels} />}
          />
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{copy.routes}</h2>
            <p className="text-sm text-tremor-content dark:text-dark-tremor-content">The system tries routes from top to bottom based on priority.</p>
          </div>
          <StatusBadge tone={summary.routeCoverage >= 80 ? "success" : summary.routeCoverage >= 40 ? "warning" : "danger"}>
            {summary.routeCoverage}%
          </StatusBadge>
        </div>
        {loadingRuntimeRoutes ? (
          <SkeletonRows />
        ) : runtimeRoutesError ? (
          <AlertBox tone="danger">{queryErrorText(runtimeRoutesError, lang)}</AlertBox>
        ) : (
          <DataTable
            columns={routeColumns}
            rows={filteredRuntimeRoutes}
            getRowKey={(route) => route.model_key}
            emptyState={<EmptyState icon={<GitBranch className="h-10 w-10" />} title={copy.noRoutes} />}
          />
        )}
      </section>

      <DetailDrawer
        open={providerDrawerMode !== null}
        title={providerDrawerMode === "edit" ? copy.editProvider : copy.newProvider}
        subtitle={providerDrawerMode === "edit" ? providerForm.provider_key : undefined}
        onClose={() => setProviderDrawerMode(null)}
      >
        <form className="space-y-5" onSubmit={submitProvider}>
          <FormSection title={copy.providers}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={copy.providerKey}>
                <input className={inputClass} value={providerForm.provider_key} disabled={providerDrawerMode === "edit"} required onChange={(event) => setProviderForm((prev) => ({ ...prev, provider_key: event.target.value }))} />
              </Field>
              <Field label={copy.providerName}>
                <input className={inputClass} value={providerForm.provider_name} disabled={providerDrawerMode === "edit"} required onChange={(event) => setProviderForm((prev) => ({ ...prev, provider_name: event.target.value }))} />
              </Field>
              <Field label={copy.displayName}>
                <input className={inputClass} value={providerForm.display_name} onChange={(event) => setProviderForm((prev) => ({ ...prev, display_name: event.target.value }))} />
              </Field>
              <Field label={copy.apiStyle}>
                <select className={inputClass} value={providerForm.api_style} onChange={(event) => setProviderForm((prev) => ({ ...prev, api_style: event.target.value }))}>
                  <option value="openai_compatible">openai_compatible</option>
                  <option value="anthropic">anthropic</option>
                </select>
              </Field>
              <Field label={copy.baseUrl}>
                <input className={inputClass} value={providerForm.base_url} onChange={(event) => setProviderForm((prev) => ({ ...prev, base_url: event.target.value }))} />
              </Field>
              <Field label={copy.organization}>
                <input className={inputClass} value={providerForm.organization} onChange={(event) => setProviderForm((prev) => ({ ...prev, organization: event.target.value }))} />
              </Field>
              <Field label={copy.priority}>
                <input type="number" className={inputClass} value={providerForm.priority} onChange={(event) => setProviderForm((prev) => ({ ...prev, priority: Number(event.target.value) }))} />
              </Field>
              <Field label={copy.apiKey}>
                <input type="password" className={inputClass} value={providerForm.api_key} onChange={(event) => setProviderForm((prev) => ({ ...prev, api_key: event.target.value }))} />
              </Field>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="flex min-h-12 cursor-pointer items-center gap-3 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong">
                <input type="checkbox" checked={providerForm.is_active} onChange={(event) => setProviderForm((prev) => ({ ...prev, is_active: event.target.checked }))} />
                {copy.providerActive}
              </label>
              {providerDrawerMode === "edit" ? (
                <label className="flex min-h-12 cursor-pointer items-center gap-3 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong">
                  <input type="checkbox" checked={providerForm.clear_api_key} onChange={(event) => setProviderForm((prev) => ({ ...prev, clear_api_key: event.target.checked }))} />
                  {copy.clearApiKey}
                </label>
              ) : null}
            </div>
          </FormSection>
          <div className="flex justify-end gap-2">
            <ActionButton onClick={resetProviderForm}>{copy.reset}</ActionButton>
            <ActionButton type="submit" tone="primary" disabled={createProvider.isPending || updateProvider.isPending}>
              {providerDrawerMode === "edit" ? copy.save : copy.create}
            </ActionButton>
          </div>
        </form>
      </DetailDrawer>

      <DetailDrawer
        open={modelDrawerMode !== null}
        title={modelDrawerMode === "edit" ? copy.editModel : copy.newModel}
        subtitle={modelDrawerMode === "edit" ? modelForm.model_name : undefined}
        onClose={() => setModelDrawerMode(null)}
      >
        <form className="space-y-5" onSubmit={submitModel}>
          <FormSection title={copy.models}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={copy.selectProvider}>
                <select className={inputClass} value={modelForm.provider_id || ""} required disabled={modelDrawerMode === "edit"} onChange={(event) => setModelForm((prev) => ({ ...prev, provider_id: Number(event.target.value) }))}>
                  <option value="">{copy.selectProvider}</option>
                  {providerOptions.map((provider) => <option key={provider.id} value={provider.id}>{provider.label}</option>)}
                </select>
              </Field>
              <Field label={copy.modelName}>
                <input className={inputClass} value={modelForm.model_name} disabled={modelDrawerMode === "edit"} required onChange={(event) => setModelForm((prev) => ({ ...prev, model_name: event.target.value }))} />
              </Field>
              <Field label={copy.displayName}>
                <input className={inputClass} value={modelForm.display_name} onChange={(event) => setModelForm((prev) => ({ ...prev, display_name: event.target.value }))} />
              </Field>
              <Field label={copy.modelType}>
                <input className={inputClass} value={modelForm.model_type} onChange={(event) => setModelForm((prev) => ({ ...prev, model_type: event.target.value }))} />
              </Field>
              <Field label={copy.apiStyle}>
                <input className={inputClass} value={modelForm.api_style} onChange={(event) => setModelForm((prev) => ({ ...prev, api_style: event.target.value }))} />
              </Field>
              <Field label={copy.priority}>
                <input type="number" className={inputClass} value={modelForm.priority} onChange={(event) => setModelForm((prev) => ({ ...prev, priority: Number(event.target.value) }))} />
              </Field>
              <Field label={copy.temperature}>
                <input type="number" step="0.01" className={inputClass} value={modelForm.temperature} onChange={(event) => setModelForm((prev) => ({ ...prev, temperature: event.target.value }))} />
              </Field>
              <Field label={copy.maxTokens}>
                <input type="number" className={inputClass} value={modelForm.max_tokens} onChange={(event) => setModelForm((prev) => ({ ...prev, max_tokens: event.target.value }))} />
              </Field>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="flex min-h-12 cursor-pointer items-center gap-3 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong">
                <input type="checkbox" checked={modelForm.is_enabled} onChange={(event) => setModelForm((prev) => ({ ...prev, is_enabled: event.target.checked }))} />
                {copy.modelEnabled}
              </label>
              <label className="flex min-h-12 cursor-pointer items-center gap-3 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong">
                <input type="checkbox" checked={modelForm.is_default} onChange={(event) => setModelForm((prev) => ({ ...prev, is_default: event.target.checked }))} />
                {copy.defaultModel}
              </label>
            </div>
          </FormSection>
          <div className="flex justify-end gap-2">
            <ActionButton onClick={resetModelForm}>{copy.reset}</ActionButton>
            <ActionButton type="submit" tone="primary" disabled={createModel.isPending || updateModel.isPending}>
              {modelDrawerMode === "edit" ? copy.save : copy.create}
            </ActionButton>
          </div>
        </form>
      </DetailDrawer>
    </div>
  );
}

function SkeletonRows() {
  return (
    <div className="space-y-3">
      {[1, 2, 3].map((item) => (
        <div key={item} className="h-16 animate-pulse rounded-tremor-default border border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background" />
      ))}
    </div>
  );
}
