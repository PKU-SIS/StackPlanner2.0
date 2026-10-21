import { describe, expect, it } from "@rstest/core";

import {
  shouldRefreshReportViewFromCustomEvent,
  shouldRefreshReportViewFromUpdate,
} from "@/core/threads/hooks";

describe("report view stream triggers", () => {
  it("refreshes when the first delegated tool starts after the initial empty view", () => {
    expect(
      shouldRefreshReportViewFromCustomEvent({
        type: "task_started",
        task_id: "t1",
      }),
    ).toBe(true);
    expect(
      shouldRefreshReportViewFromCustomEvent({
        type: "task_completed",
        task_id: "t1",
      }),
    ).toBe(true);
    expect(
      shouldRefreshReportViewFromCustomEvent({
        type: "task_running",
        task_id: "t1",
      }),
    ).toBe(false);
  });

  it("refreshes when an existing status update carries report state", () => {
    expect(
      shouldRefreshReportViewFromUpdate({
        report: { sp_current_stage: "planning" },
      }),
    ).toBe(true);
    expect(
      shouldRefreshReportViewFromUpdate({
        report: { sp_pending_human_interaction: { interaction_id: "i1" } },
      }),
    ).toBe(true);
  });

  it("does not refresh for an unrelated ordinary update", () => {
    expect(
      shouldRefreshReportViewFromUpdate({
        ordinary: { messages: [], title: "ordinary" },
      }),
    ).toBe(false);
  });
});
