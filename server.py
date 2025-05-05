# server.py
import asyncio
import random
import time
import json
from typing import List, Optional, Dict, Any, Union, AsyncGenerator, Tuple, Callable # Add Tuple, Callable
import os
import traceback
from contextlib import asynccontextmanager
import sys
import platform
# --- 新增: 日志相关导入 ---
import logging
import logging.handlers
# -----------------------
from asyncio import Queue, Lock, Future, Task, Event # Add Queue, Lock, Future, Task, Event

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
# --- 新增: WebSocket 导入 ---
from fastapi import WebSocket, WebSocketDisconnect
# -------------------------
from pydantic import BaseModel, Field
from playwright.async_api import Page as AsyncPage, Browser as AsyncBrowser, Playwright as AsyncPlaywright, Error as PlaywrightAsyncError, expect as expect_async, BrowserContext as AsyncBrowserContext, Locator
from playwright.async_api import async_playwright
from urllib.parse import urljoin, urlparse # << Add urlparse
import uuid
import datetime

# --- 全局日志控制配置 ---
DEBUG_LOGS_ENABLED = os.environ.get('DEBUG_LOGS_ENABLED', 'false').lower() in ('true', '1', 'yes')
TRACE_LOGS_ENABLED = os.environ.get('TRACE_LOGS_ENABLED', 'false').lower() in ('true', '1', 'yes')
LOG_INTERVAL = int(os.environ.get('LOG_INTERVAL', '20'))
LOG_TIME_INTERVAL = float(os.environ.get('LOG_TIME_INTERVAL', '3.0'))

# --- Configuration ---
AI_STUDIO_URL_PATTERN = 'aistudio.google.com/'
RESPONSE_COMPLETION_TIMEOUT = 300000 # 5 minutes total timeout (in ms)
POLLING_INTERVAL = 300 # ms
POLLING_INTERVAL_STREAM = 180 # ms
SILENCE_TIMEOUT_MS = 10000 # ms
POST_SPINNER_CHECK_DELAY_MS = 500
FINAL_STATE_CHECK_TIMEOUT_MS = 1500
SPINNER_CHECK_TIMEOUT_MS = 1000
POST_COMPLETION_BUFFER = 700
CLEAR_CHAT_VERIFY_TIMEOUT_MS = 5000
CLEAR_CHAT_VERIFY_INTERVAL_MS = 400
CLICK_TIMEOUT_MS = 5000
CLIPBOARD_READ_TIMEOUT_MS = 5000
PSEUDO_STREAM_DELAY = 0.001
EDIT_MESSAGE_BUTTON_SELECTOR = 'ms-chat-turn:last-child .actions-container button.toggle-edit-button'
MESSAGE_TEXTAREA_SELECTOR = 'ms-chat-turn:last-child ms-text-chunk ms-autosize-textarea'
FINISH_EDIT_BUTTON_SELECTOR = 'ms-chat-turn:last-child .actions-container button.toggle-edit-button[aria-label="Stop editing"]'

# --- Configuration ---
AUTH_PROFILES_DIR = os.path.join(os.path.dirname(__file__), 'auth_profiles')
ACTIVE_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'active')
SAVED_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'saved')
# --- 新增: 日志文件路径 ---
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
LOG_FILE_PATH = os.path.join(LOG_DIR, 'app.log')
# -----------------------

# --- Constants ---
MODEL_NAME = 'AI-Studio_Camoufox-Proxy'
CHAT_COMPLETION_ID_PREFIX = 'chatcmpl-'

# --- Selectors ---
INPUT_SELECTOR = 'ms-prompt-input-wrapper textarea'
SUBMIT_BUTTON_SELECTOR = 'button[aria-label="Run"]'
RESPONSE_CONTAINER_SELECTOR = 'ms-chat-turn .chat-turn-container.model'
RESPONSE_TEXT_SELECTOR = 'ms-cmark-node.cmark-node'
LOADING_SPINNER_SELECTOR = 'button[aria-label="Run"] svg .stoppable-spinner'
ERROR_TOAST_SELECTOR = 'div.toast.warning, div.toast.error'
CLEAR_CHAT_BUTTON_SELECTOR = 'button[aria-label="Clear chat"][data-test-clear="outside"]:has(span.material-symbols-outlined:has-text("refresh"))'
CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR = 'button.mdc-button:has-text("Continue")'
MORE_OPTIONS_BUTTON_SELECTOR = 'div.actions-container div ms-chat-turn-options div > button'
COPY_MARKDOWN_BUTTON_SELECTOR = 'div[class*="mat-menu"] div > button:nth-child(4)'
COPY_MARKDOWN_BUTTON_SELECTOR_ALT = 'div[role="menu"] button:has-text("Copy Markdown")'

# --- Global State ---
playwright_manager: Optional[AsyncPlaywright] = None
browser_instance: Optional[AsyncBrowser] = None
page_instance: Optional[AsyncPage] = None
is_playwright_ready = False
is_browser_connected = False
is_page_ready = False
is_initializing = False

request_queue: Queue = Queue()
processing_lock: Lock = Lock()
worker_task: Optional[Task] = None
# --- 新增: WebSocket 连接管理器 ---
class WebSocketConnectionManager:
    def __init__(self):
        self.active_connections = {}  # 使用字典，client_id 作为键，WebSocket 作为值

    async def connect(self, client_id, websocket):
        self.active_connections[client_id] = websocket
        logger.info(f"WebSocket 客户端已连接: {client_id}")

    def disconnect(self, client_id):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"WebSocket 客户端已断开: {client_id}")

    async def broadcast(self, message):
        # 使用字典的 items() 创建副本进行迭代，防止在迭代过程中修改字典
        disconnected_clients = []
        active_conns_copy = list(self.active_connections.items())
        # logger.debug(f"[WS Broadcast] Preparing to broadcast to {len(active_conns_copy)} client(s). Message starts with: {message[:80]}...") # Debug log (Removed)

        for client_id, connection in active_conns_copy:
            # logger.debug(f"[WS Broadcast] Attempting to send to client {client_id}...") # Debug log (Removed)
            try:
                await connection.send_text(message)
                # logger.debug(f"[WS Broadcast] Sent successfully to client {client_id}.") # Debug log (Removed)
            except WebSocketDisconnect:
                logger.info(f"[WS Broadcast] Client {client_id} disconnected during broadcast.") # Info log
                disconnected_clients.append(client_id)
            except RuntimeError as e: # 处理连接已关闭的错误
                 if "Connection is closed" in str(e):
                     logger.info(f"[WS Broadcast] Client {client_id} connection already closed.") # Info log
                     disconnected_clients.append(client_id)
                 else:
                     logger.error(f"广播到 WebSocket {client_id} 时出错 (RuntimeError): {e}")
                     disconnected_clients.append(client_id) # Also disconnect on other RuntimeErrors
            except Exception as e:
                logger.error(f"广播到 WebSocket {client_id} 时出错 (Exception): {e}")
                disconnected_clients.append(client_id)
        # 清理已断开的连接
        if disconnected_clients:
             logger.info(f"[WS Broadcast] Cleaning up disconnected clients: {disconnected_clients}") # Info log
             for client_id in disconnected_clients:
                 self.disconnect(client_id)

log_ws_manager = WebSocketConnectionManager()
# ------------------------------------

# --- 新增: StreamToLogger 类，用于重定向 print ---
class StreamToLogger:
    """
    伪文件流对象，将写入重定向到日志实例。
    """
    def __init__(self, logger_instance, log_level=logging.INFO):
        self.logger = logger_instance
        self.log_level = log_level
        self.linebuf = ''

    def write(self, buf):
        try:
            temp_linebuf = self.linebuf + buf
            self.linebuf = ''
            for line in temp_linebuf.splitlines(True):
                if line.endswith(('\\n', '\\r')):
                    self.logger.log(self.log_level, line.rstrip())
                else:
                    self.linebuf += line # 保留不完整行
        except Exception as e:
            # 如果日志失败，回退到原始 stderr
            print(f"StreamToLogger 错误: {e}", file=sys.__stderr__)

    def flush(self):
        try:
            if self.linebuf != '':
                self.logger.log(self.log_level, self.linebuf.rstrip())
            self.linebuf = ''
        except Exception as e:
            print(f"StreamToLogger Flush 错误: {e}", file=sys.__stderr__)

    def isatty(self):
        # 一些库检查这个，返回 False 避免问题
        return False

# --- 新增: WebSocketLogHandler 类 ---
class WebSocketLogHandler(logging.Handler):
    """
    将日志记录广播到 WebSocket 客户端的处理程序。
    """
    def __init__(self, manager: WebSocketConnectionManager):
        super().__init__()
        self.manager = manager
        self.formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s') # WebSocket 使用简单格式

    def emit(self, record: logging.LogRecord):
        # 仅当有连接时才尝试广播
        if self.manager.active_connections:
            try:
                log_entry = self.format(record)
                # 使用 asyncio.create_task 在事件循环中异步发送
                try:
                     loop = asyncio.get_running_loop()
                     loop.create_task(self.manager.broadcast(log_entry))
                except RuntimeError:
                     # 如果没有运行的事件循环（例如在关闭期间），则忽略
                     pass
            except Exception as e:
                # 这里打印错误到原始 stderr，以防日志系统本身出问题
                print(f"WebSocketLogHandler 错误: 广播日志失败 - {e}", file=sys.__stderr__)

# --- 新增: 日志设置函数 ---
def setup_logging(log_level=logging.INFO, redirect_print=False): # <-- 默认改为 False
    """配置全局日志记录"""
    # ... (目录创建不变) ...
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(ACTIVE_AUTH_DIR, exist_ok=True)
    os.makedirs(SAVED_AUTH_DIR, exist_ok=True)

    # --- 文件日志格式 (详细) ---
    file_log_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s'
    )
    # --- 控制台日志格式 (简洁) ---
    console_log_formatter = logging.Formatter('%(message)s')

    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    root_logger.setLevel(log_level) # <-- Revert back to INFO (or original log_level)

    # 1. Rotating File Handler (使用详细格式)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE_PATH, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setFormatter(file_log_formatter)
    root_logger.addHandler(file_handler)

    # 2. Stream Handler (to stderr, 使用简洁格式)
    stream_handler = logging.StreamHandler(sys.__stderr__) # 直接输出到原始 stderr
    stream_handler.setFormatter(console_log_formatter) # <-- 使用简洁格式
    root_logger.addHandler(stream_handler)

    # 3. WebSocket Handler (格式保持不变或根据需要调整)
    ws_handler = WebSocketLogHandler(log_ws_manager)
    ws_handler.setLevel(logging.INFO) # 可以为 WS Handler 设置不同的级别
    root_logger.addHandler(ws_handler)

    # --- 按需重定向 print ---
    if redirect_print:
        print("--- 注意：正在重定向 print 输出到日志系统 ---", file=sys.__stderr__) # 加个提示
        # 标准输出重定向 (可选，如果希望 print 也进日志文件)
        stdout_logger = logging.getLogger('stdout')
        stdout_logger.propagate = False # 通常不希望 print 的内容重复出现在根 logger 的控制台输出
        stdout_logger.addHandler(file_handler) # print 内容进文件
        # 如果需要 print 也进 WS，取消下一行注释
        # stdout_logger.addHandler(ws_handler)
        stdout_logger.setLevel(logging.INFO)
        sys.stdout = StreamToLogger(stdout_logger, logging.INFO)

        # 标准错误重定向 (同上)
        stderr_logger = logging.getLogger('stderr')
        stderr_logger.propagate = False
        stderr_logger.addHandler(file_handler) # stderr 内容进文件
        # 如果需要 stderr 也进 WS，取消下一行注释
        # stderr_logger.addHandler(ws_handler)
        stderr_logger.setLevel(logging.ERROR)
        sys.stderr = StreamToLogger(stderr_logger, logging.ERROR)
    # else: 不重定向，print 直接输出到终端

    # --- 设置库日志级别 (保持不变) ---
    # ... (设置 uvicorn, websockets, playwright 日志级别) ...
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO) # uvicorn 错误仍然显示
    logging.getLogger("uvicorn.access").setLevel(logging.INFO) # <-- 修改回 INFO
    logging.getLogger("websockets").setLevel(logging.INFO)
    logging.getLogger("playwright").setLevel(logging.INFO) # playwright 日志也减少一些


    root_logger.info("=" * 30 + " 日志系统已初始化 " + "=" * 30)
    root_logger.info(f"日志级别: {logging.getLevelName(log_level)}")
    root_logger.info(f"日志文件: {LOG_FILE_PATH}")
    root_logger.info(f"重定向 print: {'启用' if redirect_print else '禁用'}")

# --- 新增: 日志实例 ---
logger = logging.getLogger("AIStudioProxyServer") # 获取指定名称的 logger
# ----------------------

# --- Pydantic Models ---
class MessageContentItem(BaseModel):
    type: str
    text: Optional[str] = None

class Message(BaseModel):
    role: str
    content: Union[str, List[MessageContentItem]]

class ChatCompletionRequest(BaseModel):
    messages: List[Message]
    model: Optional[str] = MODEL_NAME
    stream: Optional[bool] = False

# --- Custom Exception ---
class ClientDisconnectedError(Exception):
    pass

