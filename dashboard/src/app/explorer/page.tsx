import { permanentRedirect } from "next/navigation";

export default function ExplorerLegacyPage() {
  permanentRedirect("/data/explorer");
}
