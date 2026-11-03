import { expect, test } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

test("recovered subtask history does not cause a render loop", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const call = {
    id: "same-review",
    name: "sp_delegate",
    args: { target_agent: "outline", task: "Check report integration" },
    type: "tool_call",
  };
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Recovered review",
        messages: [
          { id: "human", type: "human", content: "Review the report" },
          { id: "attempt-one", type: "ai", content: "", tool_calls: [call] },
          {
            id: "failed-result",
            type: "tool",
            name: "sp_delegate",
            tool_call_id: call.id,
            content: "Rejected review",
            additional_kwargs: {
              subagent_status: "failed",
              subagent_error: "missing report_task",
            },
          },
          { id: "attempt-two", type: "ai", content: "", tool_calls: [call] },
          {
            id: "success-result",
            type: "tool",
            name: "sp_delegate",
            tool_call_id: call.id,
            content: "Review recovered",
            additional_kwargs: {
              subagent_status: "completed",
              subagent_result_brief: "Review recovered",
            },
          },
          {
            id: "final",
            type: "ai",
            content: "Report delivered successfully.",
          },
        ],
      },
    ],
  });
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await expect(
    page.getByText("Report delivered successfully.", { exact: true }),
  ).toBeVisible({ timeout: 20000 });
  await page.waitForTimeout(1500);
  expect(
    errors.filter((error) => error.includes("Maximum update depth")),
  ).toEqual([]);
  await expect(page.getByText("This page couldn’t load")).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByText("Report delivered successfully.", { exact: true }),
  ).toBeVisible({ timeout: 15000 });
  expect(
    errors.filter((error) => error.includes("Maximum update depth")),
  ).toEqual([]);
});
