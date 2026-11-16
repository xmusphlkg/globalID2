import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { SourceChannel } from "@/lib/hooks/useSources";
import { SourceChannelLinks } from "./source-channel-links";

afterEach(cleanup);
const channel: SourceChannel = {
  country_code: "NG", scope: "ncdc_surveillance", source_id: "SRC_NG_NCDC",
  publisher: "Nigeria Centre for Disease Control and Prevention",
  official_url: "https://ncdc.gov.ng/reports/weekly", archive_url: "https://ncdc.gov.ng/diseases/sitreps",
  discovery_urls: [], cadence: "weekly_and_event_reports", refresh_strategy: "live_directory",
  refresh_years: 2, daily_time: "09:10", timezone: "Africa/Lagos",
};
describe("official update channels", () => {
  it("shows saved official and archive links plus the revision scope", () => {
    render(<SourceChannelLinks channel={channel} lang="zh" />);
    expect(screen.getByRole("link", { name: "官方更新渠道" })).toHaveAttribute("href", channel.official_url);
    expect(screen.getByRole("link", { name: "历史目录" })).toHaveAttribute("href", channel.archive_url);
    expect(screen.getByText(/最近 2 个日历年/)).toBeInTheDocument();
  });
  it("avoids duplicate links when one catalogue serves both purposes", () => {
    render(<SourceChannelLinks channel={{ ...channel, archive_url: channel.official_url }} lang="en" />);
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("link")).toHaveAttribute("rel", "noopener noreferrer");
  });
  it("leaves countries without saved channels unchanged", () => {
    const result = render(<SourceChannelLinks lang="en" />);
    expect(result.container).toBeEmptyDOMElement();
  });
});
