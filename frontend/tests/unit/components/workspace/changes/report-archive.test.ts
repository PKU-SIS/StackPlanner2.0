import { expect, rs, test } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { WorkspaceChangeBadge } from "@/components/workspace/changes/workspace-change-badge";
import { I18nContext } from "@/core/i18n/context";
import type {
  WorkspaceChangesResponse,
  WorkspaceFileChange,
} from "@/core/workspace-changes/types";

rs.mock("@/core/workspace-changes/hooks", () => ({
  useWorkspaceChanges: () => ({
    data: (globalThis as { __archiveChanges?: WorkspaceChangesResponse })
      .__archiveChanges,
    isLoading: false,
  }),
}));
rs.mock("@/components/workspace/changes/workspace-change-panel", () => ({
  WorkspaceChangePanel: () => null,
}));

function file(path: string): WorkspaceFileChange {
  return {
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
  };
}
function render(paths: string[], truncated = false) {
  (
    globalThis as { __archiveChanges?: WorkspaceChangesResponse }
  ).__archiveChanges = {
    available: true,
    version: 1,
    summary: {
      created: paths.length,
      modified: 0,
      deleted: 0,
      additions: paths.length,
      deletions: 0,
      truncated,
    },
    files: paths.map(file),
    limits: {},
  };
  return renderToStaticMarkup(
    createElement(
      I18nContext.Provider,
      { value: { locale: "en-US", setLocale: () => undefined } },
      createElement(WorkspaceChangeBadge, { threadId: "thread", runId: "run" }),
    ),
  );
}

test("full report turn copies are archived files, not hundreds of inline edits", () => {
  const html = render([
    "outputs/report_dialogue/turns/t0002/artifacts/materials/source.md",
    "outputs/report/r2/final/report-2.md",
  ]);
  expect(html).toContain("Report files");
  expect(html).not.toContain("source.md");
  expect(html).not.toContain("report-2.md");
  expect(html).toContain("button");
});

test("ordinary files and first-report files keep the existing inline preview", () => {
  const html = render([
    "workspace/main.py",
    "outputs/report/r1/final/report-1.md",
  ]);
  expect(html).not.toContain("Report files");
  expect(html).toContain("main.py");
  expect(html).toContain("report-1.md");
});

test("a truncated report-only preview is compact even before archive paths appear", () => {
  const html = render(
    [
      "outputs/report/r2/materials/source.md",
      "outputs/report/r2/final/report-2.md",
    ],
    true,
  );
  expect(html).toContain("Report files");
  expect(html).not.toContain("source.md");
});

test("current pointer and deleted historical report files stay collapsed without truncation", () => {
  const html = render([
    "outputs/report/r39/assembly/c1.json",
    "outputs/report/current.json",
    "outputs/report/r34/materials/S22/source.txt",
  ]);
  expect(html).toContain("Report files");
  expect(html).not.toContain("current.json");
  expect(html).not.toContain("source.txt");
  expect(html).not.toContain("Edited 3 files");
});

test("archive files do not hide ordinary code changes in a mixed run", () => {
  const html = render([
    "outputs/report_dialogue/index.json",
    "workspace/main.py",
  ]);
  expect(html).toContain("main.py");
});
