"use client";

import { useEffect, useMemo } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { useSubtaskContext } from "@/core/tasks/context";
import {
  hydratePipelineTasks,
  visiblePipelineNodes,
} from "@/core/tasks/pipeline";
import { useThreadReportView } from "@/core/threads/hooks";
import type { ReportWriterNode } from "@/core/threads/types";

import { SubtaskCard } from "./messages/subtask-card";

export function ReportWriterPipeline({
  threadId,
  isLoading,
  anchoredTaskIds,
}: {
  threadId: string;
  isLoading: boolean;
  anchoredTaskIds: ReadonlySet<string>;
}) {
  const { t } = useI18n();
  const labels = t.conversation.writerPipeline;
  const { data: view } = useThreadReportView(threadId, {
    enabled: Boolean(threadId),
    isLoading,
  });
  const { tasks, tasksRef, setTasks } = useSubtaskContext();
  const nodes = useMemo(
    () => visiblePipelineNodes(view, isLoading, anchoredTaskIds),
    [view, isLoading, anchoredTaskIds],
  );
  useEffect(() => {
    const next = hydratePipelineTasks(tasksRef.current, nodes, labels.failed);
    if (next !== tasksRef.current) {
      tasksRef.current = next;
      setTasks(next);
    }
  }, [nodes, labels.failed, setTasks, tasksRef]);
  if (!view?.writer_pipeline || nodes.length === 0) return null;
  const all = view.writer_pipeline.nodes;
  const renderRows = (rows: ReportWriterNode[]) => (
    <div role="list" className="flex flex-col gap-2">
      {rows.map((node) => (
        <div
          role="listitem"
          key={node.node_id}
          data-writer-node={node.node_id}
          data-writer-status={node.status}
        >
          <div className="text-muted-foreground mb-1 flex justify-between gap-2 text-xs">
            {(!node.task_id || !tasks[node.task_id]) && (
              <span>{node.title}</span>
            )}
            <span>{labels[node.status]}</span>
          </div>
          {node.task_id && tasks[node.task_id] && (
            <SubtaskCard
              taskId={node.task_id}
              threadId={threadId}
              runId={node.run_id ?? undefined}
              isLoading={view.status === "running"}
              descriptionOverride={node.title}
            />
          )}
        </div>
      ))}
    </div>
  );
  const quietGroups = ["completed", "pending"] as const;
  return (
    <section
      data-testid="report-writer-pipeline"
      className="flex w-full flex-col gap-3 rounded-lg border p-3"
    >
      <header className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium">
          {labels.title} · {all.filter((n) => n.status === "completed").length}/
          {all.length}
        </span>
        <span className="text-muted-foreground text-xs">
          {labels.concurrency}: {view.writer_pipeline.max_concurrent}
        </span>
      </header>
      {quietGroups.map((status) => {
        const rows = nodes.filter((node) => node.status === status);
        if (!rows.length) return null;
        return rows.length > 4 ? (
          <details key={status}>
            <summary className="text-muted-foreground cursor-pointer text-xs">
              {labels[status]} ({rows.length})
            </summary>
            <div className="mt-2">{renderRows(rows)}</div>
          </details>
        ) : (
          <div key={status}>{renderRows(rows)}</div>
        );
      })}
      {renderRows(
        nodes.filter(
          (node) => node.status !== "pending" && node.status !== "completed",
        ),
      )}
    </section>
  );
}
