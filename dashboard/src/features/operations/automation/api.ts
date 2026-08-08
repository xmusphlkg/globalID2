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
  SourcePolicyMetadata,
} from "@/lib/hooks/useSources";
