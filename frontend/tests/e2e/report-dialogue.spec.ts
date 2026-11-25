import { expect, test } from "@playwright/test";

import {
  handleRunStream,
  MOCK_THREAD_ID,
  mockLangGraphAPI,
} from "./utils/mock-api";

test("report opt-in is explicit through actual composer submission", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Accepted report",
        messages: [
          { id: "origin-human", type: "human", content: "Generate report" },
          {
            id: "origin-ai",
            type: "ai",
            content: "Original report delivered.",
          },
        ],
      },
    ],
  });
  let current: Record<string, unknown> | null = null;
  const submitted: Array<Record<string, unknown>> = [];
  await page.route("**/api/threads/*/report-view", (route) =>
    route.fulfill({ json: { report_view: null } }),
  );
  await page.route("**/api/threads/*/report-dialogue", (route) =>
    route.fulfill({
      json: {
        available: true,
        binding: { enabled: true, report_id: "r1" },
        head_version_id: "t0001",
        active_version_id: "t0001",
        versions: [
          {
            version_id: "t0001",
            report_id: "r1",
            is_active: true,
            is_head: true,
          },
        ],
        current_turn: current,
      },
    }),
  );
  await page.route("**/runs/stream", (route) => {
    const body = route.request().postDataJSON() as {
      input: { messages: Array<Record<string, unknown>> };
    };
    const message = body.input.messages.at(-1)!;
    submitted.push(message);
    current = {
      message_id: message.id,
      status: "accepted",
      native_delivery_created: true,
      response_only: false,
    };
    return handleRunStream(route);
  });
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  const checkbox = page.getByRole("checkbox", { name: "Continue this report" });
  await expect(checkbox).toBeVisible({ timeout: 60000 });
  await expect(checkbox).not.toBeChecked();
  const input = page.getByPlaceholder(/how can i assist you/i);
  await input.fill("An ordinary question");
  await input.press("Enter");
  await expect.poll(() => submitted.length).toBe(1);
  expect(
    (submitted[0]!.additional_kwargs as Record<string, unknown>)
      ?.report_dialogue,
  ).toBeUndefined();
  await expect(checkbox).toBeEnabled();
  await checkbox.check();
  await input.fill("Only change one sentence in chapter two");
  await input.press("Enter");
  await expect.poll(() => submitted.length).toBe(2);
  expect(submitted[1]!.id).toEqual(expect.any(String));
  expect(
    (submitted[1]!.additional_kwargs as Record<string, unknown>)
      .report_dialogue,
  ).toEqual({ enabled: true, report_id: "r1" });
  await expect(
    page.getByText("New version delivered", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("dialogue-delivered.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(checkbox).not.toBeChecked();
  expect(errors).toEqual([]);
});

test("non-delivery outcomes show active version and never claim a new delivery", async ({
  page,
}, testInfo) => {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Report outcomes",
        messages: [
          { id: "h1", type: "human", content: "Continue report" },
          { id: "a1", type: "ai", content: "Turn outcome" },
        ],
      },
    ],
  });
  let status = "failed";
  await page.route("**/api/threads/*/report-view", (route) =>
    route.fulfill({ json: { report_view: null } }),
  );
  await page.route("**/api/threads/*/report-dialogue", (route) =>
    route.fulfill({
      json: {
        available: true,
        binding: { enabled: true, report_id: "r1" },
        head_version_id: "t0002",
        active_version_id: "t0001",
        versions: [
          {
            version_id: "t0001",
            report_id: "r1",
            is_active: true,
            is_head: false,
          },
          {
            version_id: "t0002",
            report_id: "r2",
            is_active: false,
            is_head: true,
          },
        ],
        current_turn: {
          message_id: "h1",
          status,
          native_delivery_created: false,
          response_only: false,
        },
      },
    }),
  );
  for (const [next, label] of [
    ["failed", "Turn failed; previous version preserved"],
    ["cancelled", "Turn cancelled; previous version preserved"],
    ["read_only", "Answer archived; no new version"],
    ["exact_reuse", "Selected version restored"],
  ]) {
    status = next!;
    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    const control = page.getByTestId("report-dialogue-control");
    await expect(control.getByText(label!, { exact: true })).toBeVisible({
      timeout: 60000,
    });
    await expect(control).not.toContainText("New version delivered");
    await expect(control).toContainText("Current version 1");
  }
  await page.screenshot({
    path: testInfo.outputPath("dialogue-restored.png"),
    fullPage: true,
  });
});

test("report snapshot files stay behind the existing view action", async ({
  page,
}) => {
  const runId = "report-archive-run";
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Report archive",
        messages: [
          {
            id: "archive-h",
            type: "human",
            content: "Edit one cell",
            run_id: runId,
          },
          {
            id: "archive-ai",
            type: "ai",
            content: "Only one cell was updated.",
            run_id: runId,
          },
        ],
      },
    ],
  });
  await page.route("**/api/threads/*/report-dialogue", (route) =>
    route.fulfill({ json: { available: false } }),
  );
  await page.route("**/api/threads/*/report-view", (route) =>
    route.fulfill({ json: { report_view: null } }),
  );
  await page.route(
    `**/api/threads/*/runs/${runId}/workspace-changes?*`,
    (route) =>
      route.fulfill({
        json: {
          available: true,
          version: 1,
          summary: {
            created: 3,
            modified: 0,
            deleted: 0,
            additions: 3,
            deletions: 0,
            truncated: false,
          },
          limits: {},
          files: [
            "outputs/report/r39/materials/source.md",
            "outputs/report/current.json",
            "/mnt/user-data/outputs/report/r2/final/report.md",
          ].map((path) => ({
            path,
            root: "outputs",
            status: "created",
            binary: false,
            sensitive: false,
            size_before: null,
            size_after: 10,
            sha256_before: null,
            sha256_after: "hash",
            diff: "",
            diff_truncated: false,
            diff_unavailable_reason: null,
            additions: 1,
            deletions: 0,
          })),
        },
      }),
  );
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await expect(page.getByText("Report files", { exact: true })).toBeVisible({
    timeout: 60000,
  });
  await expect(page.getByText(/source\.md/)).toHaveCount(0);
  await page.getByRole("button", { name: "View changes", exact: true }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByText(/source\.md/)
      .first(),
  ).toBeVisible();
});
