export type ReportDialogueBinding = { enabled: true; report_id: string };
export type ReportDialogueSelection = {
  threadId: string;
  binding: ReportDialogueBinding;
} | null;

export type ReportDialogueView = {
  available: boolean;
  binding: ReportDialogueBinding | null;
  head_version_id: string | null;
  active_version_id: string | null;
  versions: {
    version_id: string;
    report_id: string;
    is_active: boolean;
    is_head: boolean;
  }[];
  current_turn: {
    message_id: string;
    status: string;
    native_delivery_created: boolean;
    response_only: boolean;
  } | null;
};

/** A selection belongs to one thread; normal submissions retain their kwargs. */
export function dialogueMessageKwargs(
  threadId: string,
  selection: ReportDialogueSelection,
  original?: Record<string, unknown>,
): Record<string, unknown> | undefined {
  if (selection?.threadId !== threadId) return original;
  return { ...original, report_dialogue: selection.binding };
}

export function dialogueOutcome(
  view: ReportDialogueView,
  running: boolean,
  zh: boolean,
): string {
  if (running) return zh ? "正在处理本轮" : "Processing this turn";
  const turn = view.current_turn;
  if (!turn) return "";
  const labels: Record<string, [string, string]> = {
    preparing: ["正在准备", "Preparing"],
    prepared: ["副本已准备，尚未交付", "Copy prepared; not delivered"],
    executing: ["正在修改，尚未交付", "Editing; not delivered"],
    failed: [
      "本轮未完成，保留原版本",
      "Turn failed; previous version preserved",
    ],
    cancelled: [
      "本轮已取消，保留原版本",
      "Turn cancelled; previous version preserved",
    ],
    read_only: ["回答已归档，未生成新版本", "Answer archived; no new version"],
    exact_reuse: ["已恢复所选版本", "Selected version restored"],
  };
  if (turn.status === "accepted" && turn.native_delivery_created)
    return zh ? "新版本已交付" : "New version delivered";
  return (
    labels[turn.status]?.[zh ? 0 : 1] ??
    (zh ? "本轮状态待确认" : "Turn status unconfirmed")
  );
}
