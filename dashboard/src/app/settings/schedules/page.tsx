import { permanentRedirect } from "next/navigation";
import { legacyScheduleDestination } from "@/shared/navigation/legacy-workspace";
export default async function Page({ searchParams }: { searchParams: Promise<Record<string,string|string[]|undefined>> }) { permanentRedirect(legacyScheduleDestination(await searchParams)); }
