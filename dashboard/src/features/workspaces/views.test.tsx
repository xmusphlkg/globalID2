import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { apiFetch, apiFetchWithMeta } from "@/lib/api";
import { AIWorkbenchView, GovernanceView, SettingsWorkbenchView } from "./views";
import { AI_TASK_TYPES } from "@/features/operations/task-scope";
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn(), apiFetchWithMeta:vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
function show(ui: React.ReactNode) { render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>{ui}</QueryClientProvider>); }
it("shows governance evidence and links a country to its mapping review",async () => {
 vi.mocked(apiFetch).mockImplementation(async (path) => path === "/mappings/summary" ? {ai_review_ready_total:3,active_release:{release_code:"DMR-1"}} : {holding_series_total:4,mapping_coverage:0.75,countries:[{country_code:"US",holding_series_count:4,mapping_coverage:0.75}]});
 show(<GovernanceView />);
 expect(await screen.findByText("DMR-1")).toBeVisible();
 expect(screen.getAllByText("75.0%")).toHaveLength(2);
 expect(screen.getByRole("link",{name:"Mapping & classification"})).toHaveAttribute("href","/data/mappings?country=US");
 expect(screen.getByRole("link",{name:/Knowledge evidence/})).toHaveAttribute("href","/data/knowledge");
 expect(screen.queryByRole("button",{name:/collect|publish/i})).toBeNull();
});
it("keeps AI backlog requests separate from collection and publication",async () => {
 vi.mocked(apiFetch).mockResolvedValue({model_queue:{queued:5,leased:2}});
 vi.mocked(apiFetchWithMeta).mockResolvedValue({data:[],meta:{pagination:{total:0,page:1,page_size:8,total_pages:0}},headers:new Headers()});
 show(<AIWorkbenchView />);
 expect(await screen.findByText("No runs recorded.")).toBeVisible();
 for(const [path] of vi.mocked(apiFetchWithMeta).mock.calls) {
  expect(new URL(path,"http://localhost").searchParams.get("task_type")?.split(",")).toEqual([...AI_TASK_TYPES]);
 }
 expect(screen.getByRole("link",{name:/Routes & model queue/})).toHaveAttribute("href","/models/queue");
 expect(screen.getByRole("button",{name:"Create report task"})).toBeDisabled();
});
it("shows configuration readiness and preserves direct section editing",async () => {
 vi.mocked(apiFetch).mockResolvedValue({smtp:{smtp_configured:false,smtp_host:""},github:{github_configured:true,github_data_share_repo_url:"fixture-repository",raw_archive_configured:false,raw_archive_repo_url:""},cloudflare:{cloudflare_configured:true,default_cloudflare_project_name:"fixture"},site:{ga4_configured:false,public_ga4_measurement_id:""}});
 show(<SettingsWorkbenchView />);
 expect(await screen.findByRole("link",{name:/Email connection/})).toHaveAttribute("href","/settings/integrations?section=smtp");
 expect(screen.getByRole("link",{name:/Providers & model configuration/})).toHaveAttribute("href","/settings/models");
 expect(screen.getByRole("link",{name:"Schedules"})).toHaveAttribute("href","/collection/schedules");
});
