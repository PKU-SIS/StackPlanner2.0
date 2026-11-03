import { expect, test } from "@rstest/core";

import {
  hydratePipelineTasks,
  visiblePipelineNodes,
} from "@/core/tasks/pipeline";
import type { ReportView } from "@/core/threads/types";

const nodes = [
  {
    node_id: "n1",
    title: "Alpha",
    status: "in_flight" as const,
    task_id: "spact_writer-one",
    run_id: "run1",
  },
  { node_id: "n2", title: "Beta", status: "pending" as const },
];
const view = {
  status: "running",
  writer_pipeline: { max_concurrent: 4, nodes },
} as ReportView;
test("hides planned chapters until a Writer is dispatched, retaining finished history", () => {
  const planned = {
    ...view,
    writer_pipeline: {
      max_concurrent: 4,
      nodes: nodes.map((node) => ({
        ...node,
        status: "pending" as const,
        task_id: null,
      })),
    },
  };
  expect(visiblePipelineNodes(planned, true, new Set())).toEqual([]);
  expect(visiblePipelineNodes(planned, false, new Set())).toEqual([]);
  for (const status of ["completed", "failed", "stopped"] as const) {
    expect(
      visiblePipelineNodes(
        {
          ...view,
          status: "delivered",
          writer_pipeline: {
            max_concurrent: 4,
            nodes: [{ ...nodes[0]!, status }],
          },
        },
        false,
        new Set(),
      ),
    ).toHaveLength(1);
  }
});
test("shows host tasks, deduplicates anchored IDs and hides stale report views", () => {
  expect(visiblePipelineNodes(view, true, new Set()).length).toBe(2);
  expect(visiblePipelineNodes(view, true, new Set(["writer-one"])).length).toBe(
    1,
  );
  expect(
    visiblePipelineNodes({ ...view, status: "delivered" }, true, new Set()),
  ).toEqual([]);
  expect(
    visiblePipelineNodes({ ...view, status: "not_report" }, false, new Set()),
  ).toEqual([]);
});
test("hydrates once and retains live steps when ledger accepts completion", () => {
  const initial = hydratePipelineTasks({}, nodes, "failed");
  expect(initial["spact_writer-one"]?.status).toBe("in_progress");
  expect(hydratePipelineTasks(initial, nodes, "failed")).toBe(initial);
  const withSteps = {
    ...initial,
    "spact_writer-one": {
      ...initial["spact_writer-one"]!,
      steps: [
        { kind: "tool" as const, message_index: 1, text: "read evidence" },
      ],
    },
  };
  const final = hydratePipelineTasks(
    withSteps,
    [{ ...nodes[0]!, status: "completed" }],
    "failed",
  );
  expect(final["spact_writer-one"]?.status).toBe("completed");
  expect(final["spact_writer-one"]?.steps).toBe(
    withSteps["spact_writer-one"]?.steps,
  );
});
