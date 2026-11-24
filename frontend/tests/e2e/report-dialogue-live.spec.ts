import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

test.describe.configure({ retries: 0 });

test("real Gateway native report dialogue through browser", async ({
  page,
}, info) => {
  test.skip(
    process.env.SP2_UI_LIVE !== "1",
    "Paid experiment requires explicit launch environment",
  );
  test.setTimeout(900000);
  const root = resolve(process.cwd(), "..");
  const run = resolve(
    root,
    process.env.SP2_UI_RUN_DIR ?? ".deer-flow/ui-live/20261124-02",
  );
  const binding = JSON.parse(
    readFileSync(resolve(run, "runtime-binding.json"), "utf8"),
  ) as { thread_id: string; outputs: string };
  const requests = JSON.parse(
    readFileSync(
      resolve(
        root,
        process.env.SP2_UI_CASE_FILE ??
          "docs/research-report/three-topic-stability-20261123/lowcode/requests.json",
      ),
      "utf8",
    ),
  ).turns as { turn_index: number; query: string }[];
  const checks = JSON.parse(
    readFileSync(
      resolve(
        root,
        process.env.SP2_UI_CHECKS_FILE ??
          "docs/research-report/three-topic-stability-20261123/lowcode/evaluator_checks.json",
      ),
      "utf8",
    ),
  ).turns as {
    user_turn: number;
    canonical_sha256_by_node: Record<string, string>;
    expected_status?: string;
  }[];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const outcomes: unknown[] = [];
  const recordsPath = resolve(run, "browser-turns.json");
  await page.goto(`/workspace/chats/${binding.thread_id}`);
  const checkbox = page.getByRole("checkbox", { name: "Continue this report" });
  await expect(checkbox).toBeVisible({ timeout: 60000 });
  await expect(checkbox).not.toBeChecked();
  await checkbox.check();
  const firstTurn = Number(process.env.SP2_UI_FIRST_TURN ?? "2");
  for (const request of requests.filter(
    (item) =>
      item.turn_index >= firstTurn &&
      item.turn_index <= Number(process.env.SP2_UI_LAST_TURN ?? "5"),
  )) {
    const input = page.getByPlaceholder(/how can i assist you/i);
    const beforeResponse = await page.request.get(
      `/api/threads/${binding.thread_id}/report-dialogue`,
    );
    const beforeTurn = (await beforeResponse.json()).current_turn?.turn_id;
    const sent = page.waitForRequest(
      (req) => req.method() === "POST" && req.url().includes("/runs/stream"),
    );
    await input.fill(request.query);
    await input.press("Enter");
    const http = await sent;
    const body = http.postDataJSON() as {
      input: {
        messages: {
          id: string;
          additional_kwargs?: {
            report_dialogue?: { enabled: boolean; report_id: string };
          };
        }[];
      };
    };
    const message = body.input.messages.at(-1)!;
    expect(message.additional_kwargs?.report_dialogue?.enabled).toBe(true);
    let view: {
      current_turn: {
        turn_id: string;
        message_id: string;
        status: string;
      } | null;
    } = { current_turn: null };
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `/api/threads/${binding.thread_id}/report-dialogue`,
          );
          view = await response.json();
          return (
            Boolean(
              view.current_turn && view.current_turn.turn_id !== beforeTurn,
            ) &&
            [
              "accepted",
              "read_only",
              "exact_reuse",
              "failed",
              "cancelled",
            ].includes(view.current_turn?.status ?? "")
          );
        },
        { timeout: 300000, intervals: [1000, 2000, 3000] },
      )
      .toBe(true);
    const index = JSON.parse(
      readFileSync(
        resolve(binding.outputs, "report_dialogue/index.json"),
        "utf8",
      ),
    );
    const reportId =
      index.versions[index.active_version ?? index.head].report_id;
    const ledger = JSON.parse(
      readFileSync(
        resolve(binding.outputs, `report/${reportId}/ledger.json`),
        "utf8",
      ),
    );
    const actual: Record<string, string> = {};
    for (const draft of ledger.drafts.filter(
      (item: { is_current: boolean }) => item.is_current,
    )) {
      actual[draft.node_id] = createHash("sha256")
        .update(readFileSync(draft.path))
        .digest("hex");
    }
    const expected = checks.find(
      (item) => item.user_turn === request.turn_index,
    )!.canonical_sha256_by_node;
    outcomes.push({
      user_turn: request.turn_index,
      message_id: view.current_turn?.message_id,
      view,
      canonical: actual,
      canonical_scope_ok:
        JSON.stringify(Object.entries(actual).sort()) ===
        JSON.stringify(Object.entries(expected).sort()),
    });
    writeFileSync(
      recordsPath,
      JSON.stringify({ outcomes, page_errors: errors }, null, 2),
    );
    expect(view.current_turn?.status).toBe(
      checks.find((item) => item.user_turn === request.turn_index)
        ?.expected_status ??
        (request.turn_index === 5 ? "read_only" : "accepted"),
    );
    expect(actual).toEqual(expected);
    await expect(checkbox).toBeEnabled({ timeout: 30000 });
    await page.screenshot({
      path: info.outputPath(`real-turn-${request.turn_index}.png`),
      fullPage: true,
    });
  }
  expect(errors).toEqual([]);
  await page.reload();
  await expect(checkbox).not.toBeChecked();
});
