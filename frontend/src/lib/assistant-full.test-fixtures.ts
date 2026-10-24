import type { AssistantStateV2 } from "./assistant";
import { CHAT_ID, OP_ID } from "./chat-test-fixtures";

export function fullAssistantStateFixture(): AssistantStateV2 {
  return { schema_version: "assistant-state-v2", conversation_id: CHAT_ID, revision: 2, preferences: [], skills: [], migration_ready: true,
    task: { id: OP_ID, state: "waiting", model_calls: 10, tool_calls: 12, stop_reason: "awaiting_user",
      steps: Array.from({ length: 12 }, (_, i) => ({ name: `step_${i}`, state: "succeeded", elapsed_ms: i })),
      execution_receipt: { schema_version: "travel-execution-receipt-v1",
        budget: { version: "travel-task-budget-v1", tier: "reference", model_limit: 16, tool_limit: 16, output_tokens_reserved_limit: 24576,
          deadline_seconds: 90, shared: true, real_call_authorization: false },
        reservations: { models: 10, tools: 12, output_tokens: 12000 },
        dispatch: { status: "observed", sent: 9, received: 8, failed: 1, cancelled: 0, uncertain: 0 }, trace_complete: true } } };
}