# --- Helper Functions ---
# V4: Combined prompt preparation logic - REPLACED with logic from server未重构.py to include history
def prepare_combined_prompt(messages: List[Message], req_id: str) -> str:
    """
    Takes the complete message list and formats it into a single string
    suitable for pasting into AI Studio, including history.
    Handles the first system message separately and formats user/assistant turns.
    (Logic adapted from server未重构.py)
    """
    print(f"[{req_id}] (Prepare Prompt) Preparing combined prompt from {len(messages)} messages (including history).") # Log updated
    combined_parts = []
    system_prompt_content = None
    processed_indices = set() # Keep track of processed messages

    # 1. Extract the first system message if it exists
    first_system_msg_index = -1
    for i, msg in enumerate(messages):
        if msg.role == 'system':
            if isinstance(msg.content, str) and msg.content.strip():
                system_prompt_content = msg.content.strip()
                processed_indices.add(i)
                first_system_msg_index = i
                # print(f"[{req_id}] (Prepare Prompt) Found system prompt at index {i}: '{system_prompt_content[:80]}...'")
                logger.info(f"[{req_id}] (Prepare Prompt) Found system prompt at index {i}: '{system_prompt_content[:80]}...'") # logger
            else:
                 # print(f"[{req_id}] (Prepare Prompt) Ignoring non-string or empty system message at index {i}.")
                 logger.warning(f"[{req_id}] (Prepare Prompt) Ignoring non-string or empty system message at index {i}.") # logger warning
                 processed_indices.add(i) # Mark as processed even if ignored
            break # Only process the first system message found

    # 2. Add system prompt preamble if found
    if system_prompt_content:
        # Add a separator only if there will be other messages following
        separator = "\\n\\n" if any(idx not in processed_indices for idx in range(len(messages))) else ""
        # 预构建带换行符的字符串，避免在f-string中使用反斜杠
        system_instr_prefix = "System Instructions:\\n"
        combined_parts.append(f"{system_instr_prefix}{system_prompt_content}{separator}")
    else:
        # print(f"[{req_id}] (Prepare Prompt) 未找到有效的系统提示，继续处理其他消息。")
        logger.info(f"[{req_id}] (Prepare Prompt) 未找到有效的系统提示，继续处理其他消息。") # logger


    # 3. Iterate through remaining messages (user and assistant roles primarily)
    turn_separator = "\\n---\\n" # Separator between turns
    is_first_turn_after_system = True # Track if it's the first message after potential system prompt
    for i, msg in enumerate(messages):
        if i in processed_indices:
            continue # Skip already processed (e.g., the system prompt)

        role = msg.role.capitalize()
        # Skip 'System' role here as we handled the first one already
        if role == 'System':
            # print(f"[{req_id}] (Prepare Prompt) Skipping subsequent system message at index {i}.")
            logger.info(f"[{req_id}] (Prepare Prompt) Skipping subsequent system message at index {i}.") # logger
            continue

        content = ""

        # Extract content, handling string or list[dict] format
        if isinstance(msg.content, str):
            content = msg.content
        elif isinstance(msg.content, list):
            text_parts = []
            # Convert MessageContentItem models to text
            for item_model in msg.content:
                 # Ensure item_model is the Pydantic model, not already a dict
                 if isinstance(item_model, MessageContentItem):
                     if item_model.type == 'text' and isinstance(item_model.text, str):
                          text_parts.append(item_model.text)
                     else:
                          # Handle non-text parts if necessary, e.g., log a warning
                           # print(f"[{req_id}] (Prepare Prompt) Warning: Ignoring non-text part in message at index {i}: type={item_model.type}")
                           logger.warning(f"[{req_id}] (Prepare Prompt) Ignoring non-text part in message at index {i}: type={item_model.type}") # logger
                 else:
                      # If it's somehow already a dict (less likely with Pydantic)
                      item_dict = dict(item_model) # Try converting
                      if item_dict.get('type') == 'text' and isinstance(item_dict.get('text'), str):
                           text_parts.append(item_dict['text'])
                      else:
                           # print(f"[{req_id}] (Prepare Prompt) Warning: Unexpected item format in message list at index {i}. Item: {item_model}")
                           logger.warning(f"[{req_id}] (Prepare Prompt) Unexpected item format in message list at index {i}. Item: {item_model}") # logger

            content = "\\n".join(text_parts)
        else:
            # print(f"[{req_id}] (Prepare Prompt) Warning: Unexpected content type ({type(msg.content)}) for role {role} at index {i}. Converting to string.")
            logger.warning(f"[{req_id}] (Prepare Prompt) Unexpected content type ({type(msg.content)}) for role {role} at index {i}. Converting to string.") # logger
            content = str(msg.content)

        content = content.strip() # Trim whitespace

        if content: # Only add non-empty messages
            # Add separator *before* the next role, unless it's the very first turn being added
            if not is_first_turn_after_system:
                 combined_parts.append(turn_separator)

            # 预构建带换行符的字符串，避免在f-string中使用反斜杠
            role_prefix = f"{role}:\\n"
            combined_parts.append(f"{role_prefix}{content}")
            is_first_turn_after_system = False # No longer the first turn
        else:
            # print(f"[{req_id}] (Prepare Prompt) Skipping empty message for role {role} at index {i}.")
            logger.info(f"[{req_id}] (Prepare Prompt) Skipping empty message for role {role} at index {i}.") # logger

    final_prompt = "".join(combined_parts)
    # Pre-calculate the preview string with escaped newlines
    preview_text = final_prompt[:200].replace('\\n', '\\\\n')
    # print(f"[{req_id}] (Prepare Prompt) Combined prompt length: {len(final_prompt)}. Preview: '{preview_text}...'") # Log preview with escaped newlines
    logger.info(f"[{req_id}] (Prepare Prompt) Combined prompt length: {len(final_prompt)}. Preview: '{preview_text}...'") # logger
    # Add a final newline if not empty, helps UI sometimes
    final_newline = "\\n"
    return final_prompt + final_newline if final_prompt else ""

# --- END V4 Combined Prompt Logic ---

def validate_chat_request(messages: List[Message], req_id: str) -> Dict[str, Optional[str]]:
    # This function now ONLY validates, prompt prep is done by prepare_combined_prompt
    if not messages:
        raise ValueError(f"[{req_id}] Invalid request: 'messages' array is missing or empty.")
    # Check if there's at least one non-system message
    if not any(msg.role != 'system' for msg in messages):
        raise ValueError(f"[{req_id}] Invalid request: No user or assistant messages found.")
    # Optional: Check for alternating user/assistant roles if needed for AI Studio
    # ... (validation logic can be added here if necessary) ...
    logger.info(f"[{req_id}] (Validation) Basic validation passed for {len(messages)} messages.")
    return {} # Return empty dict as it no longer extracts prompts

async def get_raw_text_content(response_element: Locator, previous_text: str, req_id: str) -> str:
    # ... (Existing implementation - may become less critical) ...
    raw_text = previous_text
    try:
        await response_element.wait_for(state='attached', timeout=1000)
        pre_element = response_element.locator('pre').last
        pre_found_and_visible = False
        try:
            await pre_element.wait_for(state='visible', timeout=250)
            pre_found_and_visible = True
        except PlaywrightAsyncError: pass

        if pre_found_and_visible:
            try:
                raw_text = await pre_element.inner_text(timeout=500)
            except PlaywrightAsyncError as pre_err:
                if DEBUG_LOGS_ENABLED:
                    error_message_first_line = pre_err.message.split('\n')[0]
                    # print(f"[{req_id}] (Warn) Failed to get innerText from visible <pre>: {error_message_first_line}", flush=True)
                    logger.warning(f"[{req_id}] Failed to get innerText from visible <pre>: {error_message_first_line}") # logger
                try:
                     raw_text = await response_element.inner_text(timeout=1000)
                except PlaywrightAsyncError as e_parent:
                     if DEBUG_LOGS_ENABLED:
                         # print(f"[{req_id}] (Warn) getRawTextContent (inner_text) failed on parent after <pre> fail: {e_parent}. Returning previous.", flush=True)
                         logger.warning(f"[{req_id}] getRawTextContent (inner_text) failed on parent after <pre> fail: {e_parent}. Returning previous.") # logger
                     raw_text = previous_text
        else:
            try:
                 raw_text = await response_element.inner_text(timeout=1500)
            except PlaywrightAsyncError as e_parent:
                 if DEBUG_LOGS_ENABLED:
                     # print(f"[{req_id}] (Warn) getRawTextContent (inner_text) failed on parent (no pre): {e_parent}. Returning previous.", flush=True)
                     logger.warning(f"[{req_id}] getRawTextContent (inner_text) failed on parent (no pre): {e_parent}. Returning previous.") # logger
                 raw_text = previous_text

        if raw_text and isinstance(raw_text, str):
            replacements = {
                "IGNORE_WHEN_COPYING_START": "", "content_copy": "", "download": "",
                "Use code with caution.": "", "IGNORE_WHEN_COPYING_END": ""
            }
            cleaned_text = raw_text
            found_junk = False
            for junk, replacement in replacements.items():
                if junk in cleaned_text:
                    cleaned_text = cleaned_text.replace(junk, replacement)
                    found_junk = True
            if found_junk:
                cleaned_text = "\n".join([line.strip() for line in cleaned_text.splitlines() if line.strip()])
                if DEBUG_LOGS_ENABLED:
                     # print(f"[{req_id}] (清理) 已移除响应文本中的已知UI元素。", flush=True)
                     logger.debug(f"[{req_id}] (清理) 已移除响应文本中的已知UI元素。") # logger debug
                raw_text = cleaned_text
        return raw_text
    except PlaywrightAsyncError: return previous_text
    except Exception as e_general:
         # print(f"[{req_id}] (Warn) getRawTextContent unexpected error: {e_general}. Returning previous.", flush=True)
         logger.warning(f"[{req_id}] getRawTextContent unexpected error: {e_general}. Returning previous.") # logger
         return previous_text

