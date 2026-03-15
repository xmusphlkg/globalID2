"use client";

import Link from "next/link";
import { FormEvent, useMemo, useState } from "react";
import { Badge, Card, Grid, Text, Title } from "@tremor/react";
import { Cpu, ListTodo, MessageSquareText, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import {
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

function statusColor(status: string): "slate" | "emerald" | "rose" | "amber" {
  const s = (status || "").toLowerCase();
  if (s === "available") return "emerald";
  if (s === "unavailable" || s === "failed") return "rose";
  if (s === "checking") return "amber";
  return "slate";
}

export default function AIModelsPage() {
  const { lang } = useAppStore();

  const { data: providers, isLoading: loadingProviders } = useAIProviders();
  const { data: models, isLoading: loadingModels } = useAIModels();
  const { data: runtimeRoutes } = useAIRuntimeRoutes();

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

  const providerOptions = useMemo(
    () => (providers ?? []).map((p) => ({ id: p.id, label: `${p.display_name} (${p.provider_key})` })),
    [providers],
  );

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
      <div className="space-y-2">
        <Badge color="violet" className="w-fit">{t(lang, "mod_ai")}</Badge>
        <Title className="text-2xl">{t(lang, "ai_models")}</Title>
        <Text>{lang === "zh" ? "统一管理多厂商 API、模型路由和可用性状态" : "Unified management for multi-provider APIs, model routing, and health states"}</Text>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Link
            href="/ai/tasks"
            className="inline-flex items-center gap-1 rounded-lg border border-violet-300/70 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:bg-violet-100 dark:border-violet-800 dark:bg-violet-950/25 dark:text-violet-300"
          >
            <ListTodo className="h-3.5 w-3.5" />
            Open AI Tasks
          </Link>
          <Link
            href="/ai/interactions"
            className="inline-flex items-center gap-1 rounded-lg border border-violet-300/70 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:bg-violet-100 dark:border-violet-800 dark:bg-violet-950/25 dark:text-violet-300"
          >
            <MessageSquareText className="h-3.5 w-3.5" />
            Open AI Interactions
          </Link>
          <button
            onClick={() => checkAll.mutate()}
            disabled={checkAll.isPending}
            className="inline-flex items-center gap-1 rounded-lg border border-emerald-300/70 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-60 dark:border-emerald-900 dark:bg-emerald-950/25 dark:text-emerald-300"
          >
            {checkAll.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
            {lang === "zh" ? "检查全部模型" : "Check All Models"}
          </button>
        </div>
      </div>

      <Grid numItems={1} numItemsLg={2} className="gap-4">
        <Card>
          <Title className="mb-3">{lang === "zh" ? "新增 API 提供商" : "New API Provider"}</Title>
          <form className="space-y-2" onSubmit={onCreateProvider}>
            <input className={inputCls} placeholder="provider_key (e.g. openai-prod)" value={providerForm.provider_key} onChange={(e) => setProviderForm((s) => ({ ...s, provider_key: e.target.value }))} required />
            <input className={inputCls} placeholder="provider_name (openai/qianwen/glm/anthropic/custom)" value={providerForm.provider_name} onChange={(e) => setProviderForm((s) => ({ ...s, provider_name: e.target.value }))} required />
            <input className={inputCls} placeholder={lang === "zh" ? "显示名称" : "Display name"} value={providerForm.display_name} onChange={(e) => setProviderForm((s) => ({ ...s, display_name: e.target.value }))} />
            <select className={inputCls} value={providerForm.api_style} onChange={(e) => setProviderForm((s) => ({ ...s, api_style: e.target.value }))}>
              <option value="openai_compatible">openai_compatible</option>
              <option value="anthropic">anthropic</option>
            </select>
            <input className={inputCls} placeholder="Base URL" value={providerForm.base_url} onChange={(e) => setProviderForm((s) => ({ ...s, base_url: e.target.value }))} />
            <input className={inputCls} placeholder="API Key" value={providerForm.api_key} onChange={(e) => setProviderForm((s) => ({ ...s, api_key: e.target.value }))} />
            <input className={inputCls} type="number" placeholder="Priority" value={providerForm.priority} onChange={(e) => setProviderForm((s) => ({ ...s, priority: Number(e.target.value) }))} />
            <button type="submit" disabled={createProvider.isPending} className="inline-flex items-center gap-1 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-violet-700 disabled:opacity-60">
              <Plus className="h-3.5 w-3.5" />
              {lang === "zh" ? "新增提供商" : "Create Provider"}
            </button>
          </form>
          {createProvider.error && <Text className="mt-2 text-xs text-rose-600">{String(createProvider.error)}</Text>}
        </Card>

        <Card>
          <Title className="mb-3">{lang === "zh" ? "新增模型路由" : "New Model Route"}</Title>
          <form className="space-y-2" onSubmit={onCreateModel}>
            <select className={inputCls} value={modelForm.provider_id || ""} onChange={(e) => setModelForm((s) => ({ ...s, provider_id: Number(e.target.value) }))} required>
              <option value="">{lang === "zh" ? "选择提供商" : "Select provider"}</option>
              {providerOptions.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
            <input className={inputCls} placeholder="model_name (e.g. qwen-plus)" value={modelForm.model_name} onChange={(e) => setModelForm((s) => ({ ...s, model_name: e.target.value }))} required />
            <input className={inputCls} placeholder={lang === "zh" ? "显示名称" : "Display name"} value={modelForm.display_name} onChange={(e) => setModelForm((s) => ({ ...s, display_name: e.target.value }))} />
            <input className={inputCls} type="number" placeholder="Priority" value={modelForm.priority} onChange={(e) => setModelForm((s) => ({ ...s, priority: Number(e.target.value) }))} />
            <button type="submit" disabled={createModel.isPending} className="inline-flex items-center gap-1 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-sky-700 disabled:opacity-60">
              <Cpu className="h-3.5 w-3.5" />
              {lang === "zh" ? "新增模型" : "Create Model"}
            </button>
          </form>
          {createModel.error && <Text className="mt-2 text-xs text-rose-600">{String(createModel.error)}</Text>}
        </Card>
      </Grid>

      <Grid numItems={1} numItemsLg={2} className="gap-4">
        <Card>
          <Title className="mb-3">{lang === "zh" ? "API 提供商" : "API Providers"}</Title>
          {loadingProviders ? (
            <Text>{lang === "zh" ? "加载中..." : "Loading..."}</Text>
          ) : (
            <div className="space-y-2">
              {(providers ?? []).map((p) => (
                <div key={p.id} className="rounded-tremor-default border border-tremor-border p-3 dark:border-dark-tremor-border">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge color="slate">{p.provider_key}</Badge>
                    <Badge color={p.is_active ? "emerald" : "rose"}>{p.is_active ? "active" : "inactive"}</Badge>
                    <Badge color={statusColor(p.last_check_status)}>{p.last_check_status}</Badge>
                  </div>
                  <Text className="mt-2 text-xs">{p.display_name} ({p.provider_name})</Text>
                  <Text className="mt-1 text-xs">{p.base_url || "-"}</Text>
                  <Text className="mt-1 text-xs">key: {p.api_key_hint || "(none)"}</Text>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => testProvider.mutate(p.id)}
                      className="rounded-md border border-blue-300/70 bg-blue-50 px-2 py-1 text-xs text-blue-700 dark:border-blue-900 dark:bg-blue-950/20 dark:text-blue-300"
                    >
                      {lang === "zh" ? "测试" : "Test"}
                    </button>
                    <button
                      onClick={() => startEditProvider(p.id)}
                      className="rounded-md border border-slate-300/70 bg-slate-50 px-2 py-1 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900/30 dark:text-slate-300"
                    >
                      {lang === "zh" ? "编辑" : "Edit"}
                    </button>
                    <button
                      onClick={() =>
                        updateProvider.mutate({
                          providerId: p.id,
                          payload: { is_active: !p.is_active },
                        })
                      }
                      className="rounded-md border border-amber-300/70 bg-amber-50 px-2 py-1 text-xs text-amber-700 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300"
                    >
                      {p.is_active ? (lang === "zh" ? "停用" : "Disable") : (lang === "zh" ? "启用" : "Enable")}
                    </button>
                  </div>
                  {editingProviderId === p.id && (
                    <form onSubmit={saveProviderEdit} className="mt-3 space-y-2 rounded-md border border-slate-200 p-3 dark:border-slate-700/80">
                      <input
                        className={inputCls}
                        placeholder={lang === "zh" ? "显示名称" : "Display Name"}
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
                        className={inputCls}
                        placeholder="Base URL"
                        value={providerEditForm.base_url}
                        onChange={(e) => setProviderEditForm((s) => ({ ...s, base_url: e.target.value }))}
                      />
                      <input
                        className={inputCls}
                        placeholder={lang === "zh" ? "组织（可选）" : "Organization (optional)"}
                        value={providerEditForm.organization}
                        onChange={(e) => setProviderEditForm((s) => ({ ...s, organization: e.target.value }))}
                      />
                      <input
                        className={inputCls}
                        type="number"
                        placeholder="Priority"
                        value={providerEditForm.priority}
                        onChange={(e) => setProviderEditForm((s) => ({ ...s, priority: Number(e.target.value) }))}
                      />
                      <input
                        className={inputCls}
                        placeholder={lang === "zh" ? "新 API Key（留空不改）" : "New API Key (leave blank to keep)"}
                        value={providerEditForm.api_key_new}
                        onChange={(e) => setProviderEditForm((s) => ({ ...s, api_key_new: e.target.value }))}
                      />
                      <label className="flex items-center gap-2 text-xs text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                        <input
                          type="checkbox"
                          checked={providerEditForm.clear_api_key}
                          onChange={(e) => setProviderEditForm((s) => ({ ...s, clear_api_key: e.target.checked }))}
                        />
                        {lang === "zh" ? "清空当前 API Key" : "Clear current API Key"}
                      </label>
                      <label className="flex items-center gap-2 text-xs text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                        <input
                          type="checkbox"
                          checked={providerEditForm.is_active}
                          onChange={(e) => setProviderEditForm((s) => ({ ...s, is_active: e.target.checked }))}
                        />
                        {lang === "zh" ? "启用该提供商" : "Provider is active"}
                      </label>
                      <div className="flex items-center gap-2">
                        <button
                          type="submit"
                          disabled={updateProvider.isPending}
                          className="rounded-md bg-slate-700 px-2 py-1 text-xs text-white disabled:opacity-60"
                        >
                          {lang === "zh" ? "保存" : "Save"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingProviderId(null)}
                          className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-700 dark:border-slate-700 dark:text-slate-300"
                        >
                          {lang === "zh" ? "取消" : "Cancel"}
                        </button>
                      </div>
                    </form>
                  )}
                  {p.last_check_message && <Text className="mt-1 text-[11px] text-tremor-content-subtle">{p.last_check_message}</Text>}
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <Title className="mb-3">{lang === "zh" ? "模型路由" : "Model Routes"}</Title>
          {loadingModels ? (
            <Text>{lang === "zh" ? "加载中..." : "Loading..."}</Text>
          ) : (
            <div className="space-y-2">
              {(models ?? []).map((m) => (
                <div key={m.id} className="rounded-tremor-default border border-tremor-border p-3 dark:border-dark-tremor-border">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge color="slate">{m.model_name}</Badge>
                    <Badge color="blue">{m.provider_key}</Badge>
                    {m.is_default && <Badge color="emerald">default</Badge>}
                    <Badge color={m.is_enabled ? "emerald" : "rose"}>{m.is_enabled ? "enabled" : "disabled"}</Badge>
                    <Badge color={statusColor(m.last_check_status)}>{m.last_check_status}</Badge>
                  </div>
                  <Text className="mt-2 text-xs">{m.display_name}</Text>
                  <Text className="mt-1 text-xs">{m.model_key}</Text>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => testModel.mutate(m.id)}
                      className="rounded-md border border-blue-300/70 bg-blue-50 px-2 py-1 text-xs text-blue-700 dark:border-blue-900 dark:bg-blue-950/20 dark:text-blue-300"
                    >
                      {lang === "zh" ? "测试" : "Test"}
                    </button>
                    <button
                      onClick={() => startEditModel(m.id)}
                      className="rounded-md border border-slate-300/70 bg-slate-50 px-2 py-1 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900/30 dark:text-slate-300"
                    >
                      {lang === "zh" ? "编辑" : "Edit"}
                    </button>
                    <button
                      onClick={() =>
                        updateModel.mutate({
                          modelId: m.id,
                          payload: { is_enabled: !m.is_enabled },
                        })
                      }
                      className="rounded-md border border-amber-300/70 bg-amber-50 px-2 py-1 text-xs text-amber-700 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300"
                    >
                      {m.is_enabled ? (lang === "zh" ? "停用" : "Disable") : (lang === "zh" ? "启用" : "Enable")}
                    </button>
                    {!m.is_default && (
                      <button
                        onClick={() =>
                          updateModel.mutate({
                            modelId: m.id,
                            payload: { is_default: true },
                          })
                        }
                        className="rounded-md border border-emerald-300/70 bg-emerald-50 px-2 py-1 text-xs text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300"
                      >
                        {lang === "zh" ? "设为默认" : "Set Default"}
                      </button>
                    )}
                  </div>
                  {editingModelId === m.id && (
                    <form onSubmit={saveModelEdit} className="mt-3 space-y-2 rounded-md border border-slate-200 p-3 dark:border-slate-700/80">
                      <input
                        className={inputCls}
                        placeholder={lang === "zh" ? "显示名称" : "Display Name"}
                        value={modelEditForm.display_name}
                        onChange={(e) => setModelEditForm((s) => ({ ...s, display_name: e.target.value }))}
                        required
                      />
                      <input
                        className={inputCls}
                        placeholder={lang === "zh" ? "模型类型（chat/text）" : "Model type (chat/text)"}
                        value={modelEditForm.model_type}
                        onChange={(e) => setModelEditForm((s) => ({ ...s, model_type: e.target.value }))}
                      />
                      <input
                        className={inputCls}
                        placeholder={lang === "zh" ? "API 风格（留空继承 Provider）" : "API style (empty to inherit provider)"}
                        value={modelEditForm.api_style}
                        onChange={(e) => setModelEditForm((s) => ({ ...s, api_style: e.target.value }))}
                      />
                      <input
                        className={inputCls}
                        type="number"
                        step="0.01"
                        placeholder={lang === "zh" ? "温度（留空使用默认）" : "Temperature (empty for default)"}
                        value={modelEditForm.temperature}
                        onChange={(e) => setModelEditForm((s) => ({ ...s, temperature: e.target.value }))}
                      />
                      <input
                        className={inputCls}
                        type="number"
                        placeholder={lang === "zh" ? "最大 token（留空使用默认）" : "Max tokens (empty for default)"}
                        value={modelEditForm.max_tokens}
                        onChange={(e) => setModelEditForm((s) => ({ ...s, max_tokens: e.target.value }))}
                      />
                      <input
                        className={inputCls}
                        type="number"
                        placeholder="Priority"
                        value={modelEditForm.priority}
                        onChange={(e) => setModelEditForm((s) => ({ ...s, priority: Number(e.target.value) }))}
                      />
                      <label className="flex items-center gap-2 text-xs text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                        <input
                          type="checkbox"
                          checked={modelEditForm.is_enabled}
                          onChange={(e) => setModelEditForm((s) => ({ ...s, is_enabled: e.target.checked }))}
                        />
                        {lang === "zh" ? "启用该模型" : "Model is enabled"}
                      </label>
                      <label className="flex items-center gap-2 text-xs text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                        <input
                          type="checkbox"
                          checked={modelEditForm.is_default}
                          onChange={(e) => setModelEditForm((s) => ({ ...s, is_default: e.target.checked }))}
                        />
                        {lang === "zh" ? "设为默认模型" : "Set as default model"}
                      </label>
                      <div className="flex items-center gap-2">
                        <button
                          type="submit"
                          disabled={updateModel.isPending}
                          className="rounded-md bg-slate-700 px-2 py-1 text-xs text-white disabled:opacity-60"
                        >
                          {lang === "zh" ? "保存" : "Save"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingModelId(null)}
                          className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-700 dark:border-slate-700 dark:text-slate-300"
                        >
                          {lang === "zh" ? "取消" : "Cancel"}
                        </button>
                      </div>
                    </form>
                  )}
                  {m.last_check_message && <Text className="mt-1 text-[11px] text-tremor-content-subtle">{m.last_check_message}</Text>}
                </div>
              ))}
            </div>
          )}
        </Card>
      </Grid>

      <Card>
        <Title className="mb-3">{lang === "zh" ? "运行时调配链路" : "Runtime Routing Chain"}</Title>
        <div className="space-y-2">
          {(runtimeRoutes ?? []).map((route, idx) => (
            <div key={route.model_key} className="rounded-tremor-default border border-tremor-border px-3 py-2 text-xs dark:border-dark-tremor-border">
              {idx + 1}. {route.provider_key} / {route.model_name} ({route.api_style}) - key: {route.api_key_hint || "(none)"}
            </div>
          ))}
          {(!runtimeRoutes || runtimeRoutes.length === 0) && (
            <Text className="text-xs text-tremor-content-subtle">{lang === "zh" ? "暂无可用运行时路由" : "No active runtime routes"}</Text>
          )}
        </div>
      </Card>
    </div>
  );
}
