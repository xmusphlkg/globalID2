import type { SourceChannel } from "@/lib/hooks/useSources";

export function SourceChannelLinks({ channel, lang }: { channel?: SourceChannel | null; lang: "en" | "zh" }) {
  if (!channel) return null;
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
      <p>{lang === "zh" ? `定时检查最新目录，默认重查最近 ${channel.refresh_years} 个日历年的修订；更早历史可手动回放。` : `Scheduled checks refresh the live catalogue; the default revision window covers ${channel.refresh_years} calendar years; older history can be replayed manually.`}</p>
    </div>
  );
}
