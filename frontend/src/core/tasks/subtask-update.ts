import { mergeSteps } from "./steps";
import type { Subtask } from "./types";

export function isTerminalSubtaskStatus(status: Subtask["status"] | undefined) {
  return status === "completed" || status === "failed";
}

/**
 * Pure state transition for a single subtask update (#3779).
 *
 * Kept separate from the React hook so it can be unit-tested and, crucially, so
 * the hook can compute `next` from the *latest* `previous` handed to a
 * functional `setTasks` updater — not a stale `tasks` snapshot captured in a
 * closure. Deriving `next` from whatever `previous` the caller passes is what
 * lets an in-flight `fetchSubtaskSteps().then(...)` merge into current state
 * instead of clobbering SSE steps / sibling subtasks that arrived meanwhile.
 *
 * `steps` are treated as deltas: they are merged into `previous.steps`
 * (deduped/ordered by message_index) rather than replacing them, so live SSE
 * steps and fetched-on-expand backfill build one timeline.
 */
export function computeNextSubtask(
  previous: Subtask | undefined,
  task: Partial<Subtask> & { id: string },
): { next: Subtask; becameTerminal: boolean } {
  const previousStatus = previous?.status;

  // MessageList writes the pending task tool-call state before parsing the
  // matching ToolMessage in the same render. Keep terminal results stable
  // across the next render so the refresh notification does not loop.
  const next = {
    ...previous,
    ...task,
    ...(task.status === "completed" && task.error === undefined
      ? { error: undefined }
      : {}),
    ...(task.status === "in_progress" && isTerminalSubtaskStatus(previousStatus)
      ? { status: previousStatus }
      : {}),
  } as Subtask;

  if (task.steps) {
    next.steps = mergeSteps(previous?.steps ?? [], task.steps);
  }
  if (task.artifacts) {
    next.artifacts = Array.from(
      new Set([...(previous?.artifacts ?? []), ...task.artifacts]),
    );
  }

  const becameTerminal =
    isTerminalSubtaskStatus(next.status) && previousStatus !== next.status;

  return { next, becameTerminal };
}

/** Notify deferred render-time updates only when the final status changed.
 * A reused task ID may appear as failed then completed in one history render.
 */
export function hasTerminalStatusChanges(
  before: ReadonlyMap<string, Subtask["status"] | undefined>,
  tasks: Record<string, Subtask>,
): boolean {
  return Array.from(before).some(
    ([id, status]) => tasks[id]?.status !== status,
  );
}