def generate_sse_chunk(delta: str, req_id: str, model: str) -> str:
    chunk = {
        "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}-{random.randint(100, 999)}",
        "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk)}\n\n"

def generate_sse_stop_chunk(req_id: str, model: str, reason: str = "stop") -> str:
    chunk = {
        "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}-{random.randint(100, 999)}",
        "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]
    }
    return f"data: {json.dumps(chunk)}\n\n"

def generate_sse_error_chunk(message: str, req_id: str, error_type: str = "server_error") -> str:
    error_payload = {"error": {"message": f"[{req_id}] {message}", "type": error_type}}
    return f"data: {json.dumps(error_payload)}\n\n"

# --- Dependency Check ---
def check_dependencies():
    # ... (Existing implementation) ...
    print("--- 步骤 1: 检查服务器依赖项 ---")
    required = {"fastapi": "fastapi", "uvicorn": "uvicorn[standard]", "playwright": "playwright"}
    missing = []
    modules_ok = True
    for mod_name, install_name in required.items():
        print(f"   - 检查 {mod_name}... ", end="")
        try: __import__(mod_name); print("✓ 已找到")
        except ImportError: print("❌ 未找到"); missing.append(install_name); modules_ok = False
    if not modules_ok:
        print("\n❌ 错误: 缺少必要的 Python 库!")
        print(f"   请运行以下命令安装:\n   pip install {' '.join(missing)}")
        sys.exit(1)
    else: print("✅ 服务器依赖检查通过.")
    print("---\n")

# --- Page Initialization --- (Simplified)
async def _initialize_page_logic(browser: AsyncBrowser):
    """初始化页面逻辑，连接到已有浏览器

    Args:
        browser: 已连接的浏览器实例

    Returns:
        tuple: (page_instance, is_page_ready) - 页面实例和就绪状态
    """
    print("--- 初始化页面逻辑 (连接到现有浏览器) ---")
    temp_context = None
    storage_state_path_to_use = None
    launch_mode = os.environ.get('LAUNCH_MODE', 'debug')
    active_auth_json_path = os.environ.get('ACTIVE_AUTH_JSON_PATH')
    print(f"   检测到启动模式: {launch_mode}")
    loop = asyncio.get_running_loop()

    # Determine storage state path based on launch_mode (simplified logic shown)
    if launch_mode == 'headless':
        auth_filename = os.environ.get('ACTIVE_AUTH_JSON_PATH')
        if auth_filename:
            constructed_path = os.path.join(ACTIVE_AUTH_DIR, auth_filename)
            if os.path.exists(constructed_path):
                storage_state_path_to_use = constructed_path
                print(f"   无头模式将使用的认证文件: {constructed_path}")
            else:
                raise RuntimeError(f"无头模式认证文件无效: '{constructed_path}'")
        else:
            raise RuntimeError("无头模式需要设置 ACTIVE_AUTH_JSON_PATH 环境变量。")
    elif launch_mode == 'debug':
        # ... (Logic for selecting profile in debug mode) ...
        print(f"   调试模式: 检查可用的认证文件...")
        available_profiles = []
        for profile_dir in [ACTIVE_AUTH_DIR, SAVED_AUTH_DIR]:
            if os.path.exists(profile_dir):
                try:
                    for filename in os.listdir(profile_dir):
                        if filename.endswith(".json"):
                            full_path = os.path.join(profile_dir, filename)
                            relative_dir = os.path.basename(profile_dir)
                            available_profiles.append({"name": f"{relative_dir}/{filename}", "path": full_path})
                except OSError as e: print(f"   ⚠️ 警告: 无法读取目录 '{profile_dir}': {e}")
        if available_profiles:
            print('-'*60 + "\n   找到以下可用的认证文件:")
            for i, profile in enumerate(available_profiles): print(f"     {i+1}: {profile['name']}")
            print("     N: 不加载任何文件 (使用浏览器当前状态)\n" + '-'*60)
            choice = await loop.run_in_executor(None, input, "   请选择要加载的认证文件编号 (输入 N 或直接回车则不加载): ")
            if choice.lower() != 'n' and choice:
                try:
                    choice_index = int(choice) - 1
                    if 0 <= choice_index < len(available_profiles):
                        selected_profile = available_profiles[choice_index]
                        storage_state_path_to_use = selected_profile["path"]
                        print(f"   已选择加载: {selected_profile['name']}")
                    else: print("   无效的选择编号。将不加载认证文件。")
                except ValueError: print("   无效的输入。将不加载认证文件。")
            else: print("   好的，不加载认证文件。")
            print('-'*60)
        else: print("   未找到认证文件。将使用浏览器当前状态。")
    else: print(f"   ⚠️ 警告: 未知的启动模式 '{launch_mode}'。不加载 storage_state。")

    try:
        print("创建新的浏览器上下文...")
        context_options = {'viewport': {'width': 460, 'height': 800}}
        if storage_state_path_to_use:
            context_options['storage_state'] = storage_state_path_to_use
            print(f"   (使用 storage_state='{os.path.basename(storage_state_path_to_use)}')")
        else: print("   (不使用 storage_state)")
        temp_context = await browser.new_context(**context_options)

        found_page = None
        pages = temp_context.pages
        target_url_base = f"https://{AI_STUDIO_URL_PATTERN}"
        target_full_url = f"{target_url_base}prompts/new_chat"
        login_url_pattern = 'accounts.google.com'
        current_url = ""

        # Find or create AI Studio page (simplified logic shown)
        for p in pages:
            try:
                page_url_check = p.url
                if not p.is_closed() and target_url_base in page_url_check and "/prompts/" in page_url_check:
                    found_page = p; current_url = page_url_check; break
                # Add logic to navigate existing non-chat pages if needed
            except PlaywrightAsyncError as pw_err:
                print(f"   警告: 检查页面 URL 时出现Playwright错误: {pw_err}")
            except AttributeError as attr_err:
                print(f"   警告: 检查页面 URL 时出现属性错误: {attr_err}")
            except Exception as e:
                print(f"   警告: 检查页面 URL 时出现其他未预期错误: {e}")
                print(f"   错误类型: {type(e).__name__}")

        if not found_page:
            print(f"-> 未找到合适的现有页面，正在打开新页面并导航到 {target_full_url}...")
            found_page = await temp_context.new_page()
            try:
                await found_page.goto(target_full_url, wait_until="domcontentloaded", timeout=90000)
                current_url = found_page.url
                print(f"-> 新页面导航尝试完成。当前 URL: {current_url}")
            except Exception as new_page_nav_err:
                await save_error_snapshot(f"init_new_page_nav_fail")
                # --- 新增: 检查特定网络错误并提供用户提示 ---
                error_str = str(new_page_nav_err)
                if "NS_ERROR_NET_INTERRUPT" in error_str:
                    print("\n" + "="*30 + " 网络导航错误提示 " + "="*30)
                    print(f"❌ 导航到 '{target_full_url}' 失败，出现网络中断错误 (NS_ERROR_NET_INTERRUPT)。")
                    print("   这通常表示浏览器在尝试加载页面时连接被意外断开。")
                    print("   可能的原因及排查建议:")
                    print("     1. 网络连接: 请检查你的本地网络连接是否稳定，并尝试在普通浏览器中访问目标网址。")
                    print("     2. AI Studio 服务: 确认 aistudio.google.com 服务本身是否可用。")
                    print("     3. 防火墙/代理/VPN: 检查本地防火墙、杀毒软件、代理或 VPN 设置，确保它们没有阻止 Python 或浏览器的网络访问。")
                    print("     4. Camoufox 服务: 确认 launch_camoufox.py 脚本是否正常运行，并且没有相关错误。")
                    print("     5. 资源问题: 确保系统有足够的内存和 CPU 资源。")
                    print("   请根据上述建议排查后重试。")
                    print("="*74 + "\n")
                # --- 结束新增部分 ---
                raise RuntimeError(f"导航新页面失败: {new_page_nav_err}") from new_page_nav_err

        # Handle login redirect (simplified logic shown)
        if login_url_pattern in current_url:
            if launch_mode == 'headless':
                raise RuntimeError("无头模式认证失败，需要更新认证文件。")
            else: # Debug mode
                print(f"\n{'='*20} 需要操作 {'='*20}")
                print(f"   请在浏览器窗口中完成 Google 登录，然后按 Enter 键继续...")
                await loop.run_in_executor(None, input)
                print("   感谢操作！正在检查登录状态...")
                try:
                    await found_page.wait_for_url(f"**/{AI_STUDIO_URL_PATTERN}**", timeout=180000)
                    current_url = found_page.url
                    if login_url_pattern in current_url:
                         raise RuntimeError("手动登录尝试后仍在登录页面。")
                    print("   ✅ 登录成功！请不要操作窗口，等待保存认证状态选择器启动。")
                    # Ask to save state (simplified)
                    save_prompt = "   是否要将当前的浏览器认证状态保存到文件？ (y/N): "
                    should_save = await loop.run_in_executor(None, input, save_prompt)
                    if should_save.lower() == 'y':
                        # ... (Logic to get filename and save state) ...
                        os.makedirs(SAVED_AUTH_DIR, exist_ok=True)
                        default_filename = f"auth_state_{int(time.time())}.json"
                        filename_prompt = f"   请输入保存的文件名 (默认为: {default_filename}): "
                        save_filename = await loop.run_in_executor(None, input, filename_prompt) or default_filename
                        if not save_filename.endswith(".json"): save_filename += ".json"
                        save_path = os.path.join(SAVED_AUTH_DIR, save_filename)
                        try:
                            await temp_context.storage_state(path=save_path)
                            print(f"   ✅ 认证状态已成功保存到: {save_path}")
                        except Exception as save_err: print(f"   ❌ 保存认证状态失败: {save_err}")
                    else: print("   好的，不保存认证状态。")
                except Exception as wait_err:
                    await save_error_snapshot(f"init_login_wait_fail")
                    raise RuntimeError(f"登录提示后未能检测到 AI Studio URL: {wait_err}")

        elif target_url_base not in current_url or "/prompts/" not in current_url:
            await save_error_snapshot(f"init_unexpected_page")
            raise RuntimeError(f"初始导航后出现意外页面: {current_url}。")

        print(f"-> 确认当前位于 AI Studio 对话页面: {current_url}")
        await found_page.bring_to_front()
        try:
            input_wrapper_locator = found_page.locator('ms-prompt-input-wrapper')
            await expect_async(input_wrapper_locator).to_be_visible(timeout=35000)
            await expect_async(found_page.locator(INPUT_SELECTOR)).to_be_visible(timeout=10000)
            print("-> ✅ 核心输入区域可见。")
            result_page = found_page
            result_ready = True
            print(f"✅ 页面逻辑初始化成功。")
            return result_page, result_ready
        except Exception as input_visible_err:
             await save_error_snapshot(f"init_fail_input_timeout")
             raise RuntimeError(f"页面初始化失败：核心输入区域未在预期时间内变为可见。最后的 URL 是 {found_page.url}") from input_visible_err

    except Exception as e:
        print(f"❌ 页面逻辑初始化期间发生意外错误: {e}")
        if temp_context:
            try: await temp_context.close()
            except: pass
        await save_error_snapshot(f"init_unexpected_error")
        raise RuntimeError(f"页面初始化意外错误: {e}") from e
    # Note: temp_context is intentionally not closed on success, result_page belongs to it.
    # The context will be closed when the browser connection closes during shutdown.

# --- Page Shutdown --- (Simplified)
async def _close_page_logic():
    """关闭页面并重置状态

    Returns:
        tuple: (page, is_ready) - 更新后的页面实例(None)和就绪状态(False)
    """
    global page_instance, is_page_ready
    print("--- 运行页面逻辑关闭 --- ")
    if page_instance and not page_instance.is_closed():
        try:
            await page_instance.close()
            print("   ✅ 页面已关闭")
        except PlaywrightAsyncError as pw_err:
            print(f"   ⚠️ 关闭页面时出现Playwright错误: {pw_err}")
        except asyncio.TimeoutError as timeout_err:
            print(f"   ⚠️ 关闭页面时超时: {timeout_err}")
        except Exception as other_err:
            print(f"   ⚠️ 关闭页面时出现意外错误: {other_err}")
            print(f"   错误类型: {type(other_err).__name__}")
    page_instance = None
    is_page_ready = False
    print("页面逻辑状态已重置。")
    return None, False

# --- Camoufox Shutdown Signal --- (Simplified)
async def signal_camoufox_shutdown():
    # ... (Existing implementation) ...
    try:
        print("   尝试发送关闭信号到 Camoufox 服务器...")
        ws_endpoint = os.environ.get('CAMOUFOX_WS_ENDPOINT')
        if not ws_endpoint: print("   ⚠️ 无法发送关闭信号：未找到 CAMOUFOX_WS_ENDPOINT"); return
        if not browser_instance or not browser_instance.is_connected(): print("   ⚠️ 浏览器实例已断开，跳过关闭信号发送"); return
        # Simulate signaling if direct API not available
        await asyncio.sleep(0.2)
        print("   ✅ 关闭信号已处理")
    except Exception as e: print(f"   ⚠️ 发送关闭信号过程中捕获异常: {e}")

# --- Lifespan Context Manager --- (Simplified)
@asynccontextmanager
async def lifespan(app_param: FastAPI):
    # ... (Existing implementation, ensure it calls _initialize_page_logic and starts queue_worker) ...
    global playwright_manager, browser_instance, page_instance, worker_task
    global is_playwright_ready, is_browser_connected, is_page_ready, is_initializing

    is_initializing = True
    print("\n" + "="*60 + "\n          🚀 AI Studio Proxy Server (Python/FastAPI - Refactored) 🚀\n" + "="*60)
    print(f"FastAPI 生命周期: 启动中...")
    try:
        os.makedirs(ACTIVE_AUTH_DIR, exist_ok=True); os.makedirs(SAVED_AUTH_DIR, exist_ok=True)
        print(f"   确保认证目录存在: Active: {ACTIVE_AUTH_DIR}, Saved: {SAVED_AUTH_DIR}")

        print(f"   启动 Playwright...")
        playwright_manager = await async_playwright().start()
        is_playwright_ready = True
        print(f"   ✅ Playwright 已启动。")

        ws_endpoint = os.environ.get('CAMOUFOX_WS_ENDPOINT')
        if not ws_endpoint: raise ValueError("未找到 CAMOUFOX_WS_ENDPOINT 环境变量。")

        print(f"   连接到 Camoufox 服务器于: {ws_endpoint}")
        try:
            browser_instance = await playwright_manager.firefox.connect(ws_endpoint, timeout=30000)
            is_browser_connected = True
            print(f"   ✅ 已连接到浏览器实例: 版本 {browser_instance.version}")
        except Exception as connect_err:
            raise RuntimeError(f"未能连接到 Camoufox 服务器: {connect_err}") from connect_err

        # 从初始化函数获取返回值，而不是依赖函数直接修改全局变量
        global page_instance, is_page_ready
        page_instance, is_page_ready = await _initialize_page_logic(browser_instance)

        if is_page_ready and is_browser_connected:
             print(f"   启动请求队列 Worker...")
             worker_task = asyncio.create_task(queue_worker())
             print(f"   ✅ 请求队列 Worker 已启动。")
        else:
             raise RuntimeError("页面或浏览器初始化失败，无法启动 Worker。")

        print(f"✅ FastAPI 生命周期: 启动完成。")
        is_initializing = False
        yield # Application runs here

    except Exception as startup_err:
        print(f"❌ FastAPI 生命周期: 启动期间出错: {startup_err}")
        traceback.print_exc()
        # Ensure cleanup happens
        if worker_task and not worker_task.done(): worker_task.cancel()
        if browser_instance and browser_instance.is_connected():
            try: await browser_instance.close()
            except: pass
        if playwright_manager:
            try: await playwright_manager.stop()
            except: pass
        raise RuntimeError(f"应用程序启动失败: {startup_err}") from startup_err
    finally:
        is_initializing = False
        print(f"\nFastAPI 生命周期: 关闭中...")
        # ... (Existing shutdown logic: cancel worker, close page, signal camoufox, close browser, stop playwright) ...
        if worker_task and not worker_task.done():
             print(f"   正在取消请求队列 Worker...")
             worker_task.cancel()
             try: await asyncio.wait_for(worker_task, timeout=5.0); print(f"   ✅ 请求队列 Worker 已停止/取消。")
             except asyncio.TimeoutError: print(f"   ⚠️ Worker 等待超时。")
             except asyncio.CancelledError: print(f"   ✅ 请求队列 Worker 已确认取消。")
             except Exception as wt_err: print(f"   ❌ 等待 Worker 停止时出错: {wt_err}")

        # 获取_close_page_logic返回的更新状态并设置全局变量
        page_instance, is_page_ready = await _close_page_logic()

        browser_ready_for_shutdown = bool(browser_instance and browser_instance.is_connected())
        if browser_ready_for_shutdown: await signal_camoufox_shutdown()

        if browser_instance:
            print(f"   正在关闭与浏览器实例的连接...")
            try:
                if browser_instance.is_connected(): await browser_instance.close(); print(f"   ✅ 浏览器连接已关闭。")
                else: print(f"   ℹ️ 浏览器已断开连接。")
            except Exception as close_err: print(f"   ❌ 关闭浏览器连接时出错: {close_err}")
            finally: browser_instance = None; is_browser_connected = False

        if playwright_manager:
            print(f"   停止 Playwright...")
            try: await playwright_manager.stop(); print(f"   ✅ Playwright 已停止。")
            except Exception as stop_err: print(f"   ❌ 停止 Playwright 时出错: {stop_err}")
            finally: playwright_manager = None; is_playwright_ready = False

        print(f"✅ FastAPI 生命周期: 关闭完成。")

# --- FastAPI App ---
app = FastAPI(
    title="AI Studio Proxy Server (Python/FastAPI/Camoufox - Refactored)",
    description="Refactored proxy server with unified request processing.",
    version="0.4.0-py-refactored",
    lifespan=lifespan
)

# --- Static Files & API Info ---
@app.get("/", response_class=FileResponse)
async def read_index():
    index_html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if not os.path.exists(index_html_path): raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_html_path)

@app.get("/api/info")
async def get_api_info(request: Request):
    host = request.headers.get('host') or f"127.0.0.1:8000" # Provide a default if headers missing
    scheme = request.headers.get('x-forwarded-proto', 'http')
    base_url = f"{scheme}://{host}"
    api_base = f"{base_url}/v1"
    return JSONResponse(content={
        "model_name": MODEL_NAME, "api_base_url": api_base, "server_base_url": base_url,
        "api_key_required": False, "message": "API Key is not required."
    })

