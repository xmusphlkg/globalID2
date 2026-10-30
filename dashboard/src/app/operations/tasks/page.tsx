import { permanentRedirect } from "next/navigation";
import { legacyTaskDestination } from "@/shared/navigation/legacy-workspace";
export default async function Page({ searchParams }: { searchParams: Promise<Record<string,string|string[]|undefined>> }) { permanentRedirect(legacyTaskDestination(await searchParams)); }
