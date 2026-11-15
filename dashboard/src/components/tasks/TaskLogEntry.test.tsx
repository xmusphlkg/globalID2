import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { LogBody } from "./TaskLogEntry";
import { TaskStageSummary } from "./TaskStageSummary";

afterEach(cleanup);

it("retains the complete long log behind an expandable preview", () => {
  const text = Array.from({ length: 35 }, (_, i) => `Log line ${i}`).join("\n");
  const { container } = render(<LogBody content={text} expandLabel="Expand" collapseLabel="Collapse" />);
  expect(container.querySelector("pre")?.textContent).not.toContain("Log line 34");
  fireEvent.click(screen.getByRole("button", { name: "Expand" }));
  expect(container.querySelector("pre")?.textContent).toBe(text);
  fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
  expect(container.querySelector("pre")?.textContent).not.toContain("Log line 34");
});

it("renders failed stage evidence and zero metrics without dropping values", () => {
  render(<TaskStageSummary title="Stages" expandLabel="Expand" collapseLabel="Collapse" stages={[
    { stage: "store", attempt: 2, status: "failed", error: "Storage unavailable", metrics: { inserted: 0, state: { retry: true } } },
  ]} />);
  expect(screen.getByRole("heading", { name: "Stages" })).toBeInTheDocument();
  expect(screen.getByText("Storage unavailable")).toBeInTheDocument();
  expect(screen.getByText("0")).toBeInTheDocument();
  expect(screen.getByText('{"retry":true}')).toBeInTheDocument();
});
