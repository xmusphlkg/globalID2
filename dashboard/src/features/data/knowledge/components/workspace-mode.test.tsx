import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CataloguePanel } from "./catalogue-panel";
import type { KnowledgePageState } from "../hooks/use-knowledge-page";
afterEach(cleanup);
const state = {
 isLoading:false,isFetching:false,search:"",statusFilter:"all",displayFilter:"all",generator:"auto",priority:"normal",
 selectedSet:new Set(),selectedDiseaseId:null,refreshSources:[],forceRefresh:false,refreshError:null,refreshResult:null,
 refreshPending:false,visibleEntries:[],selectedCount:0,visibleSelectedCount:0,visibleAllSelected:false,
 setSearch:vi.fn(),setStatusFilter:vi.fn(),setDisplayFilter:vi.fn(),setGenerator:vi.fn(),setPriority:vi.fn(),toggleVisibleSelection:vi.fn(),clearSelection:vi.fn(),handleBatchRefresh:vi.fn(),setForceRefresh:vi.fn(),toggleSourceGroup:vi.fn(),queryKnowledgeBrief:vi.fn(),
} as unknown as KnowledgePageState;
it("governance browses evidence while generation controls stay in AI",() => {
 render(<CataloguePanel lang="en" state={state} mode="governance" />);
 expect(screen.queryByLabelText("Generator")).toBeNull();
 expect(screen.queryByRole("button",{name:"Refresh selected"})).toBeNull();
 expect(screen.getByRole("link",{name:"Generate knowledge briefs"})).toHaveAttribute("href","/models/knowledge");
 expect(screen.getByRole("link",{name:"Source - Knowledge"})).toHaveAttribute("href","/collection/sources/knowledge");
});
it("AI retains the existing generation controls",() => {
 render(<CataloguePanel lang="en" state={state} mode="ai" />);
 expect(screen.getByLabelText("Generator")).toBeVisible();
 expect(screen.getByRole("button",{name:"Refresh selected"})).toBeDisabled();
});
