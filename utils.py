# utils.py - 工具函数和数据模型模块
# 包含通用辅助函数、Pydantic 模型和自定义异常

import json
import logging
import random
import time
from typing import List, Optional, Union, Set, Dict, Any

from pydantic import BaseModel


# --- 自定义异常 ---
class ClientDisconnectedError(Exception):
    """客户端断开连接异常"""
    pass


# --- Pydantic 数据模型 ---
class FunctionCall(BaseModel):
    """函数调用模型"""
    name: str
    arguments: str


class ToolCall(BaseModel):
    """工具调用模型"""
    id: str
    type: str = "function"
    function: FunctionCall


class MessageContentItem(BaseModel):
    """消息内容项模型"""
    type: str
    text: Optional[str] = None


class Message(BaseModel):
    """消息模型"""
    role: str
    content: Union[str, List[MessageContentItem], None] = None
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """聊天完成请求模型"""
    messages: List[Message]
    model: Optional[str] = None  # 注意：重构前使用 MODEL_NAME 作为默认值，但为了避免循环导入，这里保持 None
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None
    top_p: Optional[float] = None


# --- 工具函数 ---
def generate_random_string(length: int) -> str:
    """生成指定长度的随机字符串（与重构前完全一致的实现）"""
    charset = "abcdefghijklmnopqrstuvwxyz0123456789"
    return ''.join(random.choice(charset) for _ in range(length))


def prepare_combined_prompt(messages: List[Message], req_id: str) -> str:
    """
    将消息列表组合成单个提示字符串
    
    Args:
        messages: 消息列表
        req_id: 请求ID
        
    Returns:
        组合后的提示字符串
    """
    logger = logging.getLogger("AIStudioProxyServer")
    logger.info(f"[{req_id}] (准备提示) 正在从 {len(messages)} 条消息准备组合提示 (包括历史)。")
    
    combined_parts = []
    system_prompt_content: Optional[str] = None
    processed_system_message_indices: Set[int] = set()
    
    # 处理系统消息
    for i, msg in enumerate(messages):
        if msg.role == 'system':
            if isinstance(msg.content, str) and msg.content.strip():
                system_prompt_content = msg.content.strip()
                processed_system_message_indices.add(i)
                logger.info(f"[{req_id}] (准备提示) 在索引 {i} 找到并使用系统提示: '{system_prompt_content[:80]}...'")
                system_instr_prefix = "系统指令:\n"
                combined_parts.append(f"{system_instr_prefix}{system_prompt_content}")
            else:
                logger.info(f"[{req_id}] (准备提示) 在索引 {i} 忽略非字符串或空的系统消息。")
                processed_system_message_indices.add(i)
            break
    
    # 角色映射
    role_map_ui = {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}
    turn_separator = "\n---\n"
    
    # 处理其他消息
    for i, msg in enumerate(messages):
        if i in processed_system_message_indices:
            continue
        if msg.role == 'system':
            logger.info(f"[{req_id}] (准备提示) 跳过在索引 {i} 的后续系统消息。")
            continue
        
        if combined_parts:
            combined_parts.append(turn_separator)
        
        role_prefix_ui = f"{role_map_ui.get(msg.role, msg.role.capitalize())}:\n"
        current_turn_parts = [role_prefix_ui]
        
        # 处理消息内容
        content_str = ""
        if isinstance(msg.content, str):
            content_str = msg.content.strip()
        elif isinstance(msg.content, list):
            text_parts = []
            for item_model in msg.content:
                if isinstance(item_model, dict):
                    item_type = item_model.get('type')
                    if item_type == 'text' and isinstance(item_model.get('text'), str):
                        text_parts.append(item_model['text'])
                    else:
                        logger.warning(f"[{req_id}] (准备提示) 警告: 在索引 {i} 的消息中忽略非文本或未知类型的 content item: 类型={item_type}")
                elif isinstance(item_model, MessageContentItem):
                    if item_model.type == 'text' and isinstance(item_model.text, str):
                        text_parts.append(item_model.text)
                    else:
                        logger.warning(f"[{req_id}] (准备提示) 警告: 在索引 {i} 的消息中忽略非文本或未知类型的 content item: 类型={item_model.type}")
            content_str = "\n".join(text_parts).strip()
        elif msg.content is None and msg.role == 'assistant' and hasattr(msg, 'tool_calls') and msg.tool_calls:
            pass
        elif msg.content is None and msg.role == 'tool':
            logger.warning(f"[{req_id}] (准备提示) 警告: 角色 'tool' 在索引 {i} 的 content 为 None，这通常不符合预期。")
        else:
            logger.warning(f"[{req_id}] (准备提示) 警告: 角色 {msg.role} 在索引 {i} 的内容类型意外 ({type(msg.content)}) 或为 None。将尝试转换为空字符串。")
            content_str = str(msg.content or "").strip()
        
        if content_str:
            current_turn_parts.append(content_str)
        
        # 处理工具调用
        if msg.role == 'assistant' and hasattr(msg, 'tool_calls') and msg.tool_calls:
            if content_str:
                current_turn_parts.append("\n")
            tool_call_visualizations = []
            if msg.tool_calls:
                for tool_call in msg.tool_calls:
                    if isinstance(tool_call, dict) and tool_call.get('type') == 'function':
                        function_call = tool_call.get('function')
                        if isinstance(function_call, dict):
                            func_name = function_call.get('name')
                            func_args_str = function_call.get('arguments')
                            try:
                                parsed_args = json.loads(func_args_str if func_args_str else '{}')
                                formatted_args = json.dumps(parsed_args, indent=2, ensure_ascii=False)
                            except (json.JSONDecodeError, TypeError):
                                formatted_args = func_args_str if func_args_str is not None else "{}"
                            tool_call_visualizations.append(
                                f"请求调用函数: {func_name}\n参数:\n{formatted_args}"
                            )
            if tool_call_visualizations:
                current_turn_parts.append("\n".join(tool_call_visualizations))
        
        # 处理工具响应
        if msg.role == 'tool' and hasattr(msg, 'tool_call_id') and msg.tool_call_id:
            if hasattr(msg, 'name') and msg.name and content_str:
                pass
            elif not content_str:
                logger.warning(f"[{req_id}] (准备提示) 警告: 角色 'tool' (ID: {msg.tool_call_id}, Name: {getattr(msg, 'name', 'N/A')}) 在索引 {i} 的 content 为空，这通常表示函数执行无字符串输出或结果未提供。")
        
        if len(current_turn_parts) > 1 or (msg.role == 'assistant' and hasattr(msg, 'tool_calls') and msg.tool_calls):
            combined_parts.append("".join(current_turn_parts))
        elif not combined_parts and not current_turn_parts:
            logger.info(f"[{req_id}] (准备提示) 跳过角色 {msg.role} 在索引 {i} 的空消息 (且无工具调用)。")
        elif len(current_turn_parts) == 1 and not combined_parts:
            logger.info(f"[{req_id}] (准备提示) 跳过角色 {msg.role} 在索引 {i} 的空消息 (只有前缀)。")
    
    final_prompt = "".join(combined_parts)
    if final_prompt:
        final_prompt += "\n"
    
    preview_text = final_prompt[:300].replace('\n', '\\n')
    logger.info(f"[{req_id}] (准备提示) 组合提示长度: {len(final_prompt)}。预览: '{preview_text}...'")
    
    return final_prompt