# --- API Endpoints ---
@app.get("/health")
async def health_check():
    is_worker_running = bool(worker_task and not worker_task.done())
    is_core_ready = is_playwright_ready and is_browser_connected and is_page_ready
    status_val = "OK" if is_core_ready and is_worker_running else "Error"
    q_size = request_queue.qsize() if request_queue else -1
    status = {
        "status": status_val, "message": "", "playwrightReady": is_playwright_ready,
        "browserConnected": is_browser_connected, "pageReady": is_page_ready,
        "initializing": is_initializing, "workerRunning": is_worker_running, "queueLength": q_size
    }
    if status_val == "OK":
        status["message"] = f"服务运行中。队列长度: {q_size}。"
        return JSONResponse(content=status, status_code=200)
    else:
        reasons = []
        if not is_playwright_ready: reasons.append("Playwright 未初始化")
        if not is_browser_connected: reasons.append("浏览器断开")
        if not is_page_ready: reasons.append("页面未就绪")
        if not is_worker_running: reasons.append("Worker 未运行")
        if is_initializing: reasons.append("初始化进行中")
        status["message"] = f"服务不可用。问题: {(', '.join(reasons) if reasons else '未知')}. 队列长度: {q_size}."
        return JSONResponse(content=status, status_code=503)

@app.get("/v1/models")
async def list_models():
    print("[API] 收到 /v1/models 请求。")
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "created": int(time.time()), "owned_by": "camoufox-proxy"}]}

# --- Helper: Detect Error ---
async def detect_and_extract_page_error(page: AsyncPage, req_id: str) -> Optional[str]:
    error_toast_locator = page.locator(ERROR_TOAST_SELECTOR).last
    try:
        await error_toast_locator.wait_for(state='visible', timeout=500)
        message_locator = error_toast_locator.locator('span.content-text')
        error_message = await message_locator.text_content(timeout=500)
        if error_message:
             # print(f"[{req_id}]    检测到并提取错误消息: {error_message}")
             logger.error(f"[{req_id}]    检测到并提取错误消息: {error_message}") # logger
             return error_message.strip()
        else:
             # print(f"[{req_id}]    警告: 检测到错误提示框，但无法提取消息。")
             logger.warning(f"[{req_id}]    检测到错误提示框，但无法提取消息。") # logger
             return "检测到错误提示框，但无法提取特定消息。"
    except PlaywrightAsyncError: return None
    except Exception as e:
        # print(f"[{req_id}]    警告: 检查页面错误时出错: {e}")
        logger.warning(f"[{req_id}]    检查页面错误时出错: {e}") # logger
        return None

# --- Snapshot Helper --- (Simplified)
async def save_error_snapshot(error_name: str = 'error'):
    # ... (Existing implementation) ...
    name_parts = error_name.split('_')
    req_id = name_parts[-1] if len(name_parts) > 1 and len(name_parts[-1]) == 7 else None
    base_error_name = error_name if not req_id else '_'.join(name_parts[:-1])
    log_prefix = f"[{req_id}]" if req_id else "[无请求ID]"
    page_to_snapshot = page_instance
    if not browser_instance or not browser_instance.is_connected() or not page_to_snapshot or page_to_snapshot.is_closed():
        # print(f"{log_prefix} 无法保存快照 ({base_error_name})，浏览器/页面不可用。")
        logger.warning(f"{log_prefix} 无法保存快照 ({base_error_name})，浏览器/页面不可用。") # logger
        return
    # print(f"{log_prefix} 尝试保存错误快照 ({base_error_name})...")
    logger.info(f"{log_prefix} 尝试保存错误快照 ({base_error_name})...") # logger
    timestamp = int(time.time() * 1000)
    error_dir = os.path.join(os.path.dirname(__file__), 'errors_py')
    try:
        os.makedirs(error_dir, exist_ok=True)
        filename_suffix = f"{req_id}_{timestamp}" if req_id else f"{timestamp}"
        filename_base = f"{base_error_name}_{filename_suffix}"
        screenshot_path = os.path.join(error_dir, f"{filename_base}.png")
        html_path = os.path.join(error_dir, f"{filename_base}.html")
        try:
            await page_to_snapshot.screenshot(path=screenshot_path, full_page=True, timeout=15000)
            # print(f"{log_prefix}   快照已保存到: {screenshot_path}")
            logger.info(f"{log_prefix}   快照已保存到: {screenshot_path}") # logger
        except Exception as ss_err:
            # print(f"{log_prefix}   保存屏幕截图失败 ({base_error_name}): {ss_err}")
            logger.error(f"{log_prefix}   保存屏幕截图失败 ({base_error_name}): {ss_err}") # logger
        try:
            content = await page_to_snapshot.content()
            f = None
            try:
                f = open(html_path, 'w', encoding='utf-8')
                f.write(content)
                # print(f"{log_prefix}   HTML 已保存到: {html_path}")
                logger.info(f"{log_prefix}   HTML 已保存到: {html_path}") # logger
            except Exception as write_err:
                # print(f"{log_prefix}   保存 HTML 失败 ({base_error_name}): {write_err}")
                logger.error(f"{log_prefix}   保存 HTML 失败 ({base_error_name}): {write_err}") # logger
            finally:
                if f:
                    try:
                        f.close()
                        # print(f"{log_prefix}   HTML 文件已正确关闭")
                        logger.debug(f"{log_prefix}   HTML 文件已正确关闭") # logger debug
                    except Exception as close_err:
                        # print(f"{log_prefix}   关闭 HTML 文件时出错: {close_err}")
                        logger.error(f"{log_prefix}   关闭 HTML 文件时出错: {close_err}") # logger
        except Exception as html_err:
            # print(f"{log_prefix}   获取页面内容失败 ({base_error_name}): {html_err}")
            logger.error(f"{log_prefix}   获取页面内容失败 ({base_error_name}): {html_err}") # logger
    except Exception as dir_err:
        # print(f"{log_prefix}   创建错误目录或保存快照时出错: {dir_err}")
            print(f"{log_prefix}   获取页面内容失败 ({base_error_name}): {html_err}")
    except Exception as dir_err: print(f"{log_prefix}   创建错误目录或保存快照时出错: {dir_err}")

# --- V4: New Helper - Get response via Edit Button ---
async def get_response_via_edit_button(
    page: AsyncPage,
    req_id: str,
    check_client_disconnected: Callable
) -> Optional[str]:
    """Attempts to get the response content using the edit button.
       Implementation mirrors original stream logic closely.
    """
    print(f"[{req_id}] (Helper) 尝试通过编辑按钮获取响应...", flush=True)
    edit_button = page.locator(EDIT_MESSAGE_BUTTON_SELECTOR)
    textarea = page.locator(MESSAGE_TEXTAREA_SELECTOR)
    finish_edit_button = page.locator(FINISH_EDIT_BUTTON_SELECTOR)

    try:
        # 1. Click the Edit button
        print(f"[{req_id}]   - 定位并点击编辑按钮...", flush=True)
        try:
            # Direct Playwright calls with timeout
            await expect_async(edit_button).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("编辑响应 - 编辑按钮可见后: ")
            await edit_button.click(timeout=CLICK_TIMEOUT_MS)
            print(f"[{req_id}]   - 编辑按钮已点击。", flush=True)
        except Exception as edit_btn_err:
            print(f"[{req_id}]   - ❌ 编辑按钮不可见或点击失败: {edit_btn_err}", flush=True)
            await save_error_snapshot(f"edit_response_edit_button_failed_{req_id}")
            return None

        check_client_disconnected("编辑响应 - 点击编辑按钮后: ")
        await asyncio.sleep(0.3) # Use asyncio.sleep
        check_client_disconnected("编辑响应 - 点击编辑按钮后延时后: ")

        # 2. Get content from textarea
        print(f"[{req_id}]   - 从文本区域获取内容...", flush=True)
        response_content = None
        textarea_failed = False # Flag to track if textarea read failed
        try:
            # Direct Playwright call with timeout
            await expect_async(textarea).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("编辑响应 - 文本区域可见后: ")

            # Try getting content from data-value attribute first
            # print(f"[{req_id}]   - 尝试获取 data-value 属性...", flush=True)
            # logger.debug(f"[{req_id}]   - 尝试获取 data-value 属性...") # logger debug (Removed)
            try:
                # Direct evaluate call (no specific timeout in Playwright evaluate)
                data_value_content = await textarea.evaluate('el => el.getAttribute("data-value")')
                check_client_disconnected("编辑响应 - evaluate data-value 后: ")
                if data_value_content is not None:
                    response_content = str(data_value_content)
                    # print(f"[{req_id}]   - 成功从 data-value 获取。", flush=True)
                    # logger.debug(f"[{req_id}]   - 成功从 data-value 获取。") # logger debug (Removed)
            except Exception as data_val_err:
                # print(f"[{req_id}]   - 获取 data-value 失败: {data_val_err}", flush=True)
                logger.warning(f"[{req_id}]   - 获取 data-value 失败: {data_val_err}") # logger warning
                check_client_disconnected("编辑响应 - evaluate data-value 错误后: ")

            # If data-value failed or returned empty, try input_value
            if not response_content:
                # print(f"[{req_id}]   - data-value 失败或为空，尝试 input_value...", flush=True)
                # logger.debug(f"[{req_id}]   - data-value 失败或为空，尝试 input_value...") # logger debug (Removed)
                try:
                    # Direct input_value call with timeout
                    input_val_content = await textarea.input_value(timeout=CLICK_TIMEOUT_MS)
                    check_client_disconnected("编辑响应 - input_value 后: ")
                    if input_val_content is not None:
                        response_content = str(input_val_content)
                        # print(f"[{req_id}]   - 成功从 input_value 获取。", flush=True)
                        # logger.debug(f"[{req_id}]   - 成功从 input_value 获取。") # logger debug (Removed)
                except Exception as input_val_err:
                     # print(f"[{req_id}]   - 获取 input_value 失败: {input_val_err}", flush=True)
                     logger.warning(f"[{req_id}]   - 获取 input_value 失败: {input_val_err}") # logger warning
                     check_client_disconnected("编辑响应 - input_value 错误后: ")

            # Now check the final result from either method
            if response_content is not None and response_content.strip():
                response_content = response_content.strip()
                content_preview = response_content[:100].replace('\\n', '\\\\n')
                print(f"[{req_id}]   - ✅ 最终成功获取内容 (长度={len(response_content)}): '{content_preview}...'", flush=True)
            else:
                if response_content is None:
                    print(f"[{req_id}]   - ⚠️ 所有方法 (data-value, input_value) 内容获取均失败或返回 None。", flush=True)
                else:
                    print(f"[{req_id}]   - ⚠️ 所有方法 (data-value, input_value) 内容获取返回空字符串。", flush=True)
                textarea_failed = True
                response_content = None

        except Exception as textarea_err:
            print(f"[{req_id}]   - ❌ 定位或处理文本区域时失败: {textarea_err}", flush=True)
            textarea_failed = True
            response_content = None
            check_client_disconnected("编辑响应 - 获取文本区域错误后: ")

        # 3. Click the Finish Editing button
        if not textarea_failed:
            print(f"[{req_id}]   - 定位并点击完成编辑按钮...", flush=True)
            try:
                # Direct Playwright calls with timeout
                await expect_async(finish_edit_button).to_be_visible(timeout=CLICK_TIMEOUT_MS)
                check_client_disconnected("编辑响应 - 完成按钮可见后: ")
                await finish_edit_button.click(timeout=CLICK_TIMEOUT_MS)
                print(f"[{req_id}]   - 完成编辑按钮已点击。", flush=True)
            except Exception as finish_btn_err:
                print(f"[{req_id}]   - ⚠️ 警告: 完成编辑按钮不可见或点击失败: {finish_btn_err}", flush=True)
                await save_error_snapshot(f"edit_response_finish_button_failed_{req_id}")

            check_client_disconnected("编辑响应 - 点击完成编辑后: ")
            await asyncio.sleep(0.2) # Use asyncio.sleep
            check_client_disconnected("编辑响应 - 点击完成编辑后延时后: ")
        else:
             print(f"[{req_id}]   - 跳过点击完成编辑按钮，因为文本区域读取失败。")

        return response_content if not textarea_failed else None

    except ClientDisconnectedError:
        print(f"[{req_id}] (Helper Edit) 客户端断开连接。", flush=True)
        raise
    except Exception as e:
        print(f"[{req_id}] ❌ 通过编辑按钮获取响应过程中发生意外错误: {e}", flush=True)
        traceback.print_exc()
        await save_error_snapshot(f"edit_response_unexpected_error_{req_id}")
        return None

