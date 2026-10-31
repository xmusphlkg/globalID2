import { permanentRedirect } from "next/navigation";
import { legacyDestination } from "@/shared/navigation/legacy-query";
export default async function Page({ searchParams }: { searchParams: Promise<Record<string,string|string[]|undefined>> }) { permanentRedirect(legacyDestination("/data/literature", await searchParams)); }
