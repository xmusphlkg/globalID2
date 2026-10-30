import { RunRoute } from "@/features/control-plane/run-route";
export default async function Page({
  params,
}: {
  params: Promise<{ taskUuid: string }>;
}) {
  const { taskUuid } = await params;
  return <RunRoute key={taskUuid} taskUuid={taskUuid} />;
}
