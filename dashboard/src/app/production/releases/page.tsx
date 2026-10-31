import { permanentRedirect } from "next/navigation";
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const values = await searchParams;
  const destination = new URL("/publish", "http://localhost");
  for (const [key, value] of Object.entries(values)) { for (const part of Array.isArray(value) ? value : value !== undefined ? [value] : []) destination.searchParams.append(key, part); }
  permanentRedirect(destination.pathname + destination.search);
}