# --- V4: New Helper - Get response via Copy Button ---
async def get_response_via_copy_button(
    page: AsyncPage,
    req_id: str,
    check_client_disconnected: Callable
) -> Optional[str]:
    """Attempts to get the response content using the copy markdown button.
       Implementation mirrors original stream logic closely.
    """
    # print(f"[{req_id}] (Helper) 尝试通过复制按钮获取响应...", flush=True)
    logger.info(f"[{req_id}] (Helper) 尝试通过复制按钮获取响应...") # logger
    more_options_button = page.locator(MORE_OPTIONS_BUTTON_SELECTOR).last # Target last message
    copy_button_primary = page.locator(COPY_MARKDOWN_BUTTON_SELECTOR)
    copy_button_alt = page.locator(COPY_MARKDOWN_BUTTON_SELECTOR_ALT)

    try:
        # 1. Hover over the last message to reveal options
        # print(f"[{req_id}]   - 尝试悬停最后一条消息以显示选项...", flush=True)
        logger.info(f"[{req_id}]   - 尝试悬停最后一条消息以显示选项...") # logger
        last_message_container = page.locator('ms-chat-turn').last
        try:
            # Direct hover call with timeout
            await last_message_container.hover(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("复制响应 - 悬停后: ")
            await asyncio.sleep(0.5) # Use asyncio.sleep
            check_client_disconnected("复制响应 - 悬停后延时后: ")
            # print(f"[{req_id}]   - 已悬停。", flush=True)
            logger.info(f"[{req_id}]   - 已悬停。") # logger
        except Exception as hover_err:
            # print(f"[{req_id}]   - ⚠️ 悬停失败: {hover_err}。尝试直接查找按钮...", flush=True)
            logger.warning(f"[{req_id}]   - 悬停失败: {hover_err}。尝试直接查找按钮...") # logger
            check_client_disconnected("复制响应 - 悬停失败后: ")
            # Continue, maybe buttons are already visible

        # 2. Click "More options" button
        # print(f"[{req_id}]   - 定位并点击 '更多选项' 按钮...", flush=True)
        logger.info(f"[{req_id}]   - 定位并点击 '更多选项' 按钮...") # logger
        try:
            # Direct Playwright calls with timeout
            await expect_async(more_options_button).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("复制响应 - 更多选项按钮可见后: ")
            await more_options_button.click(timeout=CLICK_TIMEOUT_MS)
            # print(f"[{req_id}]   - '更多选项' 已点击。", flush=True)
            logger.info(f"[{req_id}]   - '更多选项' 已点击。") # logger
        except Exception as more_opts_err:
            # print(f"[{req_id}]   - ❌ '更多选项' 按钮不可见或点击失败: {more_opts_err}", flush=True)
            logger.error(f"[{req_id}]   - '更多选项' 按钮不可见或点击失败: {more_opts_err}") # logger
            await save_error_snapshot(f"copy_response_more_options_failed_{req_id}")
            return None

        check_client_disconnected("复制响应 - 点击更多选项后: ")
        await asyncio.sleep(0.5) # Use asyncio.sleep
        check_client_disconnected("复制响应 - 点击更多选项后延时后: ")

        # 3. Find and click "Copy Markdown" button (try primary, then alt)
        # print(f"[{req_id}]   - 定位并点击 '复制 Markdown' 按钮...", flush=True)
        logger.info(f"[{req_id}]   - 定位并点击 '复制 Markdown' 按钮...") # logger
        copy_success = False
        try:
            # Try primary selector
            await expect_async(copy_button_primary).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("复制响应 - 主复制按钮可见后: ")
            await copy_button_primary.click(timeout=CLICK_TIMEOUT_MS, force=True)
            copy_success = True
            # print(f"[{req_id}]   - 已点击 '复制 Markdown' (主选择器)。", flush=True)
            logger.info(f"[{req_id}]   - 已点击 '复制 Markdown' (主选择器)。") # logger
        except Exception as primary_copy_err:
            # print(f"[{req_id}]   - 主选择器失败 ({primary_copy_err})，尝试备选...", flush=True)
            logger.warning(f"[{req_id}]   - 主复制按钮选择器失败 ({primary_copy_err})，尝试备选...") # logger
            check_client_disconnected("复制响应 - 主复制按钮失败后: ")
            try:
                # Try alternative selector
                await expect_async(copy_button_alt).to_be_visible(timeout=CLICK_TIMEOUT_MS)
                check_client_disconnected("复制响应 - 备选复制按钮可见后: ")
                await copy_button_alt.click(timeout=CLICK_TIMEOUT_MS, force=True)
                copy_success = True
                # print(f"[{req_id}]   - 已点击 '复制 Markdown' (备选选择器)。", flush=True)
                logger.info(f"[{req_id}]   - 已点击 '复制 Markdown' (备选选择器)。") # logger
            except Exception as alt_copy_err:
                # print(f"[{req_id}]   - ❌ 备选 '复制 Markdown' 按钮失败: {alt_copy_err}", flush=True)
                logger.error(f"[{req_id}]   - 备选 '复制 Markdown' 按钮失败: {alt_copy_err}") # logger
                await save_error_snapshot(f"copy_response_copy_button_failed_{req_id}")
                return None

        if not copy_success:
             # print(f"[{req_id}]   - ❌ 未能点击任何 '复制 Markdown' 按钮。", flush=True)
             logger.error(f"[{req_id}]   - 未能点击任何 '复制 Markdown' 按钮。") # logger
             return None

        check_client_disconnected("复制响应 - 点击复制按钮后: ")
        await asyncio.sleep(0.5) # Use asyncio.sleep
        check_client_disconnected("复制响应 - 点击复制按钮后延时后: ")

        # 4. Read clipboard content
        # print(f"[{req_id}]   - 正在读取剪贴板内容...", flush=True)
        logger.info(f"[{req_id}]   - 正在读取剪贴板内容...") # logger
        try:
            # Direct evaluate call (no specific timeout needed)
            clipboard_content = await page.evaluate('navigator.clipboard.readText()')
            check_client_disconnected("复制响应 - 读取剪贴板后: ")

            if clipboard_content:
                content_preview = clipboard_content[:100].replace('\n', '\\\\n')
                # print(f"[{req_id}]   - ✅ 成功获取剪贴板内容 (长度={len(clipboard_content)}): '{content_preview}...'", flush=True)
                logger.info(f"[{req_id}]   - ✅ 成功获取剪贴板内容 (长度={len(clipboard_content)}): '{content_preview}...'") # logger
                return clipboard_content
            else:
                # print(f"[{req_id}]   - ❌ 剪贴板内容为空。", flush=True)
                logger.error(f"[{req_id}]   - 剪贴板内容为空。") # logger
                return None
        except Exception as clipboard_err:
            if "clipboard-read" in str(clipboard_err):
                 # print(f"[{req_id}]   - ❌ 读取剪贴板失败: 可能是权限问题。错误: {clipboard_err}", flush=True) # Log adjusted
                 logger.error(f"[{req_id}]   - 读取剪贴板失败: 可能是权限问题。错误: {clipboard_err}") # logger
            else:
                 # print(f"[{req_id}]   - ❌ 读取剪贴板失败: {clipboard_err}", flush=True)
                 logger.error(f"[{req_id}]   - 读取剪贴板失败: {clipboard_err}") # logger
            await save_error_snapshot(f"copy_response_clipboard_read_failed_{req_id}")
            return None

    except ClientDisconnectedError:
        # print(f"[{req_id}] (Helper Copy) 客户端断开连接。", flush=True)
        logger.info(f"[{req_id}] (Helper Copy) 客户端断开连接。") # logger
        raise
    except Exception as e:
        # print(f"[{req_id}] ❌ 复制响应过程中发生意外错误: {e}", flush=True)
        # traceback.print_exc()
        logger.exception(f"[{req_id}] ❌ 复制响应过程中发生意外错误") # logger
        await save_error_snapshot(f"copy_response_unexpected_error_{req_id}")
        return None

# --- V5: New Helper - Wait for Response Completion --- (Based on Stream Logic)
async def _wait_for_response_completion(
    page: AsyncPage,
    req_id: str,
    response_element: Locator, # Pass the located response element
    interruptible_wait_for: Callable, # This argument is no longer used, can be removed later
    check_client_disconnected: Callable,
    interruptible_sleep: Callable # This argument is no longer used, can be removed later
) -> bool:
    """Waits for the AI Studio response to complete, primarily checking for the edit button.
       Implementation mirrors original stream logic closely.
    """
    # print(f"[{req_id}] (Helper Wait) 开始等待响应完成... (超时: {RESPONSE_COMPLETION_TIMEOUT}ms)", flush=True)
    logger.info(f"[{req_id}] (Helper Wait) 开始等待响应完成... (超时: {RESPONSE_COMPLETION_TIMEOUT}ms)") # logger
    start_time_ns = time.time()
    spinner_locator = page.locator(LOADING_SPINNER_SELECTOR)
    input_field = page.locator(INPUT_SELECTOR)
    submit_button = page.locator(SUBMIT_BUTTON_SELECTOR)
    edit_button = page.locator(EDIT_MESSAGE_BUTTON_SELECTOR)

    while time.time() - start_time_ns < RESPONSE_COMPLETION_TIMEOUT / 1000:
        check_client_disconnected("等待完成循环开始: ")

        # --- Check Base Final State Conditions (Mirroring original stream checks) ---
        spinner_hidden = False
        input_empty = False
        button_disabled = False
        state_check_error = None

        try:
            # Check Spinner hidden
            try:
                # Direct Playwright call with timeout
                await expect_async(spinner_locator).to_be_hidden(timeout=SPINNER_CHECK_TIMEOUT_MS)
                spinner_hidden = True
            except (PlaywrightAsyncError, asyncio.TimeoutError, AssertionError) as e:
                spinner_hidden = False
                state_check_error = e # Store last error for logging

            check_client_disconnected("等待完成 - Spinner检查后: ")

            # Only check others if spinner IS hidden
            if spinner_hidden:
                 # Use standard asyncio.sleep
                 await asyncio.sleep(POST_SPINNER_CHECK_DELAY_MS / 1000)
                 check_client_disconnected("等待完成 - Spinner消失后延时后: ")

                 # Check Input empty
                 try:
                     await expect_async(input_field).to_have_value('', timeout=FINAL_STATE_CHECK_TIMEOUT_MS)
                     input_empty = True
                 except (PlaywrightAsyncError, asyncio.TimeoutError, AssertionError) as e:
                      input_empty = False
                      state_check_error = e
                 check_client_disconnected("等待完成 - 输入框检查后: ")

                 # Check Button disabled
                 try:
                     await expect_async(submit_button).to_be_disabled(timeout=FINAL_STATE_CHECK_TIMEOUT_MS)
                     button_disabled = True
                 except (PlaywrightAsyncError, asyncio.TimeoutError, AssertionError) as e:
                     button_disabled = False
                     state_check_error = e
                 check_client_disconnected("等待完成 - 提交按钮检查后: ")
            # else: spinner not hidden, skip other checks

        # --- Exception Handling for State Checks (Only for truly unexpected errors) ---
        except ClientDisconnectedError: raise
        except Exception as unexpected_state_err:
             # print(f"[{req_id}] (Helper Wait) ❌ 状态检查中发生意外错误: {unexpected_state_err}", flush=True)
             # traceback.print_exc()
             logger.exception(f"[{req_id}] (Helper Wait) ❌ 状态检查中发生意外错误") # logger
             await save_error_snapshot(f"wait_completion_state_check_unexpected_{req_id}")
             await asyncio.sleep(POLLING_INTERVAL_STREAM / 1000) # Still use sleep here
             continue

        # --- Logging and Continuation Logic ---
        is_final_state = spinner_hidden and input_empty and button_disabled
        if not is_final_state:
            if DEBUG_LOGS_ENABLED:
                reason = "Spinner not hidden" if not spinner_hidden else ("Input not empty" if not input_empty else "Submit button not disabled")
                error_info = f" (Last Check Error: {type(state_check_error).__name__})" if state_check_error else ""
                # print(f"[{req_id}] (Helper Wait) 基础状态未满足 ({reason}{error_info})。继续轮询...", flush=True)
                logger.debug(f"[{req_id}] (Helper Wait) 基础状态未满足 ({reason}{error_info})。继续轮询...") # logger debug
            # Use standard asyncio.sleep with stream interval
            await asyncio.sleep(POLLING_INTERVAL_STREAM / 1000)
            continue

        # --- If base conditions met, check for Edit Button --- (Mirroring original stream logic)
        # print(f"[{req_id}] (Helper Wait) 检测到基础最终状态。开始检查编辑按钮可见性 (最长 {SILENCE_TIMEOUT_MS}ms)...", flush=True)
        logger.info(f"[{req_id}] (Helper Wait) 检测到基础最终状态。开始检查编辑按钮可见性 (最长 {SILENCE_TIMEOUT_MS}ms)...") # logger
        edit_button_check_start = time.time()
        edit_button_visible = False
        last_focus_attempt_time = 0

        while time.time() - edit_button_check_start < SILENCE_TIMEOUT_MS / 1000:
            check_client_disconnected("等待完成 - 编辑按钮检查循环: ")

            # Focus attempt logic remains similar (using interruptible for safety here is okay, or revert if strictness needed)
            current_time = time.time()
            if current_time - last_focus_attempt_time > 1.0:
                try:
                    if DEBUG_LOGS_ENABLED:
                        # print(f"[{req_id}] (Helper Wait)   - 尝试聚焦响应元素...", flush=True)
                        logger.debug(f"[{req_id}] (Helper Wait)   - 尝试聚焦响应元素...") # logger debug
                    # Revert focus click to direct call if strict matching is required
                    await response_element.click(timeout=1000, position={'x': 10, 'y': 10}, force=True)
                    last_focus_attempt_time = current_time
                    await asyncio.sleep(0.1) # Use asyncio.sleep
                except (PlaywrightAsyncError, asyncio.TimeoutError) as focus_err:
                     if DEBUG_LOGS_ENABLED:
                          # print(f"[{req_id}] (Helper Wait)   - 聚焦响应元素失败 (忽略): {type(focus_err).__name__}", flush=True)
                          logger.debug(f"[{req_id}] (Helper Wait)   - 聚焦响应元素失败 (忽略): {type(focus_err).__name__}") # logger debug
                except ClientDisconnectedError: raise
                except Exception as unexpected_focus_err:
                     # print(f"[{req_id}] (Helper Wait)   - 聚焦响应元素时意外错误 (忽略): {unexpected_focus_err}", flush=True)
                     logger.warning(f"[{req_id}] (Helper Wait)   - 聚焦响应元素时意外错误 (忽略): {unexpected_focus_err}") # logger warning
                check_client_disconnected("等待完成 - 编辑按钮循环聚焦后: ")

            # Check Edit button visibility using is_visible() directly
            try:
                is_visible = False
                try:
                    # Direct call to is_visible with timeout
                    is_visible = await edit_button.is_visible(timeout=500)
                except asyncio.TimeoutError:
                    is_visible = False # Treat timeout as not visible
                except PlaywrightAsyncError as pw_vis_err:
                    # print(f"[{req_id}] (Helper Wait)   - is_visible 检查Playwright错误(忽略): {pw_vis_err}")
                    logger.warning(f"[{req_id}] (Helper Wait)   - is_visible 检查Playwright错误(忽略): {pw_vis_err}") # logger warning
                    is_visible = False

                check_client_disconnected("等待完成 - 编辑按钮 is_visible 检查后: ")

                if is_visible:
                    # print(f"[{req_id}] (Helper Wait) ✅ 编辑按钮已出现 (is_visible)，确认响应完成。", flush=True)
                    logger.info(f"[{req_id}] (Helper Wait) ✅ 编辑按钮已出现 (is_visible)，确认响应完成。") # logger
                    edit_button_visible = True
                    return True
                else:
                      if DEBUG_LOGS_ENABLED and (time.time() - edit_button_check_start) > 1.0:
                           # print(f"[{req_id}] (Helper Wait)   - 编辑按钮尚不可见... (is_visible returned False or timed out)", flush=True)
                           logger.debug(f"[{req_id}] (Helper Wait)   - 编辑按钮尚不可见... (is_visible returned False or timed out)") # logger debug

            except ClientDisconnectedError: raise
            except Exception as unexpected_btn_err:
                 # print(f"[{req_id}] (Helper Wait)   - 检查编辑按钮时意外错误: {unexpected_btn_err}", flush=True)
                 logger.warning(f"[{req_id}] (Helper Wait)   - 检查编辑按钮时意外错误: {unexpected_btn_err}") # logger warning

            # Wait before next check using asyncio.sleep
            await asyncio.sleep(POLLING_INTERVAL_STREAM / 1000)
        # --- End of Edit Button Check Loop ---

        # If edit button didn't appear within SILENCE_TIMEOUT_MS after base state met
        if not edit_button_visible:
            # print(f"[{req_id}] (Helper Wait) ⚠️ 基础状态满足后，编辑按钮未在 {SILENCE_TIMEOUT_MS}ms 内出现。判定为超时。", flush=True) # Log adjusted
            logger.warning(f"[{req_id}] (Helper Wait) 基础状态满足后，编辑按钮未在 {SILENCE_TIMEOUT_MS}ms 内出现。判定为超时。") # logger
            await save_error_snapshot(f"wait_completion_edit_button_timeout_{req_id}")
            return False

    # --- End of Main While Loop (Overall Timeout) ---
    # print(f"[{req_id}] (Helper Wait) ❌ 等待响应完成超时 ({RESPONSE_COMPLETION_TIMEOUT}ms)。", flush=True)
    logger.error(f"[{req_id}] (Helper Wait) ❌ 等待响应完成超时 ({RESPONSE_COMPLETION_TIMEOUT}ms)。") # logger
    await save_error_snapshot(f"wait_completion_overall_timeout_{req_id}")
    return False # Indicate timeout

# --- V5: New Helper - Get Final Response Content --- (Unified)
async def _get_final_response_content(
    page: AsyncPage,
    req_id: str,
    check_client_disconnected: Callable
) -> Optional[str]:
    """Gets the final response content, trying Edit Button then Copy Button.
       Implementation mirrors original stream logic closely.
    """
    # print(f"[{req_id}] (Helper GetContent) 开始获取最终响应内容...", flush=True)
    logger.info(f"[{req_id}] (Helper GetContent) 开始获取最终响应内容...") # logger

    # 1. Try getting content via Edit Button first (more reliable)
    response_content = await get_response_via_edit_button(
        page, req_id, check_client_disconnected
    )

    if response_content is not None:
        # print(f"[{req_id}] (Helper GetContent) ✅ 成功通过编辑按钮获取内容。", flush=True)
        logger.info(f"[{req_id}] (Helper GetContent) ✅ 成功通过编辑按钮获取内容。") # logger
        return response_content

    # 2. If Edit Button failed, fall back to Copy Button
    # print(f"[{req_id}] (Helper GetContent) 编辑按钮方法失败或返回空，回退到复制按钮方法...", flush=True)
    logger.warning(f"[{req_id}] (Helper GetContent) 编辑按钮方法失败或返回空，回退到复制按钮方法...") # logger
    response_content = await get_response_via_copy_button(
        page, req_id, check_client_disconnected
    )

    if response_content is not None:
        # print(f"[{req_id}] (Helper GetContent) ✅ 成功通过复制按钮获取内容。", flush=True)
        logger.info(f"[{req_id}] (Helper GetContent) ✅ 成功通过复制按钮获取内容。") # logger
        return response_content

    # 3. If both methods failed
    # print(f"[{req_id}] (Helper GetContent) ❌ 所有获取响应内容的方法均失败。", flush=True)
    logger.error(f"[{req_id}] (Helper GetContent) ❌ 所有获取响应内容的方法均失败。") # logger
    await save_error_snapshot(f"get_content_all_methods_failed_{req_id}")
    return None

# --- Queue Worker --- (Enhanced)
async def queue_worker():
    # print("--- 队列 Worker 已启动 ---")
    logger.info("--- 队列 Worker 已启动 ---") # logger
    was_last_request_streaming = False
    last_request_completion_time = 0

    while True:
        request_item = None; result_future = None; req_id = "UNKNOWN"; completion_event = None
        try:
            # Check for disconnected clients in queue (simplified)
            # ... (Consider adding back if needed, removed for brevity) ...

            # <<< ADDED: Logic to check queue for disconnected clients (from server未重构.py) >>>
            queue_size = request_queue.qsize()
            if queue_size > 0:
                checked_count = 0
                # Create a temporary list to hold items while checking
                items_to_requeue = []
                processed_ids = set()
                while checked_count < queue_size and checked_count < 10: # Limit check depth
                    try:
                        item = request_queue.get_nowait()
                        item_req_id = item.get("req_id", "unknown")
                        if item_req_id in processed_ids: # Avoid reprocessing due to requeueing order issues
                             items_to_requeue.append(item)
                             continue
                        processed_ids.add(item_req_id)

                        if not item.get("cancelled", False):
                            item_http_request = item.get("http_request")
                            if item_http_request:
                                try:
                                    if await item_http_request.is_disconnected():
                                        print(f"[{item_req_id}] (Worker Queue Check) 检测到客户端已断开，标记为取消。", flush=True)
                                        item["cancelled"] = True
                                        item_future = item.get("result_future")
                                        if item_future and not item_future.done():
                                            item_future.set_exception(HTTPException(status_code=499, detail=f"[{item_req_id}] Client disconnected while queued."))
                                except Exception as check_err:
                                    print(f"[{item_req_id}] (Worker Queue Check) Error checking disconnect: {check_err}", flush=True)
                        items_to_requeue.append(item)
                        checked_count += 1
                    except asyncio.QueueEmpty:
                        break # Stop if queue becomes empty during check
                # Put items back into the queue
                for item in items_to_requeue:
                    await request_queue.put(item)
            # <<< END ADDED QUEUE CHECK LOGIC >>>

            request_item = await request_queue.get()
            req_id = request_item["req_id"]
            request_data = request_item["request_data"]
            http_request = request_item["http_request"]
            result_future = request_item["result_future"]

            if request_item.get("cancelled", False):
                print(f"[{req_id}] (Worker) 请求已取消，跳过。", flush=True)
                if not result_future.done(): result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 请求已被用户取消"))
                request_queue.task_done(); continue

            is_streaming_request = request_data.stream
            print(f"[{req_id}] (Worker) 取出请求。模式: {'流式' if is_streaming_request else '非流式'}", flush=True)

            # Delay between consecutive streaming requests
            current_time = time.time()
            if was_last_request_streaming and is_streaming_request and (current_time - last_request_completion_time < 1.0):
                delay_time = max(0.5, 1.0 - (current_time - last_request_completion_time))
                print(f"[{req_id}] (Worker) 连续流式请求，添加 {delay_time:.2f}s 延迟...", flush=True)
                await asyncio.sleep(delay_time)

            if await http_request.is_disconnected():
                 print(f"[{req_id}] (Worker) 客户端在等待锁时断开。取消。", flush=True)
                 if not result_future.done(): result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端关闭了请求"))
                 request_queue.task_done(); continue

            print(f"[{req_id}] (Worker) 等待处理锁...", flush=True)
            async with processing_lock:
                print(f"[{req_id}] (Worker) 已获取处理锁。开始核心处理...", flush=True)

                if await http_request.is_disconnected():
                     print(f"[{req_id}] (Worker) 客户端在获取锁后断开。取消。", flush=True)
                     if not result_future.done(): result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端关闭了请求"))
                elif result_future.done():
                     print(f"[{req_id}] (Worker) Future 在处理前已完成/取消。跳过。", flush=True)
                else:
                    # <<< V5: Call refactored processing function >>>
                    completion_event = await _process_request_refactored(
                        req_id, request_data, http_request, result_future
                    )

                    # Wait for stream completion event if returned
                    if completion_event:
                         print(f"[{req_id}] (Worker) 等待流式生成器完成信号...", flush=True)
                         try:
                              await asyncio.wait_for(completion_event.wait(), timeout=RESPONSE_COMPLETION_TIMEOUT/1000 + 60) # Add buffer
                              print(f"[{req_id}] (Worker) ✅ 流式生成器完成信号收到。", flush=True)
                         except asyncio.TimeoutError:
                              print(f"[{req_id}] (Worker) ⚠️ 等待流式生成器完成信号超时。", flush=True)
                              if not result_future.done(): result_future.set_exception(HTTPException(status_code=504, detail=f"[{req_id}] Stream generation timed out waiting for completion signal."))
                         except Exception as ev_wait_err:
                              print(f"[{req_id}] (Worker) ❌ 等待流式完成事件时出错: {ev_wait_err}", flush=True)
                              if not result_future.done(): result_future.set_exception(HTTPException(status_code=500, detail=f"[{req_id}] Error waiting for stream completion: {ev_wait_err}"))

            # End of processing lock
            print(f"[{req_id}] (Worker) 释放处理锁。", flush=True)
            was_last_request_streaming = is_streaming_request
            last_request_completion_time = time.time()

        except asyncio.CancelledError:
            print("--- 队列 Worker 被取消 ---", flush=True)
            if result_future and not result_future.done(): result_future.cancel("Worker cancelled")
            break # Exit the loop
        except Exception as e:
            print(f"[{req_id}] (Worker) ❌ 处理请求时发生意外错误: {e}", flush=True)
            traceback.print_exc()
            if result_future and not result_future.done():
                result_future.set_exception(HTTPException(status_code=500, detail=f"[{req_id}] 服务器内部错误: {e}"))
            await save_error_snapshot(f"worker_loop_error_{req_id}")
        finally:
             if request_item: request_queue.task_done()

    print("--- 队列 Worker 已停止 ---", flush=True)


# --- V5: Refactored Core Request Processing Logic --- (Called by Worker)
async def _process_request_refactored(
    req_id: str,
    request: ChatCompletionRequest,
    http_request: Request,
    result_future: Future
) -> Optional[Event]: # Return completion event only for streaming
    """Refactored core logic for processing a single request."""
    # print(f"[{req_id}] (Refactored Process) 开始处理请求...")
    logger.info(f"[{req_id}] (Refactored Process) 开始处理请求...") # logger
    is_streaming = request.stream
    page: Optional[AsyncPage] = page_instance # Use global instance
    completion_event: Optional[Event] = None # For streaming

    # --- Setup Disconnect Handling --- (Same as before)
    client_disconnected_event = Event()
    disconnect_check_task = None
    input_field_locator = page.locator(INPUT_SELECTOR)
    submit_button_locator = page.locator(SUBMIT_BUTTON_SELECTOR)

    async def check_disconnect_periodically():
        while not client_disconnected_event.is_set():
            try:
                if await http_request.is_disconnected():
                    # print(f"[{req_id}] (Disco Check Task) 客户端断开。设置事件并尝试停止。", flush=True)
                    logger.info(f"[{req_id}] (Disco Check Task) 客户端断开。设置事件并尝试停止。") # logger
                    client_disconnected_event.set()
                    try: # Attempt to click stop button
                        if await submit_button_locator.is_enabled(timeout=1500):
                             if await input_field_locator.input_value(timeout=1500) == '':
                                 # print(f"[{req_id}] (Disco Check Task)   点击停止...")
                                 logger.info(f"[{req_id}] (Disco Check Task)   点击停止...") # logger
                                 await submit_button_locator.click(timeout=3000, force=True)
                    except Exception as click_err: logger.warning(f"[{req_id}] (Disco Check Task) 停止按钮点击失败: {click_err}") # logger warning
                    if not result_future.done(): result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端在处理期间关闭了请求"))
                    break
                await asyncio.sleep(1.0)
            except asyncio.CancelledError: break
            except Exception as e:
                # print(f"[{req_id}] (Disco Check Task) 错误: {e}")
                logger.error(f"[{req_id}] (Disco Check Task) 错误: {e}") # logger
                client_disconnected_event.set()
                if not result_future.done(): result_future.set_exception(HTTPException(status_code=500, detail=f"[{req_id}] Internal disconnect checker error: {e}"))
                break

    disconnect_check_task = asyncio.create_task(check_disconnect_periodically())

    def check_client_disconnected(msg_prefix=""): # Changed to logger.info
        if client_disconnected_event.is_set():
            logger.info(f"[{req_id}] {msg_prefix}检测到客户端断开连接事件。")
            raise ClientDisconnectedError(f"[{req_id}] Client disconnected event set.")
        return False

    try:
        # --- Initial Checks --- (Page Ready)
        if not page or page.is_closed() or not is_page_ready:
            raise HTTPException(status_code=503, detail=f"[{req_id}] AI Studio 页面丢失或未就绪。", headers={"Retry-After": "30"})
        check_client_disconnected("Initial Page Check: ")

        # --- 1. Validation & Prompt Prep --- (Use logger for validation message)
        try: validate_chat_request(request.messages, req_id)
        except ValueError as e: raise HTTPException(status_code=400, detail=f"[{req_id}] 无效请求: {e}")
        # Validation log is already inside validate_chat_request using print, change it there too?
        # For now, assume prepare_combined_prompt handles its own logging via print->logger
        prepared_prompt = prepare_combined_prompt(request.messages, req_id)
        check_client_disconnected("After Prompt Prep: ")

        # --- 2. Clear Chat --- (Revert to direct calls, use logger for messages)
        # print(f"[{req_id}] (Refactored Process) 开始清空聊天记录...")
        logger.info(f"[{req_id}] (Refactored Process) 开始清空聊天记录...") # logger
        try:
            clear_chat_button = page.locator(CLEAR_CHAT_BUTTON_SELECTOR)
            confirm_button = page.locator(CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR)
            overlay_locator = page.locator('div.cdk-overlay-backdrop') # Locator for the overlay
            proceed_with_clear_clicks = False
            try:
                # Direct call with timeout
                await expect_async(clear_chat_button).to_be_enabled(timeout=5000) # Increased timeout slightly
                proceed_with_clear_clicks = True
            except Exception as e:
                is_new_chat_url = '/prompts/new_chat' in page.url.rstrip('/')
                if is_new_chat_url:
                    # print(f"[{req_id}] Info: 清空按钮在新聊天页未就绪 (预期)。")
                    logger.info(f"[{req_id}] 清空按钮在新聊天页未就绪 (预期)。") # logger
                else:
                    # print(f"[{req_id}] ⚠️ 警告: 等待清空按钮失败: {e}。跳过点击。")
                    logger.warning(f"[{req_id}] 等待清空按钮失败: {e}。跳过点击。") # logger

            check_client_disconnected("After Clear Button Check: ")

            if proceed_with_clear_clicks:
                # ** ADDED: Wait for potential overlay to disappear BEFORE clicking clear **
                try:
                    # logger.debug(f"[{req_id}] Waiting for overlay to disappear before clicking clear...")
                    await expect_async(overlay_locator).to_be_hidden(timeout=3000) # Wait up to 3s
                except Exception as overlay_err:
                    logger.warning(f"[{req_id}] Overlay did not disappear before clear click (ignored): {overlay_err}")
                check_client_disconnected("After Overlay Check (Before Clear): ")

                # Direct calls with timeout
                await clear_chat_button.click(timeout=5000)
                check_client_disconnected("After Clear Button Click: ")

                # ** ADDED: Wait for confirm button AND wait for overlay to disappear BEFORE clicking confirm **
                try:
                    # logger.debug(f"[{req_id}] Waiting for confirm button and overlay disappearance...")
                    await expect_async(confirm_button).to_be_visible(timeout=5000)
                    # ***** 移除这行错误的检查 *****
                    # await expect_async(overlay_locator).to_be_hidden(timeout=5000) # Wait for overlay from confirmation dialog
                    # logger.debug(f"[{req_id}] Confirm button visible and overlay hidden. Proceeding to click confirm.")
                except Exception as confirm_wait_err:
                    # Modify error message to be more accurate
                    logger.error(f"[{req_id}] Error waiting for confirm button visibility: {confirm_wait_err}")
                    await save_error_snapshot(f"clear_chat_confirm_wait_error_{req_id}")
                    raise PlaywrightAsyncError(f"Confirm button wait failed: {confirm_wait_err}") from confirm_wait_err

                check_client_disconnected("After Confirm Button/Overlay Wait: ")
                await confirm_button.click(timeout=5000)
                check_client_disconnected("After Confirm Button Click: ")
                # print(f"[{req_id}] >>确认按钮点击完成<<")
                logger.info(f"[{req_id}] 清空确认按钮已点击。") # logger

                last_response_container = page.locator(RESPONSE_CONTAINER_SELECTOR).last
                await asyncio.sleep(0.5) # Use asyncio.sleep
                check_client_disconnected("After Clear Post-Delay: ")
                try:
                    # Direct call with timeout
                    await expect_async(last_response_container).to_be_hidden(timeout=CLEAR_CHAT_VERIFY_TIMEOUT_MS - 500)
                    # print(f"[{req_id}] ✅ 聊天已成功清空 (验证通过)。")
                    logger.info(f"[{req_id}] ✅ 聊天已成功清空 (验证通过)。") # logger
                except Exception as verify_err:
                    # print(f"[{req_id}] ⚠️ 警告: 清空聊天验证失败: {verify_err}")
                    logger.warning(f"[{req_id}] ⚠️ 警告: 清空聊天验证失败: {verify_err}") # logger
        except (PlaywrightAsyncError, asyncio.TimeoutError, ClientDisconnectedError) as clear_err:
            if isinstance(clear_err, ClientDisconnectedError): raise
            # print(f"[{req_id}] ❌ 错误: 清空聊天阶段出错: {clear_err}")
            logger.error(f"[{req_id}] ❌ 错误: 清空聊天阶段出错: {clear_err}") # logger
            await save_error_snapshot(f"clear_chat_error_{req_id}")
        except Exception as clear_exc:
            # print(f"[{req_id}] ❌ 错误: 清空聊天阶段意外错误: {clear_exc}")
            logger.exception(f"[{req_id}] ❌ 错误: 清空聊天阶段意外错误") # logger
            await save_error_snapshot(f"clear_chat_unexpected_{req_id}")
        check_client_disconnected("After Clear Chat Logic: ")

        # --- 3. Fill & Submit Prompt --- (Use logger)
        # print(f"[{req_id}] (Refactored Process) 填充并提交提示 ({len(prepared_prompt)} chars)...")
        logger.info(f"[{req_id}] (Refactored Process) 填充并提交提示 ({len(prepared_prompt)} chars)...") # logger
        input_field = page.locator(INPUT_SELECTOR)
        submit_button = page.locator(SUBMIT_BUTTON_SELECTOR)
        try:
            # Direct calls with timeout
            await expect_async(input_field).to_be_visible(timeout=5000)
            check_client_disconnected("After Input Visible: ")
            await input_field.fill(prepared_prompt, timeout=90000)
            check_client_disconnected("After Input Fill: ")
            await expect_async(submit_button).to_be_enabled(timeout=10000)
            check_client_disconnected("After Submit Enabled: ")
            await asyncio.sleep(0.2) # Use asyncio.sleep
            check_client_disconnected("After Submit Pre-Delay: ")

            # Try shortcut submit
            submitted_successfully = False
            try:
                navigator_platform = await page.evaluate("navigator.platform")
                is_mac = "mac" in navigator_platform.lower()
                shortcut_key = "Meta" if is_mac else "Control"
                await input_field.focus(timeout=5000)
                check_client_disconnected("After Input Focus (Shortcut): ")
                await page.keyboard.press(f'{shortcut_key}+Enter')
                check_client_disconnected("After Keyboard Press: ")
                # Check input cleared (direct call)
                await expect_async(input_field).to_have_value('', timeout=1000)
                submitted_successfully = True
                # print(f"[{req_id}]   - 快捷键提交成功。")
                logger.info(f"[{req_id}]   - 快捷键提交成功。") # logger
            except Exception as shortcut_err:
                # print(f"[{req_id}]   - 快捷键提交失败或未确认: {shortcut_err}。回退到点击。")
                logger.warning(f"[{req_id}]   - 快捷键提交失败或未确认: {shortcut_err}。回退到点击。") # logger

            check_client_disconnected("After Shortcut Attempt Logic: ")

            # Fallback to click
            if not submitted_successfully:
                # Direct calls with timeout
                await submit_button.scroll_into_view_if_needed(timeout=5000)
                check_client_disconnected("After Scroll Fallback: ")
                await submit_button.click(timeout=10000, force=True)
                check_client_disconnected("After Click Fallback: ")
                await expect_async(input_field).to_have_value('', timeout=3000)
                submitted_successfully = True
                # print(f"[{req_id}]   - 点击提交成功。")
                logger.info(f"[{req_id}]   - 点击提交成功。") # logger

            if not submitted_successfully:
                 raise PlaywrightAsyncError("Failed to submit prompt via shortcut or click.")

        except (PlaywrightAsyncError, asyncio.TimeoutError, ClientDisconnectedError) as submit_err:
            if isinstance(submit_err, ClientDisconnectedError): raise
            # print(f"[{req_id}] ❌ 错误: 填充或提交提示时出错: {submit_err}")
            logger.error(f"[{req_id}] ❌ 错误: 填充或提交提示时出错: {submit_err}") # logger
            await save_error_snapshot(f"submit_prompt_error_{req_id}")
            raise HTTPException(status_code=502, detail=f"[{req_id}] Failed to submit prompt to AI Studio: {submit_err}")
        except Exception as submit_exc:
            # print(f"[{req_id}] ❌ 错误: 填充或提交提示时意外错误: {submit_exc}")
            logger.exception(f"[{req_id}] ❌ 错误: 填充或提交提示时意外错误") # logger
            await save_error_snapshot(f"submit_prompt_unexpected_{req_id}")
            raise HTTPException(status_code=500, detail=f"[{req_id}] Unexpected error during prompt submission: {submit_exc}")
        check_client_disconnected("After Submit Logic: ")

        # --- 4. Locate Response Element --- (Use logger)
        # print(f"[{req_id}] (Refactored Process) 定位响应元素...")
        logger.info(f"[{req_id}] (Refactored Process) 定位响应元素...") # logger
        response_container = page.locator(RESPONSE_CONTAINER_SELECTOR).last
        response_element = response_container.locator(RESPONSE_TEXT_SELECTOR)
        try:
            # Direct calls with timeout
            await expect_async(response_container).to_be_attached(timeout=20000)
            check_client_disconnected("After Response Container Attached: ")
            await expect_async(response_element).to_be_attached(timeout=90000)
            # print(f"[{req_id}]   - 响应元素已定位。")
            logger.info(f"[{req_id}]   - 响应元素已定位。") # logger
        except (PlaywrightAsyncError, asyncio.TimeoutError, ClientDisconnectedError) as locate_err:
            if isinstance(locate_err, ClientDisconnectedError): raise
            # print(f"[{req_id}] ❌ 错误: 定位响应元素失败或超时: {locate_err}")
            logger.error(f"[{req_id}] ❌ 错误: 定位响应元素失败或超时: {locate_err}") # logger
            await save_error_snapshot(f"response_locate_error_{req_id}")
            raise HTTPException(status_code=502, detail=f"[{req_id}] Failed to locate AI Studio response element: {locate_err}")
        except Exception as locate_exc:
            # print(f"[{req_id}] ❌ 错误: 定位响应元素时意外错误: {locate_exc}")
            logger.exception(f"[{req_id}] ❌ 错误: 定位响应元素时意外错误") # logger
            await save_error_snapshot(f"response_locate_unexpected_{req_id}")
            raise HTTPException(status_code=500, detail=f"[{req_id}] Unexpected error locating response element: {locate_exc}")
        check_client_disconnected("After Locate Response: ")

        # --- 5. Wait for Completion --- (Uses helper, which was reverted internally)
        # print(f"[{req_id}] (Refactored Process) 等待响应生成完成...")
        logger.info(f"[{req_id}] (Refactored Process) 等待响应生成完成...") # logger
        completion_detected = await _wait_for_response_completion(
            page, req_id, response_element, None, check_client_disconnected, None # Pass None for unused helpers
        )
        if not completion_detected:
            raise HTTPException(status_code=504, detail=f"[{req_id}] AI Studio response generation timed out.")
        check_client_disconnected("After Wait Completion: ")

        # --- 6. Check for Page Errors --- (Use logger)
        # print(f"[{req_id}] (Refactored Process) 检查页面错误提示...")
        logger.info(f"[{req_id}] (Refactored Process) 检查页面错误提示...") # logger
        page_error = await detect_and_extract_page_error(page, req_id)
        if page_error:
            # print(f"[{req_id}] ❌ 错误: AI Studio 页面返回错误: {page_error}")
            logger.error(f"[{req_id}] ❌ 错误: AI Studio 页面返回错误: {page_error}") # logger
            await save_error_snapshot(f"page_error_detected_{req_id}")
            raise HTTPException(status_code=502, detail=f"[{req_id}] AI Studio Error: {page_error}")
        check_client_disconnected("After Page Error Check: ")

        # --- 7. Get Final Content --- (Uses helpers, which were reverted internally)
        # print(f"[{req_id}] (Refactored Process) 获取最终响应内容...")
        logger.info(f"[{req_id}] (Refactored Process) 获取最终响应内容...") # logger
        final_content = await _get_final_response_content(
            page, req_id, check_client_disconnected # Pass only needed args
        )
        if final_content is None:
            raise HTTPException(status_code=500, detail=f"[{req_id}] Failed to extract final response content from AI Studio.")
        check_client_disconnected("After Get Content: ")

        # --- 8. Format and Return Result --- (Use logger)
        # print(f"[{req_id}] (Refactored Process) 格式化并设置结果 (模式: {'流式' if is_streaming else '非流式'})...")
        logger.info(f"[{req_id}] (Refactored Process) 格式化并设置结果 (模式: {'流式' if is_streaming else '非流式'})...") # logger
        if is_streaming:
            completion_event = Event() # Create event for streaming

            async def create_stream_generator(event_to_set: Event, content_to_stream: str) -> AsyncGenerator[str, None]:
                """Closure to generate SSE stream from final content."""
                # print(f"[{req_id}] (Stream Gen) 开始伪流式输出...")
                logger.info(f"[{req_id}] (Stream Gen) 开始伪流式输出 ({len(content_to_stream)} chars)...") # logger
                try:
                    char_count = 0
                    total_chars = len(content_to_stream)
                    for i in range(0, total_chars):
                        if client_disconnected_event.is_set():
                            # print(f"[{req_id}] (Stream Gen) 断开连接，停止。", flush=True)
                            logger.info(f"[{req_id}] (Stream Gen) 断开连接，停止。") # logger
                            break
                        delta = content_to_stream[i]
                        yield generate_sse_chunk(delta, req_id, MODEL_NAME)
                        char_count += 1
                        if char_count % 100 == 0 or char_count == total_chars:
                            if DEBUG_LOGS_ENABLED:
                                # print(f"[{req_id}] (Stream Gen) 进度: {char_count}/{total_chars}", flush=True)
                                # logger.debug(f"[{req_id}] (Stream Gen) 进度: {char_count}/{total_chars}") # logger debug (Removed)
                                pass # Keep the structure, but no log needed here now
                        await asyncio.sleep(PSEUDO_STREAM_DELAY) # Use asyncio.sleep

                    yield generate_sse_stop_chunk(req_id, MODEL_NAME)
                    yield "data: [DONE]\n\n"
                    # print(f"[{req_id}] (Stream Gen) ✅ 伪流式响应发送完毕。")
                    logger.info(f"[{req_id}] (Stream Gen) ✅ 伪流式响应发送完毕。") # logger
                except asyncio.CancelledError:
                    # print(f"[{req_id}] (Stream Gen) 流生成器被取消。")
                    logger.info(f"[{req_id}] (Stream Gen) 流生成器被取消。") # logger
                except Exception as e:
                    # print(f"[{req_id}] (Stream Gen) ❌ 伪流式生成过程中出错: {e}")
                    # traceback.print_exc()
                    logger.exception(f"[{req_id}] (Stream Gen) ❌ 伪流式生成过程中出错") # logger
                    try: yield generate_sse_error_chunk(f"Stream generation error: {e}", req_id); yield "data: [DONE]\n\n"
                    except: pass
                finally:
                    # print(f"[{req_id}] (Stream Gen) 设置完成事件。")
                    logger.info(f"[{req_id}] (Stream Gen) 设置完成事件。") # logger
                    if not event_to_set.is_set(): event_to_set.set()

            stream_generator_func = create_stream_generator(completion_event, final_content)
            if not result_future.done():
                result_future.set_result(StreamingResponse(stream_generator_func, media_type="text/event-stream"))
                # print(f"[{req_id}] (Refactored Process) 流式响应生成器已设置。")
                logger.info(f"[{req_id}] (Refactored Process) 流式响应生成器已设置。") # logger
            else:
                # print(f"[{req_id}] (Refactored Process) Future 已完成/取消，无法设置流式结果。")
                logger.warning(f"[{req_id}] (Refactored Process) Future 已完成/取消，无法设置流式结果。") # logger
                if not completion_event.is_set(): completion_event.set()
            return completion_event
        else: # Non-streaming
            response_payload = {
                "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": MODEL_NAME,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": final_content},
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
            if not result_future.done():
                result_future.set_result(JSONResponse(content=response_payload))
                # print(f"[{req_id}] (Refactored Process) 非流式 JSON 响应已设置。")
                logger.info(f"[{req_id}] (Refactored Process) 非流式 JSON 响应已设置。") # logger
            else:
                # print(f"[{req_id}] (Refactored Process) Future 已完成/取消，无法设置 JSON 结果。")
                logger.warning(f"[{req_id}] (Refactored Process) Future 已完成/取消，无法设置 JSON 结果。") # logger
            return None

    # --- Exception Handling --- (Use logger)
    except ClientDisconnectedError as disco_err:
        # print(f"[{req_id}] (Refactored Process) 捕获到客户端断开连接信号: {disco_err}")
        logger.info(f"[{req_id}] (Refactored Process) 捕获到客户端断开连接信号: {disco_err}") # logger
        if not result_future.done():
             result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] Client disconnected during processing."))
    except HTTPException as http_err:
        # print(f"[{req_id}] (Refactored Process) 捕获到 HTTP 异常: {http_err.status_code} - {http_err.detail}")
        logger.warning(f"[{req_id}] (Refactored Process) 捕获到 HTTP 异常: {http_err.status_code} - {http_err.detail}") # logger
        if not result_future.done(): result_future.set_exception(http_err)
    except PlaywrightAsyncError as pw_err:
        # print(f"[{req_id}] (Refactored Process) 捕获到 Playwright 错误: {pw_err}")
        logger.error(f"[{req_id}] (Refactored Process) 捕获到 Playwright 错误: {pw_err}") # logger
        await save_error_snapshot(f"process_playwright_error_{req_id}")
        if not result_future.done(): result_future.set_exception(HTTPException(status_code=502, detail=f"[{req_id}] Playwright interaction failed: {pw_err}"))
    except asyncio.TimeoutError as timeout_err:
        # print(f"[{req_id}] (Refactored Process) 捕获到操作超时: {timeout_err}")
        logger.error(f"[{req_id}] (Refactored Process) 捕获到操作超时: {timeout_err}") # logger
        await save_error_snapshot(f"process_timeout_error_{req_id}")
        if not result_future.done(): result_future.set_exception(HTTPException(status_code=504, detail=f"[{req_id}] Operation timed out: {timeout_err}"))
    except asyncio.CancelledError:
        # print(f"[{req_id}] (Refactored Process) 任务被取消。")
        logger.info(f"[{req_id}] (Refactored Process) 任务被取消。") # logger
        if not result_future.done(): result_future.cancel("Processing task cancelled")
    except Exception as e:
        # print(f"[{req_id}] (Refactored Process) 捕获到意外错误: {e}")
        # traceback.print_exc()
        logger.exception(f"[{req_id}] (Refactored Process) 捕获到意外错误") # logger
        await save_error_snapshot(f"process_unexpected_error_{req_id}")
        if not result_future.done(): result_future.set_exception(HTTPException(status_code=500, detail=f"[{req_id}] Unexpected server error: {e}"))
    finally:
        # --- Cleanup Disconnect Task --- (Use logger)
        if disconnect_check_task and not disconnect_check_task.done():
            # print(f"[{req_id}] (Refactored Process) 清理断开连接检查任务...")
            # logger.debug(f"[{req_id}] (Refactored Process) 清理断开连接检查任务...") # logger debug (Removed)
            disconnect_check_task.cancel()
            try: await disconnect_check_task
            except asyncio.CancelledError: pass
            except Exception as task_clean_err: logger.error(f"[{req_id}] 清理任务时出错: {task_clean_err}") # logger
        # print(f"[{req_id}] (Refactored Process) 处理完成。")
        logger.info(f"[{req_id}] (Refactored Process) 处理完成。") # logger
        if is_streaming and completion_event and not completion_event.is_set() and (result_future.done() and result_future.exception() is not None):
             # print(f"[{req_id}] (Refactored Process) 流式请求异常，确保完成事件已设置。")
             logger.warning(f"[{req_id}] (Refactored Process) 流式请求异常，确保完成事件已设置。") # logger
             completion_event.set()
        return completion_event

# --- Main Chat Endpoint --- (Enqueue request)
@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, http_request: Request):
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    # print(f"[{req_id}] 收到 /v1/chat/completions 请求 (Stream={request.stream})")
    logger.info(f"[{req_id}] 收到 /v1/chat/completions 请求 (Stream={request.stream})") # logger

    if is_initializing or not is_page_ready or not is_browser_connected or not worker_task or worker_task.done():
        status_code = 503
        detail = f"[{req_id}] 服务当前不可用 (初始化中、页面/浏览器未就绪或 Worker 未运行)。请稍后重试。"
        # print(f"[{req_id}] 错误: {detail}")
        logger.error(f"[{req_id}] 错误: {detail}") # logger
        raise HTTPException(status_code=status_code, detail=detail, headers={"Retry-After": "30"})

    result_future = Future()
    request_item = {
        "req_id": req_id,
        "request_data": request,
        "http_request": http_request,
        "result_future": result_future,
        "enqueue_time": time.time(),
        "cancelled": False # Add cancelled flag
    }

    await request_queue.put(request_item)
    # print(f"[{req_id}] 请求已加入队列 (当前队列长度: {request_queue.qsize()})")
    logger.info(f"[{req_id}] 请求已加入队列 (当前队列长度: {request_queue.qsize()})") # logger

    try:
        # Wait for the result from the worker
        # Add timeout to prevent indefinite hanging if worker fails unexpectedly
        timeout_seconds = RESPONSE_COMPLETION_TIMEOUT / 1000 + 120 # Base timeout + buffer
        result = await asyncio.wait_for(result_future, timeout=timeout_seconds)
        # print(f"[{req_id}] Worker 处理完成，返回结果。")
        logger.info(f"[{req_id}] Worker 处理完成，返回结果。") # logger
        return result
    except asyncio.TimeoutError:
        # print(f"[{req_id}] ❌ 等待 Worker 响应超时 ({timeout_seconds}s)。")
        logger.error(f"[{req_id}] ❌ 等待 Worker 响应超时 ({timeout_seconds}s)。") # logger
        # Mark the item in queue as cancelled (if possible, might be complex)
        # Best effort: Raise 504
        raise HTTPException(status_code=504, detail=f"[{req_id}] Request processing timed out waiting for worker response.")
    except asyncio.CancelledError:
        # print(f"[{req_id}] 请求 Future 被取消 (可能由客户端断开触发)。")
        logger.info(f"[{req_id}] 请求 Future 被取消 (可能由客户端断开触发)。") # logger
        # Worker should have handled setting the 499, but raise defensively
        raise HTTPException(status_code=499, detail=f"[{req_id}] Request cancelled (likely client disconnect).")
    except HTTPException as http_err: # Re-raise exceptions set by worker
        # print(f"[{req_id}] Worker 抛出 HTTP 异常 {http_err.status_code}，重新抛出。")
        logger.warning(f"[{req_id}] Worker 抛出 HTTP 异常 {http_err.status_code}，重新抛出。") # logger
        raise http_err
    except Exception as e:
        # print(f"[{req_id}] ❌ 等待 Worker 响应时发生意外错误: {e}")
        logger.exception(f"[{req_id}] ❌ 等待 Worker 响应时发生意外错误") # logger
        # traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"[{req_id}] Unexpected error waiting for worker response: {e}")

