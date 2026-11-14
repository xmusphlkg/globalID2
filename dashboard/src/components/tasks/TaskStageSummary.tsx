import { Badge } from "@/components/ui/tremor";
import { LogBody } from "./TaskLogEntry";
import type { StageSnapshot } from "./task-metadata";

export function TaskStageSummary({ stages, title, expandLabel, collapseLabel }: {
  stages: StageSnapshot[];
  title: string;
  expandLabel: string;
  collapseLabel: string;
}) {
  if (!stages.length) return null;
  return (
    <section className="mb-3 min-w-0 space-y-2">
      <h3 className="text-xs font-medium uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{title}</h3>
      {stages.map((stage) => (
        <article key={`${stage.stage}-${stage.attempt}`} className={`min-w-0 rounded-md border px-3 py-2 ${stage.status === "failed" ? "border-rose-200 bg-rose-50/70 dark:border-rose-900/50 dark:bg-rose-950/20" : "border-tremor-border dark:border-dark-tremor-border"}`}>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-semibold">{stage.stage}</span>
            <Badge color={stage.status === "failed" ? "rose" : stage.status === "completed" ? "emerald" : "slate"}>{stage.status}</Badge>
            <span className="text-tremor-content-subtle">#{stage.attempt}</span>
          </div>
          {stage.metrics && Object.keys(stage.metrics).length > 0 ? (
            <dl className="mt-2 grid gap-2 sm:grid-cols-2">
              {Object.entries(stage.metrics).map(([key, value]) => (
                <div key={key} className="min-w-0">
                  <dt className="text-[10px] uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{key}</dt>
                  <dd className="break-all text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                    {typeof value === "string" ? value : JSON.stringify(value)}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
          {stage.error ? <div className="mt-2"><LogBody content={stage.error} expandLabel={expandLabel} collapseLabel={collapseLabel} /></div> : null}
        </article>
      ))}
    </section>
  );
}
