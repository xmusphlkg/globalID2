import { permanentRedirect } from "next/navigation";

export default function SourcesIndexPage() {
  permanentRedirect("/operations/sources");
}
