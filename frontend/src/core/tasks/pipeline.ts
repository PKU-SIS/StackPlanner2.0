import type { ReportView, ReportWriterNode } from "../threads/types";

import { computeNextSubtask } from "./subtask-update";
import type { Subtask } from "./types";

export function visiblePipelineNodes(
  view: ReportView | null | undefined,
  isLoading: boolean,
  anchored: ReadonlySet<string>,
): ReportWriterNode[] {
  if (
    !view ||
    view.status === "not_report" ||
    (isLoading && view.status !== "running" && view.status !== "awaiting_user")
  )
    return [];
  const nodes = view.writer_pipeline?.nodes ?? [];
  // A planned queue is not writing activity. Keep the panel after dispatch,
  // including when every Writer has finished or stopped.
  if (!nodes.some((node) => Boolean(node.task_id))) return [];
  return nodes.filter(
    (node) =>
      !node.task_id ||
      (!anchored.has(node.task_id) &&
        !anchored.has(node.task_id.replace(/^spact_/, ""))),
  );
}

/** Apply an authoritative snapshot outside render, retaining live event detail. */
export function hydratePipelineTasks(
  current: Record<string, Subtask>,
  nodes: ReportWriterNode[],
  failureText: string,
): Record<string, Subtask> {
  let result = current;
  for (const node of nodes) {
    if (!node.task_id || node.status === "pending") continue;
    const previous = result[node.task_id];
    const status =
      node.status === "completed"
        ? "completed"
        : node.status === "failed" || node.status === "stopped"
          ? "failed"
          : "in_progress";
    const next = computeNextSubtask(previous, {
      id: node.task_id,
      subagent_type: "reporter",
      description: node.title,
      prompt: previous?.prompt ?? node.title,
      status,
      ...(status === "failed" ? { error: failureText } : {}),
      ...(node.draft_path ? { artifacts: [node.draft_path] } : {}),
    }).next;
    if (
      previous?.status === next.status &&
      previous.description === next.description &&
      previous.prompt === next.prompt &&
      previous.error === next.error &&
      previous.subagent_type === next.subagent_type &&
      (previous.artifacts ?? []).join("\0") ===
        (next.artifacts ?? []).join("\0")
    )
      continue;
    if (result === current) result = { ...current };
    result[node.task_id] = next;
  }
  return result;
}
