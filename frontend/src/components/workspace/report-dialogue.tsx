"use client";

import { useI18n } from "@/core/i18n/hooks";
import {
  dialogueOutcome,
  type ReportDialogueView,
} from "@/core/threads/report-dialogue";

export function ReportDialogueControl({
  view,
  selected,
  running,
  onChange,
}: {
  view: ReportDialogueView;
  selected: boolean;
  running: boolean;
  onChange: (selected: boolean) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === "zh-CN";
  if (!view.available || !view.binding) return null;
  const version =
    view.versions.findIndex(
      (item) => item.version_id === view.active_version_id,
    ) + 1;
  return (
    <div
      className="bg-muted/40 mb-2 flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-xs"
      data-testid="report-dialogue-control"
    >
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={selected}
          disabled={running}
          onChange={(event) => onChange(event.target.checked)}
        />
        {zh ? "继续修改这份报告" : "Continue this report"}
      </label>
      <span>
        {zh ? "当前版本" : "Current version"} {version || 1}
      </span>
      <span role="status" aria-live="polite">
        {dialogueOutcome(view, running, zh)}
      </span>
    </div>
  );
}
