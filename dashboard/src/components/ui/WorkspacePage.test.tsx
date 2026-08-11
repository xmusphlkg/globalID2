import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WorkspacePage } from "./WorkspacePage";

describe("WorkspacePage", () => {
  it("renders a consistent page heading and description", () => {
    render(
      <WorkspacePage title="Task Runs" description="Inspect execution history and logs.">
        <div>Task table</div>
      </WorkspacePage>,
    );
    expect(screen.getByRole("heading", { name: "Task Runs" })).toBeInTheDocument();
    expect(screen.getByText("Inspect execution history and logs.")).toBeInTheDocument();
    expect(screen.getByText("Task table")).toBeInTheDocument();
  });
});
