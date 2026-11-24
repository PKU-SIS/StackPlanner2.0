import { expect, test } from "@rstest/core";

import {
  dialogueMessageKwargs,
  dialogueOutcome,
  type ReportDialogueView,
} from "@/core/threads/report-dialogue";

const selection = {
  threadId: "t1",
  binding: { enabled: true as const, report_id: "r1" },
};

test("opt-in defaults off and never crosses threads; attachments survive", () => {
  const original = { files: ["material.csv"] };
  expect(dialogueMessageKwargs("t1", null, original)).toBe(original);
  expect(dialogueMessageKwargs("t2", selection, original)).toBe(original);
  expect(dialogueMessageKwargs("t1", selection, original)).toEqual({
    ...original,
    report_dialogue: selection.binding,
  });
});

test("read-only and failed outcomes never appear as report delivery", () => {
  const view = {
    current_turn: { status: "failed", native_delivery_created: false },
  } as ReportDialogueView;
  expect(dialogueOutcome(view, false, true)).toContain("未完成");
  expect(dialogueOutcome(view, true, true)).toBe("正在处理本轮");
  view.current_turn!.status = "read_only";
  expect(dialogueOutcome(view, false, true)).toContain("未生成新版本");
  view.current_turn!.status = "accepted";
  expect(dialogueOutcome(view, false, true)).not.toContain("已交付");
  view.current_turn!.native_delivery_created = true;
  expect(dialogueOutcome(view, false, true)).toBe("新版本已交付");
});
