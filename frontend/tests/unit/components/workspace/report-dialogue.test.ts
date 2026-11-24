import { expect, test } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ReportDialogueControl } from "@/components/workspace/report-dialogue";
import { I18nContext } from "@/core/i18n/context";
import type { ReportDialogueView } from "@/core/threads/report-dialogue";

const view: ReportDialogueView = {
  available: true,
  binding: { enabled: true, report_id: "r1" },
  head_version_id: "t0002",
  active_version_id: "t0001",
  versions: [
    { version_id: "t0001", report_id: "r1", is_head: false, is_active: true },
    { version_id: "t0002", report_id: "r2", is_head: true, is_active: false },
  ],
  current_turn: {
    message_id: "m3",
    status: "exact_reuse",
    native_delivery_created: false,
    response_only: false,
  },
};

function render(value: ReportDialogueView, selected = false, running = false) {
  return renderToStaticMarkup(
    createElement(
      I18nContext.Provider,
      { value: { locale: "zh-CN", setLocale: () => undefined } },
      createElement(ReportDialogueControl, {
        view: value,
        selected,
        running,
        onChange: () => undefined,
      }),
    ),
  );
}

test("no report means no opt-in control; delivered report defaults unchecked", () => {
  expect(render({ ...view, available: false })).toBe("");
  expect(render(view)).toContain("继续修改这份报告");
  expect(render(view)).not.toContain('checked=""');
});

test("restored version is shown as active; running hides stale completion", () => {
  expect(render(view)).toContain("已恢复所选版本");
  expect(render(view)).toMatch(/当前版本.*?1/);
  const active = render(view, true, true);
  expect(active).toContain('disabled=""');
  expect(active).toContain('checked=""');
  expect(active).toContain("正在处理本轮");
  expect(active).not.toContain("已恢复所选版本");
});
