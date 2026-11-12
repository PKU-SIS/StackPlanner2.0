"""Read receipts scoped to one actual delegation, never shared judgements."""

from contextvars import ContextVar

from langchain.agents.middleware import AgentMiddleware

CURRENT_DELEGATION = ContextVar("collusion_current_delegation", default=None)
READ_TOOLS = {"audit_m01_index", "audit_m01_candidates", "audit_read_complete", "audit_read_result", "audit_collection_overview", "project_search", "project_read", "read_file", "knowledge_read", "ragflow_retrieve"}


class DelegationReadScopeMiddleware(AgentMiddleware):
    def __init__(self, action_id):
        self.action_id = action_id

    def wrap_tool_call(self, request, handler):
        token = CURRENT_DELEGATION.set(self.action_id)
        try:
            return handler(request)
        finally:
            CURRENT_DELEGATION.reset(token)

    async def awrap_tool_call(self, request, handler):
        token = CURRENT_DELEGATION.set(self.action_id)
        try:
            return await handler(request)
        finally:
            CURRENT_DELEGATION.reset(token)