def validate_chat_request(messages: List[Message], req_id: str) -> Dict[str, Optional[str]]:
    """
    验证聊天请求的有效性
    
    Args:
        messages: 消息列表
        req_id: 请求ID
        
    Returns:
        验证结果字典
        
    Raises:
        ValueError: 当请求无效时
    """
    logger = logging.getLogger("AIStudioProxyServer")
    
    if not messages:
        raise ValueError(f"[{req_id}] 无效请求: 'messages' 数组缺失或为空。")
    
    if not any(msg.role != 'system' for msg in messages):
        raise ValueError(f"[{req_id}] 无效请求: 未找到用户或助手消息。")
    
    logger.info(f"[{req_id}] (校验) 对 {len(messages)} 条消息的基本校验通过。")
    return {}





def generate_sse_chunk(delta: str, req_id: str, model: str) -> str:
    """生成 SSE 数据块"""
    chunk = {
        "id": f"chatcmpl-{req_id}-{int(time.time())}-{random.randint(100, 999)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk)}\n\n"


def generate_sse_stop_chunk(req_id: str, model: str, reason: str = "stop") -> str:
    """生成 SSE 停止块"""
    chunk = {
        "id": f"chatcmpl-{req_id}-{int(time.time())}-{random.randint(100, 999)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]
    }
    return f"data: {json.dumps(chunk)}\n\n"


def generate_sse_error_chunk(message: str, req_id: str, error_type: str = "server_error") -> str:
    """生成 SSE 错误块"""
    error_payload = {"error": {"message": f"[{req_id}] {message}", "type": error_type}}
    return f"data: {json.dumps(error_payload)}\n\n"
