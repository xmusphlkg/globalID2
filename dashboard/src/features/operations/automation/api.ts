export {
  useAutomationConfig,
  useAutomationJobs,
  useCreateAutomationJob,
  useDeleteAutomationJob,
  useRunAutomationJob,
  useSourceConfigs,
  useUpdateAutomationJob,
} from "@/lib/hooks/useSources";

export type {
  AutomationConfig,
  AutomationJob,
  AutomationJobInput,
  AutomationTriggerResult,
  CountrySourceConfig,
} from "@/lib/hooks/useSources";
