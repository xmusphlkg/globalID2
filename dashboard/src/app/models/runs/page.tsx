import { permanentRedirect } from "next/navigation";
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const values = await searchParams;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) for (const part of Array.isArray(value) ? value : value !== undefined ? [value] : []) params.append(key, part);
  permanentRedirect(`/models/agents${params.size ? `?${params}` : ""}`);
}
