import { AlertTriangle } from "lucide-react";

export function ErrorState({ title = "Unable to load data", description, onRetry }: { title?: string; description?: string; onRetry?: () => void }) {
  return <div role="alert" className="flex min-h-48 flex-col items-center justify-center rounded-md border border-rose-200 bg-rose-50 p-6 text-center"><AlertTriangle className="h-6 w-6 text-rose-600" /><p className="mt-3 text-sm font-semibold text-rose-900">{title}</p>{description ? <p className="mt-1 max-w-md text-sm text-rose-700">{description}</p> : null}{onRetry ? <button type="button" onClick={onRetry} className="mt-4 h-9 rounded-md border border-rose-300 bg-white px-3 text-sm font-semibold text-rose-800 hover:bg-rose-100">Try again</button> : null}</div>;
}
