export type DebugTraceFilter =
  | "all"
  | "central"
  | "subagents"
  | "tools"
  | "middleware"
  | "errors";

export interface DebugTraceTokenUsage {
  input: number;
  output: number;
  total: number;
}

export interface DebugTraceStep {
  id: string;
  seq: number | null;
  parent_id: string | null;
  kind: string;
  label: string;
  actor: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  offset_ms: number | null;
  tokens: DebugTraceTokenUsage | null;
  summary: string | null;
  detail: unknown;
  error: string | null;
  provider_reasoning?: string | null;
}

export interface DebugTraceResponse {
  thread_id: string;
  run_id: string;
  status: string;
  enabled: boolean;
  capture_level: "baseline" | "enhanced";
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  event_count: number;
  stop_reason: string;
  stop_detail: string | null;
  last_stage: string | null;
  last_action_id: string | null;
  last_event_type: string | null;
  truncated: boolean;
  tokens: {
    input: number;
    output: number;
    total: number;
    llm_calls: number;
    lead_agent: number;
    subagent: number;
    middleware: number;
  };
  steps: DebugTraceStep[];
  disclosure: {
    hidden_chain_of_thought: false;
    provider_returned_reasoning?: boolean;
    reasoning_notice?: string | null;
    shows: string[];
  };
}
