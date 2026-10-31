import { permanentRedirect } from "next/navigation";
import { legacyDestination } from "@/shared/navigation/legacy-query";
export default async function Page({ searchParams }: { searchParams: Promise<Record<string,string|string[]|undefined>> }) {
  const query = await searchParams;
  const line = Array.isArray(query.line) ? query.line[0] : query.line;
  const destinations: Record<string,string> = {surveillance:"ingestion",situation:"situation",literature:"literature",knowledge:"knowledge"};
  permanentRedirect(legacyDestination(`/collection/sources/${destinations[line ?? ""] ?? "ingestion"}`,query));
}
