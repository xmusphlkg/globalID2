

import { asString } from "../presentation";
import { referenceIndex, referenceUrl, referenceLinkLabel, formatReferenceCitation } from "../citations";

export function ReportReferences({ references, lang }: { references: Record<string, unknown>[]; lang: "en" | "zh" }) {
  if (references.length === 0) return null;

  return (
    <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {lang === "zh" ? "参考文献" : "References"}
      </p>
      <ol className="mt-3 space-y-3">
        {references.map((source, index) => {
          const citationIndex = referenceIndex(source, index + 1);
          const url = referenceUrl(source);
          return (
            <li key={`${citationIndex}-${url || asString(source.title) || index}`} id={`report-ref-${citationIndex}`}>
              <div className="flex items-start gap-3 text-sm leading-6 text-tremor-content dark:text-dark-tremor-content">
                <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-tremor-small border border-tremor-border bg-tremor-background-subtle font-mono text-[10px] font-semibold text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
                  {citationIndex}
                </span>
                <span className="min-w-0">
                  <span className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                    {formatReferenceCitation(source, citationIndex)}
                  </span>
                  {url ? (
                    <span className="mt-1 block break-all text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {referenceLinkLabel(source)}:{" "}
                      <a href={url} target="_blank" rel="noreferrer" className="text-tremor-brand underline-offset-2 hover:underline dark:text-dark-tremor-brand">
                        {url}
                      </a>
                    </span>
                  ) : null}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
