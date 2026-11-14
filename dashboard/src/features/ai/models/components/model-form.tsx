

import { type ThinkingMode } from "@/features/ai/api";

import { DetailDrawer } from "@/components/ui/DetailDrawer";

import { type ModelCenterController } from "../hooks/use-model-center";
import { ActionButton, Field, FormSection, inputClass } from "./form-controls";

export function ModelForm({ controller }: { controller: ModelCenterController }) {
  const { models, createModel, updateModel, modelDrawerMode, setModelDrawerMode, modelForm, setModelForm, copy, providerOptions, resetModelForm, submitModel } = controller;

  return (<>
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
              <Field label={copy.modelConcurrency}>
                <input type="number" min={1} max={512} step={1} className={inputClass} value={modelForm.runtime_max_concurrency} placeholder="1" onChange={(event) => setModelForm((prev) => ({ ...prev, runtime_max_concurrency: event.target.value }))} />
              </Field>
              <Field label={copy.thinkingMode}>
                <select className={inputClass} value={modelForm.thinking_mode} onChange={(event) => setModelForm((prev) => ({ ...prev, thinking_mode: event.target.value as ThinkingMode }))}>
                  {(Object.keys(copy.thinkingLabels) as ThinkingMode[]).map((mode) => (
                    <option key={mode} value={mode}>{copy.thinkingLabels[mode]}</option>
                  ))}
                </select>
              </Field>
            </div>
            <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{copy.modelConcurrencyHint}</p>
            <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{copy.thinkingHint}</p>
            <div className="space-y-3 rounded-tremor-default border border-tremor-border p-3 dark:border-dark-tremor-border">
              <p className="text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{copy.availability}</p>
              <div className="flex flex-wrap items-end justify-between gap-2">
                <Field label={copy.availabilityTimezone}>
                  <input className={inputClass} value={modelForm.availability_timezone} onChange={(event) => setModelForm((prev) => ({ ...prev, availability_timezone: event.target.value }))} placeholder="Asia/Shanghai" />
                </Field>
                <ActionButton
                  type="button"
                  onClick={() => setModelForm((prev) => ({
                    ...prev,
                    availability_windows: [...prev.availability_windows, { start: "00:00", end: "08:00" }],
                  }))}
                >
                  {copy.addWindow}
                </ActionButton>
              </div>
              {modelForm.availability_windows.length === 0 ? (
                <p className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{copy.alwaysAvailable}</p>
              ) : modelForm.availability_windows.map((window, index) => (
                <div key={`${window.start}-${window.end}-${index}`} className="grid grid-cols-[1fr_1fr_auto] items-end gap-2">
                  <input
                    type="time"
                    required
                    className={inputClass}
                    value={window.start}
                    onChange={(event) => setModelForm((prev) => ({
                      ...prev,
                      availability_windows: prev.availability_windows.map((item, itemIndex) => itemIndex === index ? { ...item, start: event.target.value } : item),
                    }))}
                  />
                  <input
                    type="time"
                    required
                    className={inputClass}
                    value={window.end}
                    onChange={(event) => setModelForm((prev) => ({
                      ...prev,
                      availability_windows: prev.availability_windows.map((item, itemIndex) => itemIndex === index ? { ...item, end: event.target.value } : item),
                    }))}
                  />
                  <ActionButton
                    type="button"
                    onClick={() => setModelForm((prev) => ({
                      ...prev,
                      availability_windows: prev.availability_windows.filter((_, itemIndex) => itemIndex !== index),
                    }))}
                  >
                    {copy.removeWindow}
                  </ActionButton>
                </div>
              ))}
              <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{copy.availabilityHint}</p>
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
  </>);
}
