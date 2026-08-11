import { permanentRedirect } from "next/navigation";

export default function DiseasesLegacyPage() {
  permanentRedirect("/data/diseases");
}
