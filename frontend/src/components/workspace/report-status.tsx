"use client";

import { useEffect } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { useThreadReportView } from "@/core/threads/hooks";
import { cn } from "@/lib/utils";

type ReportStatusBannerProps = {
  threadId?: string | null;
  isLoading?: boolean;
  isMock?: boolean;
  className?: string;
};

/**
 * Small read-side report status.  The server derives it from the current
 * ledger/run/pending state; this component never invents delivery state.
 */
export function ReportStatusBanner({
  threadId,
  isLoading = false,
  isMock = false,
  className,
}: ReportStatusBannerProps) {
  const { t } = useI18n();
  const report = t.conversation.reportStatus;
  const { data: view, refetch } = useThreadReportView(threadId, {
    enabled: Boolean(threadId) && !isMock,
    isMock,
    refetchInterval: isLoading ? 2000 : false,
  });

  // The state endpoint is the source for the derived view. A finished, stopped,
  // or HITL-ended stream does not carry a persisted report status, so refresh
  // the read-side projection when the active stream transitions to idle.
  useEffect(() => {
    if (!threadId || isMock || isLoading) {
      return;
    }
    void refetch();
  }, [isLoading, isMock, refetch, threadId]);

  if (!view || view.status === "not_report") {
    return null;
  }
  // While a new run is active, a delivered view belongs to the previous task;
  // do not show a stale completion card over the new run.
  if (isLoading && view.status === "delivered") {
    return null;
  }

  const status = isLoading ? "running" : view.status;
  const label =
    status === "running"
      ? report.running
      : status === "awaiting_user"
        ? report.awaiting
        : status === "delivered"
          ? report.delivered
          : status === "stopped"
            ? report.stopped
            : status === "technical_error"
              ? report.technical
              : report.incomplete;

  return (
    <div
      className={cn(
        "mb-2 flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs",
        status === "technical_error"
          ? "border-red-500/30 bg-red-500/5 text-red-700 dark:text-red-300"
          : status === "delivered"
            ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300"
            : "bg-muted/40 text-muted-foreground",
        className,
      )}
      role="status"
      aria-live="polite"
      title={view.reason}
      data-testid="report-status-banner"
      data-report-status={status}
    >
      <span className="bg-current size-1.5 shrink-0 rounded-full" aria-hidden="true" />
      <span className="font-medium">{report.title}:</span>
      <span>{label}</span>
    </div>
  );
}