# --- 新增：辅助函数，搜索队列中的请求并标记为取消 --- (Helper from server未重构.py)
async def cancel_queued_request(req_id: str) -> bool:
    """在队列中查找指定req_id的请求并标记为取消。

    返回:
        bool: 如果找到并标记了请求则返回True，否则返回False
    """
    cancelled = False
    # Create a temporary list to hold items while searching
    items_to_requeue = []
    found = False
    try:
        while True: # Process the whole queue or until found
            item = request_queue.get_nowait()
            if item.get("req_id") == req_id and not item.get("cancelled", False):
                # print(f"[{req_id}] 在队列中找到请求，标记为已取消。", flush=True)
                logger.info(f"[{req_id}] 在队列中找到请求，标记为已取消。") # logger
                item["cancelled"] = True
                # Set exception on future immediately if possible
                item_future = item.get("result_future")
                if item_future and not item_future.done():
                    item_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] Request cancelled by API call."))
                items_to_requeue.append(item) # Requeue the cancelled item
                cancelled = True
                found = True
                # Don't break, process the rest of the queue to requeue items
            else:
                items_to_requeue.append(item)
    except asyncio.QueueEmpty:
        pass # Finished processing the queue
    finally:
        # Put all items back into the queue
        for item in items_to_requeue:
            await request_queue.put(item)
    return cancelled

