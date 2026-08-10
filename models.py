from pydantic import BaseModel


class CreateAgentRequest(BaseModel):
    name: str
    # Required: every agent has a knowledge base.
    kb_url: str
    # Optional. Names, not UUIDs: the catalog is seeded from code, so names are
    # stable and readable, and the frontend already has them from
    # GET /api/mcp-servers.
    mcp_server_names: list[str] = []


class UpdateAgentRequest(BaseModel):
    name: str
    kb_url: str
    mcp_server_names: list[str] = []
    reindex: bool = False


class AgentResponse(BaseModel):
    id: str
    name: str
    kb_url: str | None
    mcp_server_names: list[str]
    status: str
    error_message: str | None
    last_indexed_at: str | None
    created_at: str
    updated_at: str


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class ChatResponse(BaseModel):
    reply: str
    references: list[dict] = []
    related_questions: list[dict] = []
    tool_calls: list[dict] = []


class CreateMistakeRequest(BaseModel):
    user_message: str
    bot_response: str
    user_description: str | None = None


class MistakeResponse(BaseModel):
    id: str
    agent_id: str
    user_message: str
    bot_response: str
    user_description: str | None
    status: str
    fix_comment: str | None
    verified_response: str | None
    created_at: str
    resolved_at: str | None
