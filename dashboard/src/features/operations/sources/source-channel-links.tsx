import type { SourceChannel } from "@/lib/hooks/useSources";

export function SourceChannelLinks({ channel, lang }: { channel?: SourceChannel | null; lang: "en" | "zh" }) {
  if (!channel) return null;
  const awaitingBes = channel.integration_status === "awaiting_egress_and_source_contract";
  const awaitingRuntime = channel.integration_status === "backfilled_pending_runtime_rollout";
  const managedAutomation = channel.integration_status === "automated_managed_runtime";
  const transport = channel.transport_readiness;
  const links = [
    { url: channel.official_url, label: lang === "zh" ? "官方更新渠道" : "Official updates" },
    ...(channel.archive_url !== channel.official_url
      ? [{ url: channel.archive_url, label: lang === "zh" ? "历史目录" : "Archive" }]
      : []),
  ];
  return (
    <div className="space-y-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {links.map(({ url, label }) => <a key={url} href={url} target="_blank" rel="noopener noreferrer" className="text-sky-700 underline dark:text-sky-300">{label}</a>)}
      </div>
      <p>{channel.publisher}</p>
      <p>{awaitingBes
        ? lang === "zh"
          ? "等待哥伦比亚网络出口与报告格式确认。BES 数据覆盖尚未核验，自动更新未启用。"
          : "Awaiting Colombia network access and bulletin format verification. BES coverage is unverified; automatic updates are disabled."
        : managedAutomation
          ? lang === "zh"
            ? "自动更新已由独立受管任务运行：每小时检查新报告与修订，失败自动重试；原稿异常保留为空并附原文。"
            : "A managed task checks for new and revised reports hourly and retries failures automatically; malformed source values remain null with original evidence."
        : awaitingRuntime
          ? lang === "zh"
            ? "历史数据已接入，原稿异常值保留为空并附原文。自动更新将在运行服务更新后启用。"
            : "Historical data is imported; malformed source values retain their printed evidence and remain null. Automatic updates await the runtime update."
        : lang === "zh"
          ? `定时检查最新目录，默认重查最近 ${channel.refresh_years} 个日历年的修订；更早历史可手动回放。`
          : `Scheduled checks refresh the live catalogue; the default revision window covers ${channel.refresh_years} calendar years; older history can be replayed manually.`}</p>
      {transport && <p>{lang === "zh"
        ? `传输配置：${transport.configured ? "已配置" : "未配置"} · 方式：${transport.proxy_kind} · 依赖：${transport.dependency_ready ? "就绪" : "未就绪"}`
        : `Transport: ${transport.configured ? "configured" : "not configured"} · ${transport.proxy_kind} · dependency ${transport.dependency_ready ? "ready" : "not ready"}`}</p>}
    </div>
  );
}
