import { expect, test } from "@playwright/test";

import {
  MOCK_THREAD_ID,
  MOCK_THREAD_ID_2,
  mockLangGraphAPI,
} from "./utils/mock-api";

test("host Writers appear together without delegate messages and survive reload", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Report",
        messages: [
          { id: "u1", type: "human", content: "Write report" },
          { id: "a1", type: "ai", content: "Working on the report." },
        ],
      },
      {
        thread_id: MOCK_THREAD_ID_2,
        title: "Ordinary",
        messages: [
          { id: "u2", type: "human", content: "Hello" },
          { id: "a2", type: "ai", content: "Ordinary response." },
        ],
      },
    ],
  });
  let started = false;
  let done = false;
  await page.route("**/report-view", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        report_view: route.request().url().includes(MOCK_THREAD_ID_2)
          ? { status: "not_report" }
          : {
              status: done ? "delivered" : "running",
              run_id: "run1",
              writer_pipeline: {
                max_concurrent: 4,
                nodes: [
                  {
                    node_id: "n1",
                    title: "Chapter Alpha",
                    status: done
                      ? "completed"
                      : started
                        ? "in_flight"
                        : "pending",
                    task_id: started ? "writer-alpha" : undefined,
                    run_id: "run1",
                  },
                  {
                    node_id: "n2",
                    title: "Chapter Beta",
                    status: done
                      ? "completed"
                      : started
                        ? "in_flight"
                        : "pending",
                    task_id: started ? "writer-beta" : undefined,
                    run_id: "run1",
                  },
                  {
                    node_id: "n3",
                    title: "Chapter Gamma",
                    status: done ? "completed" : "pending",
                    task_id: done ? "writer-gamma" : undefined,
                    run_id: "run1",
                  },
                ],
              },
            },
      }),
    }),
  );
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  const pipeline = page.getByTestId("report-writer-pipeline");
  await expect(
    page.getByText("Working on the report.", { exact: true }),
  ).toBeVisible();
  await expect(pipeline).toHaveCount(0);
  started = true;
  await page.reload();
  await expect(pipeline).toBeVisible();
  await expect(
    pipeline.locator('[data-writer-status="in_flight"]'),
  ).toHaveCount(2);
  await expect(pipeline.locator('[data-writer-status="pending"]')).toHaveCount(
    1,
  );
  await expect(
    pipeline.getByText("Chapter Alpha", { exact: true }),
  ).toBeVisible();
  await expect(
    pipeline.getByText("Chapter Beta", { exact: true }),
  ).toBeVisible();
  done = true;
  await page.reload();
  await expect(
    pipeline.locator('[data-writer-status="completed"]'),
  ).toHaveCount(3);
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID_2}`);
  await expect(
    page.getByText("Ordinary response.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("report-writer-pipeline")).toHaveCount(0);
  expect(errors).toEqual([]);
});
