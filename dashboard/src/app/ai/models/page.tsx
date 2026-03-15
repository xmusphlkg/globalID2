"use client";

import Link from "next/link";
import { FormEvent, useMemo, useState, type ReactNode } from "react";
import { Badge, Card, Grid, Metric, ProgressBar, Text, Title } from "@tremor/react";
import {
  ArrowRight,
  CheckCircle2,
  CircleSlash,
  Cpu,
  GitBranch,
  KeyRound,
  ListTodo,
  MessageSquareText,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
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
  useTestAIModel,
  useTestAIProvider,
  useUpdateAIModel,
  useUpdateAIProvider,
} from "@/lib/hooks/useAIModels";

const inputCls =
  "w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis";

const pillButtonCls =
  "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition";

type HealthFilter = "all" | "healthy" | "attention" | "checking";

function statusColor(status: string): "slate" | "emerald" | "rose" | "amber" {
  const s = (status || "").toLowerCase();
  if (s === "available") return "emerald";
  if (s === "rate_limited") return "amber";
  if (s === "unavailable" || s === "failed") return "rose";
  if (s === "checking") return "amber";
  return "slate";
}

function formatDateTime(value: string | null): string {
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

function mutationErrorText(error: unknown, lang: "en" | "zh"): string {
  if (error instanceof Error && error.message) return error.message;
  return lang === "zh" ? "操作失败，请检查接口返回。" : "Action failed. Please review the API response.";
}

function queryErrorText(error: unknown, lang: "en" | "zh"): string {
  const fallback = lang === "zh" ? "接口返回失败，请检查模型中心后端服务。" : "Request failed. Please check the model-center backend service.";
  if (error instanceof ApiError) {
    return `Request failed (${error.status}). ${error.message || fallback}`;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
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
    return !provider.is_active || !provider.has_api_key || provider.rate_limit_active || status === "unavailable" || status === "failed" || status === "rate_limited";
  }
  return status === "checking";
}

function modelMatchesFilter(model: AIModelItem, filter: HealthFilter): boolean {
  const status = (model.last_check_status || "").toLowerCase();
  if (filter === "all") return true;
  if (filter === "healthy") return model.is_enabled && status === "available" && !model.rate_limit_active;
  if (filter === "attention") {
    return !model.is_enabled || model.rate_limit_active || status === "unavailable" || status === "failed" || status === "rate_limited";
  }
  return status === "checking";
}

function routeMatchesFilter(route: AIRuntimeRoute, filter: HealthFilter): boolean {
  if (filter === "all") return true;
  if (filter === "healthy") return route.available_for_routing;
  if (filter === "attention") return !route.available_for_routing || route.rate_limit_active || !route.has_api_key;
  return (route.last_check_status || "").toLowerCase() === "checking";
}

function DetailField({ label, value, mono = false }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="rounded-2xl border border-tremor-border/70 bg-tremor-background-subtle/70 p-3 dark:border-dark-tremor-border/70 dark:bg-dark-tremor-background-subtle/70">
      <Text className="text-[11px] uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {label}
      </Text>
      <div
        className={cn(
          "mt-1 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong",
          mono && "break-all font-mono text-xs",
        )}
      >
        {value}
      </div>
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
  const testProvider = useTestAIProvider();

  const createModel = useCreateAIModel();
  const updateModel = useUpdateAIModel();
  const testModel = useTestAIModel();
  const checkAll = useCheckAllAIModels();

  const [providerForm, setProviderForm] = useState({
    provider_key: "",
    provider_name: "openai",
    display_name: "",
    api_style: "openai_compatible",
    base_url: "",
    api_key: "",
    priority: 100,
  });

  const [modelForm, setModelForm] = useState({
    provider_id: 0,
    model_name: "",
    display_name: "",
    priority: 100,
  });

  const [editingProviderId, setEditingProviderId] = useState<number | null>(null);
  const [providerEditForm, setProviderEditForm] = useState({
    display_name: "",
    api_style: "openai_compatible",
    base_url: "",
    organization: "",
    priority: 100,
    api_key_new: "",
    clear_api_key: false,
    is_active: true,
  });

  const [editingModelId, setEditingModelId] = useState<number | null>(null);
  const [modelEditForm, setModelEditForm] = useState({
    display_name: "",
    model_type: "chat",
    api_style: "",
    temperature: "",
    max_tokens: "",
    priority: 100,
    is_enabled: true,
    is_default: false,
  });

  const [search, setSearch] = useState("");
  const [healthFilter, setHealthFilter] = useState<HealthFilter>("all");

  const copy = isZh
    ? {
        subtitle: "把提供商配置、模型路由和运行时优先级放在同一个工作台里，快速发现凭证缺口、默认模型和可用性问题。",
        openTasks: "打开 AI 任务",
        openInteractions: "打开 AI 交互",
        checkAllModels: "检查全部模型",
        providersMetric: "提供商",
        activeProvidersMetric: "活跃提供商",
        modelsMetric: "启用模型",
        keysMetric: "已配密钥",
        routesMetric: "运行路由",
        defaultsMetric: "默认模型",
        providerFormTitle: "新增 API 提供商",
        providerFormDesc: "先录入端点和密钥，再把模型挂到对应提供商下。",
        modelFormTitle: "新增模型路由",
        modelFormDesc: "为同一个 provider 添加多个模型，并通过优先级控制选择顺序。",
        providerFormHint: "建议用稳定的 provider_key 标识环境，例如 openai-prod 或 qwen-cn-primary。",
        modelFormHint: "优先级数值越小越优先，默认模型建议只保留一个。",
        providerKey: "Provider Key",
        providerName: "Provider Name",
        displayName: "显示名称",
        apiStyle: "API 风格",
        baseUrl: "Base URL",
        apiKey: "API Key",
        priority: "优先级",
        createProvider: "新增提供商",
        createModel: "新增模型",
        selectProvider: "选择提供商",
        modelName: "模型名",
        workspaceFilters: "工作台筛选",
        workspaceFiltersDesc: "按关键字和健康状态过滤提供商、模型与运行时链路。",
        searchPlaceholder: "搜索 provider、model、base url 或路由 key",
        all: "全部",
        healthy: "健康",
        attention: "需处理",
        checking: "检查中",
        filteredProviders: "筛选后提供商",
        filteredModels: "筛选后模型",
        filteredRoutes: "筛选后路由",
        providersTitle: "API 提供商",
        providersDesc: "检查连接状态、凭证配置和基础端点。",
        modelsTitle: "模型路由",
        modelsDesc: "查看默认模型、启用状态和运行参数。",
        runtimeTitle: "运行时调配链路",
        runtimeDesc: "系统会按优先级从上到下尝试匹配可用路由。",
        active: "活跃",
        inactive: "停用",
        enabled: "启用",
        disabled: "停用",
        defaultModel: "默认",
        providerCount: "模型数",
        credential: "凭证",
        noCredential: "未配置",
        lastCheck: "最近检查",
        lastMessage: "最近消息",
        organization: "组织",
        test: "测试",
        edit: "编辑",
        disable: "停用",
        enable: "启用",
        save: "保存",
        cancel: "取消",
        clearApiKey: "清空当前 API Key",
        providerIsActive: "启用该提供商",
        modelType: "模型类型",
        modelTypePlaceholder: "模型类型（chat/text）",
        inheritProvider: "继承 Provider",
        temperature: "温度",
        maxTokens: "最大 Token",
        modelKey: "模型 Key",
        routeCoverage: "可用路由覆盖率",
        routeReady: "密钥就绪",
        routable: "可路由",
        preferredRoute: "首选",
        noProviders: "当前筛选下没有提供商。",
        noModels: "当前筛选下没有模型。",
        noRoutes: "当前筛选下没有运行时路由。",
        loadingProviders: "正在加载提供商...",
        loadingModels: "正在加载模型...",
        routeHint: "缺少 API Key 的路由不会真正可用。",
        loadingRoutes: "正在加载运行时路由...",
        runtimeErrorTitle: "运行时路由加载失败",
        setDefault: "设为默认",
        modelEnabled: "启用该模型",
        setDefaultModel: "设为默认模型",
        temperaturePlaceholder: "温度（留空使用默认）",
        maxTokensPlaceholder: "最大 token（留空使用默认）",
        organizationOptional: "组织（可选）",
        newApiKey: "新 API Key（留空不改）",
        lastStatus: "状态",
        cooldown: "冷却中",
        cooldownUntil: "冷却到",
        cooldownRemaining: "剩余冷却",
        rateLimitCount: "限流次数",
        lastRateLimit: "最近限流",
        routeCoolingHint: "命中 rate limit 后，模型中心会把当前路由冷却一段时间，并自动切换到下一个可用模型。",
      }
    : {
        subtitle: "Manage providers, model routes, and runtime priority in a single workspace so credential gaps, defaults, and health issues are obvious.",
        openTasks: "Open AI Tasks",
        openInteractions: "Open AI Interactions",
        checkAllModels: "Check All Models",
        providersMetric: "Providers",
        activeProvidersMetric: "Active Providers",
        modelsMetric: "Enabled Models",
        keysMetric: "Configured Keys",
        routesMetric: "Runtime Routes",
        defaultsMetric: "Default Models",
        providerFormTitle: "New API Provider",
        providerFormDesc: "Register endpoint and credentials first, then attach models to that provider.",
        modelFormTitle: "New Model Route",
        modelFormDesc: "Add multiple models to one provider and control selection order with priority.",
        providerFormHint: "Use a stable provider_key to identify environments, for example openai-prod or qwen-cn-primary.",
        modelFormHint: "Lower priority values win first. Keep only one default model where possible.",
        providerKey: "Provider Key",
        providerName: "Provider Name",
        displayName: "Display Name",
        apiStyle: "API Style",
        baseUrl: "Base URL",
        apiKey: "API Key",
        priority: "Priority",
        createProvider: "Create Provider",
        createModel: "Create Model",
        selectProvider: "Select Provider",
        modelName: "Model Name",
        workspaceFilters: "Workspace Filters",
        workspaceFiltersDesc: "Filter providers, models, and runtime chains by keyword and health state.",
        searchPlaceholder: "Search provider, model, base url, or route key",
        all: "All",
        healthy: "Healthy",
        attention: "Needs Attention",
        checking: "Checking",
        filteredProviders: "Filtered providers",
        filteredModels: "Filtered models",
        filteredRoutes: "Filtered routes",
        providersTitle: "API Providers",
        providersDesc: "Inspect connectivity, credentials, and base endpoints.",
        modelsTitle: "Model Routes",
        modelsDesc: "Review default routes, enablement state, and runtime parameters.",
        runtimeTitle: "Runtime Routing Chain",
        runtimeDesc: "The system tries routes from top to bottom based on priority.",
        active: "active",
        inactive: "inactive",
        enabled: "enabled",
        disabled: "disabled",
        defaultModel: "default",
        providerCount: "Models",
        credential: "Credential",
        noCredential: "Not configured",
        lastCheck: "Last check",
        lastMessage: "Last message",
        organization: "Organization",
        test: "Test",
        edit: "Edit",
        disable: "Disable",
        enable: "Enable",
        save: "Save",
        cancel: "Cancel",
        clearApiKey: "Clear current API Key",
        providerIsActive: "Provider is active",
        modelType: "Model Type",
        modelTypePlaceholder: "Model type (chat/text)",
        inheritProvider: "Inherit Provider",
        temperature: "Temperature",
        maxTokens: "Max Tokens",
        modelKey: "Model Key",
        routeCoverage: "Ready route coverage",
        routeReady: "Key ready",
        routable: "Routable",
        preferredRoute: "Preferred",
        noProviders: "No providers match the current filter.",
        noModels: "No models match the current filter.",
        noRoutes: "No runtime routes match the current filter.",
        loadingProviders: "Loading providers...",
        loadingModels: "Loading models...",
        routeHint: "Routes without an API key are visible here but not actually ready to serve traffic.",
        loadingRoutes: "Loading runtime routes...",
        runtimeErrorTitle: "Failed to load runtime routes",
        setDefault: "Set Default",
        modelEnabled: "Model is enabled",
        setDefaultModel: "Set as default model",
        temperaturePlaceholder: "Temperature (empty for default)",
        maxTokensPlaceholder: "Max tokens (empty for default)",
        organizationOptional: "Organization (optional)",
        newApiKey: "New API Key (leave blank to keep)",
        lastStatus: "Status",
        cooldown: "Cooling",
        cooldownUntil: "Cooldown Until",
        cooldownRemaining: "Cooldown Remaining",
        rateLimitCount: "Rate-limit Count",
        lastRateLimit: "Last Rate-limit",
        routeCoolingHint: "When a route hits a rate limit, model center cools it down for a while and automatically moves to the next available model.",
      };

  const providerOptions = useMemo(
    () =>
      (providers ?? [])
        .slice()
        .sort((a, b) => a.priority - b.priority || a.display_name.localeCompare(b.display_name))
        .map((p) => ({ id: p.id, label: `${p.display_name} (${p.provider_key})` })),
    [providers],
  );

  const modelCountByProvider = useMemo(() => {
    const counter = new Map<number, number>();
    (models ?? []).forEach((model) => {
      counter.set(model.provider_id, (counter.get(model.provider_id) ?? 0) + 1);
    });
    return counter;
  }, [models]);

  const summary = useMemo(() => {
    const totalProviders = providers?.length ?? 0;
    const activeProviders = (providers ?? []).filter((provider) => provider.is_active).length;
    const configuredKeys = (providers ?? []).filter((provider) => provider.has_api_key).length;
    const enabledModels = (models ?? []).filter((model) => model.is_enabled).length;
    const defaultModels = (models ?? []).filter((model) => model.is_default).length;
    const runtimeReady = (runtimeRoutes ?? []).filter((route) => route.available_for_routing).length;
    return {
      totalProviders,
      activeProviders,
      configuredKeys,
      enabledModels,
      defaultModels,
      totalRoutes: runtimeRoutes?.length ?? 0,
      runtimeReady,
      routeCoverage: runtimeRoutes && runtimeRoutes.length > 0
        ? Math.round((runtimeReady / runtimeRoutes.length) * 100)
        : 0,
    };
  }, [models, providers, runtimeRoutes]);

  const searchQuery = search.trim().toLowerCase();

  const filteredProviders = useMemo(
    () =>
      (providers ?? [])
        .filter((provider) =>
          matchesQuery(
            [provider.display_name, provider.provider_key, provider.provider_name, provider.base_url, provider.api_style],
            searchQuery,
          ),
        )
        .filter((provider) => providerMatchesFilter(provider, healthFilter))
        .sort(
          (a, b) =>
            Number(b.is_active) - Number(a.is_active) ||
            a.priority - b.priority ||
            a.display_name.localeCompare(b.display_name),
        ),
    [healthFilter, providers, searchQuery],
  );

  const filteredModels = useMemo(
    () =>
      (models ?? [])
        .filter((model) =>
          matchesQuery(
            [model.display_name, model.model_name, model.model_key, model.provider_key, model.provider_name, model.api_style],
            searchQuery,
          ),
        )
        .filter((model) => modelMatchesFilter(model, healthFilter))
        .sort(
          (a, b) =>
            Number(b.is_default) - Number(a.is_default) ||
            Number(b.is_enabled) - Number(a.is_enabled) ||
            a.priority - b.priority ||
            a.display_name.localeCompare(b.display_name),
        ),
    [healthFilter, models, searchQuery],
  );

  const filteredRuntimeRoutes = useMemo(
    () =>
      (runtimeRoutes ?? [])
        .filter((route) =>
          matchesQuery(
            [route.model_name, route.model_key, route.provider_key, route.provider_name, route.api_style, route.base_url],
            searchQuery,
          ),
        )
        .filter((route) => routeMatchesFilter(route, healthFilter))
        .sort((a, b) => (a.priority ?? Number.MAX_SAFE_INTEGER) - (b.priority ?? Number.MAX_SAFE_INTEGER)),
    [healthFilter, runtimeRoutes, searchQuery],
  );

  const operationError =
    createProvider.error ??
    updateProvider.error ??
    testProvider.error ??
    createModel.error ??
    updateModel.error ??
    testModel.error ??
    checkAll.error;

  const onCreateProvider = (e: FormEvent) => {
    e.preventDefault();
    createProvider.mutate(
      {
        provider_key: providerForm.provider_key.trim(),
        provider_name: providerForm.provider_name.trim(),
        display_name: providerForm.display_name.trim() || providerForm.provider_key.trim(),
        api_style: providerForm.api_style,
        base_url: providerForm.base_url.trim() || null,
        api_key: providerForm.api_key.trim() || null,
        priority: Number(providerForm.priority) || 100,
      },
      {
        onSuccess: () => {
          setProviderForm({
            provider_key: "",
            provider_name: "openai",
            display_name: "",
            api_style: "openai_compatible",
            base_url: "",
            api_key: "",
            priority: 100,
          });
        },
      },
    );
  };

  const onCreateModel = (e: FormEvent) => {
    e.preventDefault();
    if (!modelForm.provider_id) return;

    createModel.mutate(
      {
        provider_id: Number(modelForm.provider_id),
        model_name: modelForm.model_name.trim(),
        display_name: modelForm.display_name.trim() || modelForm.model_name.trim(),
        priority: Number(modelForm.priority) || 100,
      },
      {
        onSuccess: () => {
          setModelForm((old) => ({ ...old, model_name: "", display_name: "", priority: 100 }));
        },
      },
    );
  };

  const startEditProvider = (providerId: number) => {
    const target = (providers ?? []).find((p) => p.id === providerId);
    if (!target) return;

    setEditingProviderId(providerId);
    setProviderEditForm({
      display_name: target.display_name,
      api_style: target.api_style || "openai_compatible",
      base_url: target.base_url || "",
      organization: target.organization || "",
      priority: target.priority,
      api_key_new: "",
      clear_api_key: false,
      is_active: target.is_active,
    });
  };

  const saveProviderEdit = (e: FormEvent) => {
    e.preventDefault();
    if (!editingProviderId) return;

    updateProvider.mutate(
      {
        providerId: editingProviderId,
        payload: {
          display_name: providerEditForm.display_name.trim(),
          api_style: providerEditForm.api_style,
          base_url: providerEditForm.base_url.trim() || null,
          organization: providerEditForm.organization.trim() || null,
          priority: Number(providerEditForm.priority) || 100,
          is_active: providerEditForm.is_active,
          clear_api_key: providerEditForm.clear_api_key,
          ...(providerEditForm.api_key_new.trim()
            ? { api_key: providerEditForm.api_key_new.trim() }
            : {}),
        },
      },
      {
        onSuccess: () => {
          setEditingProviderId(null);
        },
      },
    );
  };

  const startEditModel = (modelId: number) => {
    const target = (models ?? []).find((m) => m.id === modelId);
    if (!target) return;

    setEditingModelId(modelId);
    setModelEditForm({
      display_name: target.display_name,
      model_type: target.model_type || "chat",
      api_style: target.api_style || "",
      temperature: target.temperature == null ? "" : String(target.temperature),
      max_tokens: target.max_tokens == null ? "" : String(target.max_tokens),
      priority: target.priority,
      is_enabled: target.is_enabled,
      is_default: target.is_default,
    });
  };

  const saveModelEdit = (e: FormEvent) => {
    e.preventDefault();
    if (!editingModelId) return;

    updateModel.mutate(
      {
        modelId: editingModelId,
        payload: {
          display_name: modelEditForm.display_name.trim(),
          model_type: modelEditForm.model_type.trim() || "chat",
          api_style: modelEditForm.api_style.trim() || null,
          temperature: modelEditForm.temperature.trim() === "" ? null : Number(modelEditForm.temperature),
          max_tokens: modelEditForm.max_tokens.trim() === "" ? null : Number(modelEditForm.max_tokens),
          priority: Number(modelEditForm.priority) || 100,
          is_enabled: modelEditForm.is_enabled,
          is_default: modelEditForm.is_default,
        },
      },
      {
        onSuccess: () => {
          setEditingModelId(null);
        },
      },
    );
  };

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <Card className="overflow-hidden border border-violet-200/60 bg-gradient-to-br from-violet-50 via-white to-sky-50 shadow-sm dark:border-violet-900/40 dark:from-slate-950 dark:via-slate-950 dark:to-slate-900">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl space-y-3">
            <Badge color="violet" className="w-fit">{t(lang, "mod_ai")}</Badge>
            <div className="space-y-2">
              <Title className="text-3xl tracking-tight">{t(lang, "ai_models")}</Title>
              <Text className="max-w-2xl text-base leading-7">{copy.subtitle}</Text>
            </div>
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Link
                href="/ai/tasks"
                className="inline-flex items-center gap-1.5 rounded-xl border border-violet-300/70 bg-violet-50 px-3 py-2 text-xs font-medium text-violet-700 transition hover:bg-violet-100 dark:border-violet-800 dark:bg-violet-950/25 dark:text-violet-300"
              >
                <ListTodo className="h-3.5 w-3.5" />
                {copy.openTasks}
              </Link>
              <Link
                href="/ai/interactions"
                className="inline-flex items-center gap-1.5 rounded-xl border border-sky-300/70 bg-sky-50 px-3 py-2 text-xs font-medium text-sky-700 transition hover:bg-sky-100 dark:border-sky-900 dark:bg-sky-950/25 dark:text-sky-300"
              >
                <MessageSquareText className="h-3.5 w-3.5" />
                {copy.openInteractions}
              </Link>
              <button
                onClick={() => checkAll.mutate()}
                disabled={checkAll.isPending}
                className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-300/70 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-60 dark:border-emerald-900 dark:bg-emerald-950/25 dark:text-emerald-300"
              >
                {checkAll.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                {copy.checkAllModels}
              </button>
            </div>
            {operationError && (
              <div className="rounded-2xl border border-rose-200 bg-rose-50/90 px-4 py-3 dark:border-rose-900/60 dark:bg-rose-950/30">
                <Text className="text-sm text-rose-700 dark:text-rose-300">{mutationErrorText(operationError, lang)}</Text>
              </div>
            )}
          </div>

          <div className="grid flex-1 gap-3 sm:grid-cols-2 xl:max-w-2xl">
            {[
              {
                title: copy.providersMetric,
                value: summary.totalProviders,
                detail: `${summary.activeProviders} ${copy.activeProvidersMetric}`,
                icon: ShieldCheck,
                tone: "bg-violet-100 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300",
              },
              {
                title: copy.modelsMetric,
                value: summary.enabledModels,
                detail: `${summary.defaultModels} ${copy.defaultsMetric}`,
                icon: Cpu,
                tone: "bg-sky-100 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300",
              },
              {
                title: copy.keysMetric,
                value: summary.configuredKeys,
                detail: `${summary.totalProviders} ${copy.providersMetric}`,
                icon: KeyRound,
                tone: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
              },
              {
                title: copy.routesMetric,
                value: summary.totalRoutes,
                detail: `${summary.routeCoverage}% ${copy.routeCoverage}`,
                icon: GitBranch,
                tone: "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
              },
            ].map((item) => {
              const Icon = item.icon;
              return (
                <div
                  key={item.title}
                  className="rounded-2xl border border-white/70 bg-white/80 p-4 shadow-sm backdrop-blur dark:border-white/5 dark:bg-white/5"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <Text className="text-[11px] uppercase tracking-[0.18em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {item.title}
                      </Text>
                      <Metric className="mt-2">{item.value}</Metric>
                      <Text className="mt-1 text-xs text-tremor-content dark:text-dark-tremor-content">{item.detail}</Text>
                    </div>
                    <div className={cn("rounded-2xl p-2.5", item.tone)}>
                      <Icon className="h-5 w-5" />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </Card>

      <Grid numItems={1} numItemsLg={2} className="gap-4">
        <Card className="border border-tremor-border/80 shadow-sm dark:border-dark-tremor-border/80">
          <div className="flex items-start justify-between gap-3">
            <div>
              <Title className="text-lg">{copy.providerFormTitle}</Title>
              <Text className="mt-1">{copy.providerFormDesc}</Text>
            </div>
            <div className="rounded-2xl bg-violet-100 p-2.5 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">
              <Plus className="h-5 w-5" />
            </div>
          </div>

          <form className="mt-5 grid gap-3 md:grid-cols-2" onSubmit={onCreateProvider}>
            <input
              className={inputCls}
              placeholder={`${copy.providerKey} (e.g. openai-prod)`}
              value={providerForm.provider_key}
              onChange={(e) => setProviderForm((s) => ({ ...s, provider_key: e.target.value }))}
              required
            />
            <input
              className={inputCls}
              placeholder={`${copy.providerName} (openai/qianwen/glm/anthropic/custom)`}
              value={providerForm.provider_name}
              onChange={(e) => setProviderForm((s) => ({ ...s, provider_name: e.target.value }))}
              required
            />
            <input
              className={inputCls}
              placeholder={copy.displayName}
              value={providerForm.display_name}
              onChange={(e) => setProviderForm((s) => ({ ...s, display_name: e.target.value }))}
            />
            <select
              className={inputCls}
              value={providerForm.api_style}
              onChange={(e) => setProviderForm((s) => ({ ...s, api_style: e.target.value }))}
            >
              <option value="openai_compatible">openai_compatible</option>
              <option value="anthropic">anthropic</option>
            </select>
            <input
              className={cn(inputCls, "md:col-span-2")}
              placeholder={copy.baseUrl}
              value={providerForm.base_url}
              onChange={(e) => setProviderForm((s) => ({ ...s, base_url: e.target.value }))}
            />
            <input
              className={cn(inputCls, "md:col-span-2")}
              type="password"
              placeholder={copy.apiKey}
              value={providerForm.api_key}
              onChange={(e) => setProviderForm((s) => ({ ...s, api_key: e.target.value }))}
            />
            <input
              className={inputCls}
              type="number"
              placeholder={copy.priority}
              value={providerForm.priority}
              onChange={(e) => setProviderForm((s) => ({ ...s, priority: Number(e.target.value) }))}
            />
            <div className="rounded-2xl border border-dashed border-tremor-border p-3 dark:border-dark-tremor-border">
              <Text className="text-xs leading-6">{copy.providerFormHint}</Text>
            </div>
            <div className="md:col-span-2 flex flex-wrap items-center gap-3 pt-1">
              <button
                type="submit"
                disabled={createProvider.isPending}
                className="inline-flex items-center gap-1.5 rounded-xl bg-violet-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-violet-700 disabled:opacity-60"
              >
                <Plus className="h-3.5 w-3.5" />
                {copy.createProvider}
              </button>
            </div>
          </form>
        </Card>

        <Card className="border border-tremor-border/80 shadow-sm dark:border-dark-tremor-border/80">
          <div className="flex items-start justify-between gap-3">
            <div>
              <Title className="text-lg">{copy.modelFormTitle}</Title>
              <Text className="mt-1">{copy.modelFormDesc}</Text>
            </div>
            <div className="rounded-2xl bg-sky-100 p-2.5 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
              <Cpu className="h-5 w-5" />
            </div>
          </div>

          <form className="mt-5 grid gap-3 md:grid-cols-2" onSubmit={onCreateModel}>
            <select
              className={cn(inputCls, "md:col-span-2")}
              value={modelForm.provider_id || ""}
              onChange={(e) => setModelForm((s) => ({ ...s, provider_id: Number(e.target.value) }))}
              required
            >
              <option value="">{copy.selectProvider}</option>
              {providerOptions.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
            <input
              className={inputCls}
              placeholder={`${copy.modelName} (e.g. qwen-plus)`}
              value={modelForm.model_name}
              onChange={(e) => setModelForm((s) => ({ ...s, model_name: e.target.value }))}
              required
            />
            <input
              className={inputCls}
              placeholder={copy.displayName}
              value={modelForm.display_name}
              onChange={(e) => setModelForm((s) => ({ ...s, display_name: e.target.value }))}
            />
            <input
              className={inputCls}
              type="number"
              placeholder={copy.priority}
              value={modelForm.priority}
              onChange={(e) => setModelForm((s) => ({ ...s, priority: Number(e.target.value) }))}
            />
            <div className="rounded-2xl border border-dashed border-tremor-border p-3 dark:border-dark-tremor-border">
              <Text className="text-xs leading-6">{copy.modelFormHint}</Text>
            </div>
            <div className="md:col-span-2 flex flex-wrap items-center gap-3 pt-1">
              <button
                type="submit"
                disabled={createModel.isPending}
                className="inline-flex items-center gap-1.5 rounded-xl bg-sky-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-sky-700 disabled:opacity-60"
              >
                <Cpu className="h-3.5 w-3.5" />
                {copy.createModel}
              </button>
            </div>
          </form>
        </Card>
      </Grid>

      <Card className="border border-tremor-border/80 shadow-sm dark:border-dark-tremor-border/80">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <Title className="text-lg">{copy.workspaceFilters}</Title>
            <Text className="mt-1">{copy.workspaceFiltersDesc}</Text>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <Badge color="slate">{copy.filteredProviders}: {filteredProviders.length}</Badge>
            <Badge color="blue">{copy.filteredModels}: {filteredModels.length}</Badge>
            <Badge color="emerald">{copy.filteredRoutes}: {filteredRuntimeRoutes.length}</Badge>
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <input
              type="text"
              placeholder={copy.searchPlaceholder}
              className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-emphasis outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {[
              { key: "all", label: copy.all, activeCls: "border-slate-400 bg-slate-100 text-slate-800 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100" },
              { key: "healthy", label: copy.healthy, activeCls: "border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950/35 dark:text-emerald-300" },
              { key: "attention", label: copy.attention, activeCls: "border-rose-400 bg-rose-50 text-rose-700 dark:border-rose-700 dark:bg-rose-950/35 dark:text-rose-300" },
              { key: "checking", label: copy.checking, activeCls: "border-amber-400 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-950/35 dark:text-amber-300" },
            ].map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setHealthFilter(option.key as HealthFilter)}
                className={cn(
                  pillButtonCls,
                  healthFilter === option.key
                    ? option.activeCls
                    : "border-tremor-border bg-white text-tremor-content hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content dark:hover:bg-dark-tremor-background-subtle",
                )}
              >
                {healthFilter === option.key ? <CheckCircle2 className="h-3.5 w-3.5" /> : <CircleSlash className="h-3.5 w-3.5 opacity-70" />}
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </Card>

      <section className="space-y-4" id="providers">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <Title className="text-lg">{copy.providersTitle}</Title>
            <Text className="mt-1">{copy.providersDesc}</Text>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge color="emerald">{summary.activeProviders} {copy.activeProvidersMetric}</Badge>
            <Badge color="blue">{summary.configuredKeys} {copy.keysMetric}</Badge>
          </div>
        </div>

        {loadingProviders ? (
          <Card>
            <Text>{copy.loadingProviders}</Text>
          </Card>
        ) : filteredProviders.length === 0 ? (
          <Card>
            <Text>{copy.noProviders}</Text>
          </Card>
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">
            {filteredProviders.map((provider) => (
              <Card key={provider.id} className="border border-tremor-border/80 shadow-sm dark:border-dark-tremor-border/80">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Title className="text-base">{provider.display_name}</Title>
                      <Badge color="slate">{provider.provider_key}</Badge>
                      <Badge color="blue">{provider.provider_name}</Badge>
                    </div>
                    <Text>{provider.api_style}</Text>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Badge color={provider.is_active ? "emerald" : "rose"}>
                      {provider.is_active ? copy.active : copy.inactive}
                    </Badge>
                    {provider.rate_limit_active && (
                      <Badge color="amber">{copy.cooldown} {formatDuration(provider.rate_limit_remaining_seconds, lang)}</Badge>
                    )}
                    <Badge color={statusColor(provider.last_check_status)}>{provider.last_check_status || "unknown"}</Badge>
                  </div>
                </div>

                <div className="mt-4 grid gap-3 sm:grid-cols-2 2xl:grid-cols-5">
                  <DetailField label={copy.baseUrl} value={provider.base_url || "-"} mono />
                  <DetailField label={copy.credential} value={provider.api_key_hint || copy.noCredential} mono />
                  <DetailField label={copy.priority} value={provider.priority} />
                  <DetailField label={copy.providerCount} value={modelCountByProvider.get(provider.id) ?? 0} />
                  <DetailField
                    label={provider.rate_limit_active ? copy.cooldownUntil : copy.lastCheck}
                    value={provider.rate_limit_active ? formatDateTime(provider.rate_limit_cooldown_until) : formatDateTime(provider.last_checked_at)}
                  />
                </div>

                {provider.rate_limit_count > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <Badge color="amber">{copy.rateLimitCount}: {provider.rate_limit_count}</Badge>
                    {provider.last_rate_limit_at && <Badge color="slate">{copy.lastRateLimit}: {formatDateTime(provider.last_rate_limit_at)}</Badge>}
                  </div>
                )}

                {provider.last_check_message && (
                  <div className="mt-4 rounded-2xl border border-tremor-border/80 bg-tremor-background-subtle/70 px-4 py-3 dark:border-dark-tremor-border/80 dark:bg-dark-tremor-background-subtle/70">
                    <Text className="text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{copy.lastMessage}</Text>
                    <Text className="mt-1 text-sm">{provider.last_check_message}</Text>
                  </div>
                )}

                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <button
                    onClick={() => testProvider.mutate(provider.id)}
                    className="rounded-xl border border-blue-300/70 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 dark:border-blue-900 dark:bg-blue-950/20 dark:text-blue-300"
                  >
                    {copy.test}
                  </button>
                  <button
                    onClick={() => startEditProvider(provider.id)}
                    className="rounded-xl border border-slate-300/70 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700 dark:border-slate-700 dark:bg-slate-900/30 dark:text-slate-300"
                  >
                    {copy.edit}
                  </button>
                  <button
                    onClick={() =>
                      updateProvider.mutate({
                        providerId: provider.id,
                        payload: { is_active: !provider.is_active },
                      })
                    }
                    className="rounded-xl border border-amber-300/70 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300"
                  >
                    {provider.is_active ? copy.disable : copy.enable}
                  </button>
                </div>

                {editingProviderId === provider.id && (
                  <form onSubmit={saveProviderEdit} className="mt-4 grid gap-3 rounded-2xl border border-slate-200 p-4 dark:border-slate-700/80 md:grid-cols-2">
                    <input
                      className={inputCls}
                      placeholder={copy.displayName}
                      value={providerEditForm.display_name}
                      onChange={(e) => setProviderEditForm((s) => ({ ...s, display_name: e.target.value }))}
                      required
                    />
                    <select
                      className={inputCls}
                      value={providerEditForm.api_style}
                      onChange={(e) => setProviderEditForm((s) => ({ ...s, api_style: e.target.value }))}
                    >
                      <option value="openai_compatible">openai_compatible</option>
                      <option value="anthropic">anthropic</option>
                    </select>
                    <input
                      className={cn(inputCls, "md:col-span-2")}
                      placeholder={copy.baseUrl}
                      value={providerEditForm.base_url}
                      onChange={(e) => setProviderEditForm((s) => ({ ...s, base_url: e.target.value }))}
                    />
                    <input
                      className={inputCls}
                      placeholder={copy.organizationOptional}
                      value={providerEditForm.organization}
                      onChange={(e) => setProviderEditForm((s) => ({ ...s, organization: e.target.value }))}
                    />
                    <input
                      className={inputCls}
                      type="number"
                      placeholder={copy.priority}
                      value={providerEditForm.priority}
                      onChange={(e) => setProviderEditForm((s) => ({ ...s, priority: Number(e.target.value) }))}
                    />
                    <input
                      className={cn(inputCls, "md:col-span-2")}
                      type="password"
                      placeholder={copy.newApiKey}
                      value={providerEditForm.api_key_new}
                      onChange={(e) => setProviderEditForm((s) => ({ ...s, api_key_new: e.target.value }))}
                    />
                    <label className="flex items-center gap-2 text-xs text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                      <input
                        type="checkbox"
                        checked={providerEditForm.clear_api_key}
                        onChange={(e) => setProviderEditForm((s) => ({ ...s, clear_api_key: e.target.checked }))}
                      />
                      {copy.clearApiKey}
                    </label>
                    <label className="flex items-center gap-2 text-xs text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                      <input
                        type="checkbox"
                        checked={providerEditForm.is_active}
                        onChange={(e) => setProviderEditForm((s) => ({ ...s, is_active: e.target.checked }))}
                      />
                      {copy.providerIsActive}
                    </label>
                    <div className="md:col-span-2 flex items-center gap-2 pt-1">
                      <button
                        type="submit"
                        disabled={updateProvider.isPending}
                        className="rounded-xl bg-slate-700 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
                      >
                        {copy.save}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingProviderId(null)}
                        className="rounded-xl border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 dark:border-slate-700 dark:text-slate-300"
                      >
                        {copy.cancel}
                      </button>
                    </div>
                  </form>
                )}
              </Card>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-4" id="models">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <Title className="text-lg">{copy.modelsTitle}</Title>
            <Text className="mt-1">{copy.modelsDesc}</Text>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge color="emerald">{summary.enabledModels} {copy.modelsMetric}</Badge>
            <Badge color="blue">{summary.defaultModels} {copy.defaultsMetric}</Badge>
          </div>
        </div>

        {loadingModels ? (
          <Card>
            <Text>{copy.loadingModels}</Text>
          </Card>
        ) : filteredModels.length === 0 ? (
          <Card>
            <Text>{copy.noModels}</Text>
          </Card>
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">
            {filteredModels.map((model) => (
              <Card key={model.id} className="border border-tremor-border/80 shadow-sm dark:border-dark-tremor-border/80">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Title className="text-base">{model.display_name}</Title>
                      <Badge color="slate">{model.model_name}</Badge>
                      <Badge color="blue">{model.provider_key}</Badge>
                    </div>
                    <Text className="text-sm">{model.provider_name}</Text>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    {model.is_default && <Badge color="emerald">{copy.defaultModel}</Badge>}
                    <Badge color={model.is_enabled ? "emerald" : "rose"}>
                      {model.is_enabled ? copy.enabled : copy.disabled}
                    </Badge>
                    {model.rate_limit_active && (
                      <Badge color="amber">{copy.cooldown} {formatDuration(model.rate_limit_remaining_seconds, lang)}</Badge>
                    )}
                    <Badge color={statusColor(model.last_check_status)}>{model.last_check_status || "unknown"}</Badge>
                  </div>
                </div>

                <div className="mt-4 grid gap-3 sm:grid-cols-2 2xl:grid-cols-6">
                  <DetailField label={copy.modelKey} value={model.model_key} mono />
                  <DetailField label={copy.modelType} value={model.model_type || "chat"} />
                  <DetailField label={copy.apiStyle} value={model.api_style || copy.inheritProvider} />
                  <DetailField label={copy.temperature} value={model.temperature == null ? "-" : model.temperature} />
                  <DetailField label={copy.maxTokens} value={model.max_tokens == null ? "-" : model.max_tokens} />
                  <DetailField label={copy.priority} value={model.priority} />
                </div>

                {model.rate_limit_count > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <Badge color="amber">{copy.rateLimitCount}: {model.rate_limit_count}</Badge>
                    {model.last_rate_limit_at && <Badge color="slate">{copy.lastRateLimit}: {formatDateTime(model.last_rate_limit_at)}</Badge>}
                    {model.rate_limit_active && <Badge color="amber">{copy.cooldownUntil}: {formatDateTime(model.rate_limit_cooldown_until)}</Badge>}
                  </div>
                )}

                {model.last_check_message && (
                  <div className="mt-4 rounded-2xl border border-tremor-border/80 bg-tremor-background-subtle/70 px-4 py-3 dark:border-dark-tremor-border/80 dark:bg-dark-tremor-background-subtle/70">
                    <Text className="text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{copy.lastMessage}</Text>
                    <Text className="mt-1 text-sm">{model.last_check_message}</Text>
                  </div>
                )}

                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <button
                    onClick={() => testModel.mutate(model.id)}
                    className="rounded-xl border border-blue-300/70 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 dark:border-blue-900 dark:bg-blue-950/20 dark:text-blue-300"
                  >
                    {copy.test}
                  </button>
                  <button
                    onClick={() => startEditModel(model.id)}
                    className="rounded-xl border border-slate-300/70 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700 dark:border-slate-700 dark:bg-slate-900/30 dark:text-slate-300"
                  >
                    {copy.edit}
                  </button>
                  <button
                    onClick={() =>
                      updateModel.mutate({
                        modelId: model.id,
                        payload: { is_enabled: !model.is_enabled },
                      })
                    }
                    className="rounded-xl border border-amber-300/70 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300"
                  >
                    {model.is_enabled ? copy.disable : copy.enable}
                  </button>
                  {!model.is_default && (
                    <button
                      onClick={() =>
                        updateModel.mutate({
                          modelId: model.id,
                          payload: { is_default: true },
                        })
                      }
                      className="rounded-xl border border-emerald-300/70 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300"
                    >
                      {copy.setDefault}
                    </button>
                  )}
                </div>

                {editingModelId === model.id && (
                  <form onSubmit={saveModelEdit} className="mt-4 grid gap-3 rounded-2xl border border-slate-200 p-4 dark:border-slate-700/80 md:grid-cols-2">
                    <input
                      className={inputCls}
                      placeholder={copy.displayName}
                      value={modelEditForm.display_name}
                      onChange={(e) => setModelEditForm((s) => ({ ...s, display_name: e.target.value }))}
                      required
                    />
                    <input
                      className={inputCls}
                      placeholder={copy.modelTypePlaceholder}
                      value={modelEditForm.model_type}
                      onChange={(e) => setModelEditForm((s) => ({ ...s, model_type: e.target.value }))}
                    />
                    <input
                      className={cn(inputCls, "md:col-span-2")}
                      placeholder={`${copy.apiStyle} (${copy.inheritProvider})`}
                      value={modelEditForm.api_style}
                      onChange={(e) => setModelEditForm((s) => ({ ...s, api_style: e.target.value }))}
                    />
                    <input
                      className={inputCls}
                      type="number"
                      step="0.01"
                      placeholder={copy.temperaturePlaceholder}
                      value={modelEditForm.temperature}
                      onChange={(e) => setModelEditForm((s) => ({ ...s, temperature: e.target.value }))}
                    />
                    <input
                      className={inputCls}
                      type="number"
                      placeholder={copy.maxTokensPlaceholder}
                      value={modelEditForm.max_tokens}
                      onChange={(e) => setModelEditForm((s) => ({ ...s, max_tokens: e.target.value }))}
                    />
                    <input
                      className={inputCls}
                      type="number"
                      placeholder={copy.priority}
                      value={modelEditForm.priority}
                      onChange={(e) => setModelEditForm((s) => ({ ...s, priority: Number(e.target.value) }))}
                    />
                    <div className="rounded-2xl border border-dashed border-tremor-border p-3 dark:border-dark-tremor-border md:col-span-1">
                      <Text className="text-xs leading-6">{copy.modelFormHint}</Text>
                    </div>
                    <label className="flex items-center gap-2 text-xs text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis md:col-span-2">
                      <input
                        type="checkbox"
                        checked={modelEditForm.is_enabled}
                        onChange={(e) => setModelEditForm((s) => ({ ...s, is_enabled: e.target.checked }))}
                      />
                      {copy.modelEnabled}
                    </label>
                    <label className="flex items-center gap-2 text-xs text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis md:col-span-2">
                      <input
                        type="checkbox"
                        checked={modelEditForm.is_default}
                        onChange={(e) => setModelEditForm((s) => ({ ...s, is_default: e.target.checked }))}
                      />
                      {copy.setDefaultModel}
                    </label>
                    <div className="md:col-span-2 flex items-center gap-2 pt-1">
                      <button
                        type="submit"
                        disabled={updateModel.isPending}
                        className="rounded-xl bg-slate-700 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
                      >
                        {copy.save}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingModelId(null)}
                        className="rounded-xl border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 dark:border-slate-700 dark:text-slate-300"
                      >
                        {copy.cancel}
                      </button>
                    </div>
                  </form>
                )}
              </Card>
            ))}
          </div>
        )}
      </section>

      <section id="runtime">
        <Card className="border border-tremor-border/80 shadow-sm dark:border-dark-tremor-border/80">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <Title className="text-lg">{copy.runtimeTitle}</Title>
              <Text className="mt-1">{copy.runtimeDesc}</Text>
            </div>

            <div className="w-full max-w-sm rounded-2xl border border-tremor-border/70 bg-tremor-background-subtle/70 p-4 dark:border-dark-tremor-border/70 dark:bg-dark-tremor-background-subtle/70">
              <div className="flex items-center justify-between text-xs font-medium text-tremor-content dark:text-dark-tremor-content">
                <span>{copy.routeCoverage}</span>
                <span>{summary.routeCoverage}%</span>
              </div>
              <ProgressBar
                value={summary.routeCoverage}
                color={summary.routeCoverage >= 100 ? "emerald" : summary.routeCoverage >= 60 ? "amber" : "rose"}
                className="mt-3"
              />
              <Text className="mt-2 text-xs">
                {summary.runtimeReady}/{summary.totalRoutes} {copy.routeReady}
              </Text>
            </div>
          </div>

          <div className="mt-4 space-y-3">
            {loadingRuntimeRoutes ? (
              <Text>{copy.loadingRoutes}</Text>
            ) : runtimeRoutesError ? (
              <div className="rounded-2xl border border-rose-200 bg-rose-50/90 px-4 py-3 dark:border-rose-900/60 dark:bg-rose-950/30">
                <Text className="text-sm font-medium text-rose-700 dark:text-rose-300">{copy.runtimeErrorTitle}</Text>
                <Text className="mt-1 text-sm text-rose-700 dark:text-rose-300">{queryErrorText(runtimeRoutesError, lang)}</Text>
              </div>
            ) : filteredRuntimeRoutes.length === 0 ? (
              <Text>{copy.noRoutes}</Text>
            ) : (
              filteredRuntimeRoutes.map((route, index) => (
                <div
                  key={route.model_key}
                  className="rounded-2xl border border-tremor-border/80 bg-white/80 px-4 py-4 shadow-sm dark:border-dark-tremor-border/80 dark:bg-white/5"
                >
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex items-start gap-3">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-900 text-sm font-semibold text-white dark:bg-slate-100 dark:text-slate-900">
                        {index + 1}
                      </div>
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <Title className="text-base">{route.model_name}</Title>
                          {index === 0 && <Badge color="emerald">{copy.preferredRoute}</Badge>}
                          <Badge color="blue">{route.provider_key}</Badge>
                          <Badge color="slate">{route.api_style}</Badge>
                        </div>
                        <Text className="mt-1">{route.provider_name}</Text>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <Badge color={route.has_api_key ? "emerald" : "rose"}>
                        {route.has_api_key ? copy.routeReady : copy.noCredential}
                      </Badge>
                      <Badge color={route.available_for_routing ? "emerald" : route.rate_limit_active ? "amber" : "slate"}>
                        {route.available_for_routing ? copy.routable : route.rate_limit_active ? copy.cooldown : copy.disabled}
                      </Badge>
                      {route.rate_limit_active && (
                        <Badge color="amber">{formatDuration(route.rate_limit_remaining_seconds, lang)}</Badge>
                      )}
                      {route.priority != null && <Badge color="amber">P{route.priority}</Badge>}
                    </div>
                  </div>

                  <div className="mt-4 grid gap-3 md:grid-cols-4">
                    <DetailField label={copy.modelKey} value={route.model_key} mono />
                    <DetailField label={copy.baseUrl} value={route.base_url || "-"} mono />
                    <DetailField label={copy.credential} value={route.api_key_hint || copy.noCredential} mono />
                    <DetailField label={copy.lastStatus} value={route.last_check_status || "unknown"} />
                  </div>

                  {route.rate_limit_count > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2 text-xs">
                      <Badge color="amber">{copy.rateLimitCount}: {route.rate_limit_count}</Badge>
                      {route.last_rate_limit_at && <Badge color="slate">{copy.lastRateLimit}: {formatDateTime(route.last_rate_limit_at)}</Badge>}
                      {route.rate_limit_active && <Badge color="amber">{copy.cooldownUntil}: {formatDateTime(route.rate_limit_cooldown_until)}</Badge>}
                    </div>
                  )}

                  <div className="mt-4 inline-flex items-center gap-2 rounded-full bg-tremor-background-subtle px-3 py-1 text-xs text-tremor-content dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content">
                    <GitBranch className="h-3.5 w-3.5" />
                    {route.rate_limit_active ? copy.routeCoolingHint : copy.routeHint}
                    <ArrowRight className="h-3.5 w-3.5 opacity-70" />
                  </div>
                </div>
              ))
            )}
          </div>
        </Card>
      </section>
    </div>
  );
}
