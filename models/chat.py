from typing import List, Optional, Union
from pydantic import BaseModel
from config import MODEL_NAME


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: FunctionCall


class MessageContentItem(BaseModel):
    type: str
    text: Optional[str] = None


class Message(BaseModel):
    role: str
    content: Union[str, List[MessageContentItem], None] = None
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    messages: List[Message]
    model: Optional[str] = MODEL_NAME
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None
    top_p: Optional[float] = None 