from pydantic import BaseModel


class InstructionItem(BaseModel):
    instruction_text: str
    tool_name: str | None = None
    display_order: int = 0


class CreateAgentRequest(BaseModel):
    name: str
    kb_url: str
    instructions: list[InstructionItem] = []


class UpdateAgentRequest(BaseModel):
    name: str
    kb_url: str
    instructions: list[InstructionItem] = []
    reindex: bool = False


class AgentResponse(BaseModel):
    id: str
    name: str
    kb_url: str
    instructions: list[dict]
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
