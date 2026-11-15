

import { DetailDrawer } from "@/components/ui/DetailDrawer";

import { type ModelCenterController } from "../hooks/use-model-center";
import { ActionButton, Field, FormSection, inputClass } from "./form-controls";

export function ProviderForm({ controller }: { controller: ModelCenterController }) {
  const { providers, createProvider, updateProvider, providerDrawerMode, setProviderDrawerMode, providerForm, setProviderForm, copy, resetProviderForm, submitProvider } = controller;

  return (<>
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
  </>);
}
