import { describe, expect, it, rs } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ReportStatusBanner } from "@/components/workspace/report-status";
import { I18nContext } from "@/core/i18n/context";

rs.mock("@/core/threads/hooks", () => ({
  useThreadReportView: () => ({
    data: (globalThis as { __reportView?: unknown }).__reportView ?? null,
    refetch: () => undefined,
  }),
}));

function renderBanner(isLoading = false) {
  return renderToStaticMarkup(
    createElement(
      I18nContext.Provider,
      { value: { locale: "en-US", setLocale: () => undefined } },
      createElement(ReportStatusBanner, {
        threadId: "t1",
        isLoading,
      }),
    ),
  );
}

describe("ReportStatusBanner", () => {
  it("renders running when the report view is associated with an active run", () => {
    (globalThis as { __reportView?: unknown }).__reportView = {
      status: "running",
      can_continue: true,
    };
    const html = renderBanner(true);
    expect(html).toContain('data-report-status="running"');
    expect(html).toContain("Report generation in progress");
  });

  it("does not turn an old incomplete report into running because a new run is active", () => {
    (globalThis as { __reportView?: unknown }).__reportView = {
      status: "incomplete",
      can_continue: true,
    };
    expect(renderBanner(true)).toBe("");
    expect(renderBanner(false)).toContain('data-report-status="incomplete"');
  });

  it("renders awaiting, stopped, technical, and delivered views", () => {
    const cases: Array<[string, string]> = [
      ["awaiting_user", "waiting for your input"],
      ["stopped", "Report stopped"],
      ["technical_error", "technical error"],
      ["delivered", "Report delivered"],
    ];
    for (const [status, label] of cases) {
      (globalThis as { __reportView?: unknown }).__reportView = {
        status,
        can_continue: status !== "delivered",
      };
      expect(renderBanner(false)).toContain(label);
    }
  });

  it("hides a delivered view while a new run is active and hides not_report", () => {
    (globalThis as { __reportView?: unknown }).__reportView = {
      status: "delivered",
      can_continue: false,
    };
    expect(renderBanner(true)).toBe("");
    (globalThis as { __reportView?: unknown }).__reportView = {
      status: "not_report",
    };
    expect(renderBanner(false)).toBe("");
  });
});
