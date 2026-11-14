import { Badge, Card, Grid, Text } from "@/components/ui/tremor";
import { ChevronDown } from "lucide-react";
import type { AIInteractionItem } from "@/features/ai/api";
import { formatDateTime } from "@/lib/utils";
import type { InteractionLabels } from "../messages";
import { shortUuid, qualityText, rowStatusColor, jsonString } from "../presentation";

export function InteractionRow({
  item,
  expanded,
  onToggle,
  labels,
}: {
  item: AIInteractionItem;
  expanded: boolean;
  onToggle: () => void;
  labels: InteractionLabels;
}) {
  return (
    <Card className="overflow-hidden p-0">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition-colors hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-subtle"
      >
        <Badge color="slate">{item.agent ?? labels.unknown}</Badge>
        <span className="flex-1 truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {item.report_title}
        </span>
        {item.task_uuid && <Badge color="amber">{shortUuid(item.task_uuid)}</Badge>}
        <Badge color="indigo">{shortUuid(item.report_uuid)}</Badge>
        <Badge color="blue">{item.model ?? "-"}</Badge>
        <span className="w-24 text-right text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {item.total_tokens} {labels.tokens}
        </span>
        <span className="w-16 text-right text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {labels.qualityShort} {qualityText(item.quality_overall)}
        </span>
        <span
          className="h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: rowStatusColor(item.run_status) }}
          aria-hidden="true"
        />
        <ChevronDown
          className={`h-4 w-4 text-tremor-content-subtle transition-transform dark:text-dark-tremor-content-subtle ${expanded ? "rotate-180" : ""}`}
        />
      </button>

      {expanded && (
        <div className="border-t border-tremor-border px-4 py-3 dark:border-dark-tremor-border">
          <Grid numItems={1} numItemsLg={4} className="mb-3 gap-3">
            <Card className="p-3">
              <Text>{labels.taskUuid}</Text>
              <Text className="mt-1 break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.task_uuid ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.taskStatus}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.task_status ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.timestamp}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {formatDateTime(item.timestamp)}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.reportUuid}</Text>
              <Text className="mt-1 break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.report_uuid}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.runStatus}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.run_status ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.runUuid}</Text>
              <Text className="mt-1 break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.run_uuid ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.duration}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.duration != null ? `${item.duration.toFixed(2)}s` : "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.section}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.section_type ?? "-"} {item.section_title ? `(${item.section_title})` : ""}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.runModel}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.run_provider ?? "-"} / {item.run_model ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.promptTemp}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.temperature != null ? item.temperature.toFixed(2) : "-"}
              </Text>
            </Card>
          </Grid>

          <Grid numItems={1} numItemsLg={2} className="gap-3">
            <Card className="p-3">
              <Text className="mb-2">{labels.systemPrompt}</Text>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {item.system_prompt || "-"}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">{labels.prompt}</Text>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {item.prompt || "-"}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">{labels.response}</Text>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {item.response || "-"}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">{labels.tokens}</Text>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {jsonString(item.tokens)}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">{labels.qualityScores}</Text>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {jsonString(item.quality_scores)}
              </pre>
            </Card>
          </Grid>
        </div>
      )}
    </Card>
  );
}
