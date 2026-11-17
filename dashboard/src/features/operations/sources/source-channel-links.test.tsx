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
  it("marks the BES channel as pending and shows only transport readiness", () => {
    render(<SourceChannelLinks channel={{ ...channel,
      country_code: "CO", scope: "co_ins_bes_weekly", integration_status: "awaiting_egress_and_source_contract",
      coverage_verified: false,
      transport_readiness: { configured: false, proxy_kind: "direct", dependency_ready: true },
    }} lang="zh" />);
    expect(screen.getByText(/等待哥伦比亚网络出口与报告格式确认/)).toBeInTheDocument();
    expect(screen.getByText(/传输配置：未配置/)).toBeInTheDocument();
    expect(screen.queryByText(/定时检查最新目录/)).not.toBeInTheDocument();
  });
  it("distinguishes imported BES history from scheduled runtime activation", () => {
    render(<SourceChannelLinks channel={{ ...channel,
      country_code: "CO", scope: "co_ins_bes_weekly", integration_status: "backfilled_pending_runtime_rollout",
      coverage_verified: false,
    }} lang="zh" />);
    expect(screen.getByText(/历史数据已接入/)).toBeInTheDocument();
    expect(screen.getByText(/运行服务更新后启用/)).toBeInTheDocument();
    expect(screen.queryByText(/等待哥伦比亚网络出口/)).not.toBeInTheDocument();
    expect(screen.queryByText(/定时检查最新目录/)).not.toBeInTheDocument();
  });
  it("describes active managed automation without claiming complete numerical coverage", () => {
    render(<SourceChannelLinks channel={{ ...channel,
      country_code: "CO", scope: "co_ins_bes_weekly", integration_status: "automated_managed_runtime",
      coverage_verified: false,
    }} lang="zh" />);
    expect(screen.getByText(/每小时检查新报告与修订/)).toBeInTheDocument();
    expect(screen.getByText(/失败自动重试/)).toBeInTheDocument();
    expect(screen.queryByText(/运行服务更新后启用/)).not.toBeInTheDocument();
  });
});