# --- 新增：添加取消请求的API端点 --- (Endpoint from server未重构.py)
@app.post("/v1/cancel/{req_id}")
async def cancel_request(req_id: str):
    """取消指定ID的请求，如果它还在队列中等待处理"""
    # print(f"[{req_id}] 收到取消请求。", flush=True)
    logger.info(f"[{req_id}] 收到取消请求。") # logger
    cancelled = await cancel_queued_request(req_id)
    if cancelled:
        return JSONResponse(content={"success": True, "message": f"Request {req_id} marked as cancelled in queue."}) # Updated message
    else:
        # 未找到请求或请求可能已经在处理中
        return JSONResponse(
            content={"success": False, "message": f"Request {req_id} not found in queue (it might be processing or already finished)."}, # Updated message
            status_code=404
        )

# --- 新增：添加队列状态查询的API端点 --- (Endpoint from server未重构.py)
@app.get("/v1/queue")
async def get_queue_status():
    """返回当前队列状态的信息"""
    queue_items = []
    items_to_requeue = []
    try:
        while True:
            item = request_queue.get_nowait()
            items_to_requeue.append(item) # Temporarily store item
            req_id = item.get("req_id", "unknown")
            timestamp = item.get("enqueue_time", 0) # Use enqueue_time if available
            is_streaming = item.get("request_data").stream if hasattr(item.get("request_data", {}), "stream") else False
            cancelled = item.get("cancelled", False)
            queue_items.append({
                "req_id": req_id,
                "enqueue_time": timestamp,
                "wait_time_seconds": round(time.time() - timestamp, 2) if timestamp else None,
                "is_streaming": is_streaming,
                "cancelled": cancelled
            })
    except asyncio.QueueEmpty:
        pass # Finished reading queue
    finally:
        # Put items back into the queue
        for item in items_to_requeue:
            await request_queue.put(item)

    return JSONResponse(content={
        "queue_length": len(queue_items), # Use length of extracted items
        "is_processing_locked": processing_lock.locked(), # Check if lock is held
        "items": sorted(queue_items, key=lambda x: x.get("enqueue_time", 0)) # Sort by enqueue time
    })

