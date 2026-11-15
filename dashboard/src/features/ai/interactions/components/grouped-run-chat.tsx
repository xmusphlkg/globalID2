import { Badge, Card, Text } from "@/components/ui/tremor";
import { formatDateTime } from "@/lib/utils";
import type { InteractionLabels } from "../messages";
import type { RunThread } from "../threads";
import { shortUuid, qualityText } from "../presentation";

export function GroupedRunChat({
  thread,
  labels,
}: {
  thread: RunThread;
  labels: InteractionLabels;
}) {
  return (
    <Card className="space-y-4 p-4">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          {thread.taskUuid && <Badge color="amber">{labels.taskUuid} {shortUuid(thread.taskUuid)}</Badge>}
          <Badge color="indigo">{labels.reportUuid} {shortUuid(thread.reportUuid)}</Badge>
          <Badge color="slate">{labels.runUuid} {shortUuid(thread.runUuid)}</Badge>
          <Badge color="blue">{thread.sectionType ?? labels.unknownSection}</Badge>
          <Badge color="emerald">{thread.runStatus ?? "-"}</Badge>
        </div>
        <Text className="text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {thread.taskName || thread.reportTitle}
        </Text>
        <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {thread.sectionTitle ?? "-"} | {labels.disease}: {thread.diseaseName ?? "-"} | {labels.model}: {thread.runProvider ?? "-"}/{thread.runModel ?? "-"} | {labels.qualityShort} {qualityText(thread.qualityOverall)} | {labels.tokens} {thread.totalTokens} | {labels.taskStatus}: {thread.taskStatus ?? "-"}
        </Text>
        <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {formatDateTime(thread.startedAt)} {"->"} {formatDateTime(thread.endedAt)}
        </Text>
      </div>

      <div className="space-y-3">
        {thread.messages.map((item) => {
          const alignRight = (item.agent ?? "").toLowerCase() === "writer";
          return (
            <div key={item.id} className={`flex ${alignRight ? "justify-end" : "justify-start"}`}>
              <div className={`w-full max-w-4xl rounded-tremor-default border p-3 ${alignRight ? "border-blue-200 bg-blue-50/70 dark:border-blue-900/70 dark:bg-blue-950/20" : "border-slate-200 bg-slate-50/70 dark:border-slate-800 dark:bg-slate-900/20"}`}>
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Badge color="slate">{item.agent ?? labels.unknown}</Badge>
                  <Badge color="blue">{item.role ?? "-"}</Badge>
                  <Badge color="indigo">{item.model ?? "-"}</Badge>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {formatDateTime(item.timestamp)}
                  </Text>
                </div>

                <details className="mb-2 rounded-md border border-tremor-border/70 px-2 py-1 dark:border-dark-tremor-border/70">
                  <summary className="cursor-pointer text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {labels.systemPrompt}
                  </summary>
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                    {item.system_prompt || "-"}
                  </pre>
                </details>

                <Text className="mb-1 text-xs font-medium">{labels.prompt}</Text>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-white/80 p-2 text-xs text-tremor-content dark:bg-black/20 dark:text-dark-tremor-content">
                  {item.prompt || "-"}
                </pre>

                <Text className="mb-1 mt-2 text-xs font-medium">{labels.response}</Text>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-white/80 p-2 text-xs text-tremor-content dark:bg-black/20 dark:text-dark-tremor-content">
                  {item.response || "-"}
                </pre>

                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {labels.tokens}: {item.total_tokens}
                  </Text>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {labels.duration}: {item.duration != null ? `${item.duration.toFixed(2)}s` : "-"}
                  </Text>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    temp: {item.temperature != null ? item.temperature.toFixed(2) : "-"}
                  </Text>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {labels.qualityShort}: {qualityText(item.quality_overall)}
                  </Text>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