# --- 新增: WebSocket 日志端点 ---
@app.websocket("/ws/logs")
async def websocket_log_endpoint(websocket: WebSocket):
    """WebSocket 端点，用于实时日志流"""
    client_id = str(uuid.uuid4())
    try:
        # 接受 WebSocket 连接
        await websocket.accept()

        # 将连接添加到管理器
        await log_ws_manager.connect(client_id, websocket) # <<-- 使用 await

        # 发送欢迎消息
        await websocket.send_text(json.dumps({
            "type": "connection_status",
            "status": "connected",
            "message": "已连接到日志流。",
            "timestamp": datetime.datetime.now().isoformat()
        }))

        # 保持连接打开，直到客户端断开连接
        while True:
            # 为防止超时，使用简单的心跳机制
            await websocket.receive_text() # 等待客户端消息
            # 可以选择性地回复一个 pong 消息，如果需要严格的心跳
            # await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        # 客户端断开连接时，从管理器中移除
        log_ws_manager.disconnect(client_id)
        logger.info(f"日志客户端已断开连接: {client_id}")
    except Exception as e:
        # 发生其他异常时，同样从管理器中移除
        logger.error(f"日志 WebSocket 异常 ({client_id}): {str(e)}") # 添加 client_id
        log_ws_manager.disconnect(client_id)


# --- Main Execution --- (if running directly)
if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="运行 AI Studio Proxy Server")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="服务器主机地址")
    parser.add_argument(
        "--port",
        type=int,
        default=2048, # <-- 修改默认值为 2048
        help="服务器端口"
    )
    # --- 新增参数 ---
    parser.add_argument(
        "--disable-print-redirect",
        action="store_true", # 设置为 store_true，存在即为 True
        help="禁用将 print 输出重定向到日志系统的功能，使终端输出更干净。"
    )
    args = parser.parse_args()

    # --- 修改调用 ---
    # 如果提供了 --disable-print-redirect，则 redirect_print 为 False
    setup_logging(log_level=logging.INFO, redirect_print=not args.disable_print_redirect)

    uvicorn.run(app, host=args.host, port=args.port)

