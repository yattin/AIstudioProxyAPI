import asyncio
import random
import time
import json
from typing import List, Optional, Dict, Any, Union, AsyncGenerator, Tuple, Callable, Set
import os
import traceback
from contextlib import asynccontextmanager
import sys
import platform
import logging
import logging.handlers
import socket # 保留 socket 以便在 __main__ 中进行简单的直接运行提示
from asyncio import Queue, Lock, Future, Task, Event

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel # Field 未使用，可以移除
from playwright.async_api import Page as AsyncPage, Browser as AsyncBrowser, Playwright as AsyncPlaywright, Error as PlaywrightAsyncError, expect as expect_async, BrowserContext as AsyncBrowserContext, Locator
from playwright.async_api import async_playwright
from urllib.parse import urljoin, urlparse
import uuid
import datetime

# --- 全局添加标记常量 ---
# 这些标记主要用于 server.py 内部 print 和 input 的协调。
# 如果 print 输出到控制台 (SERVER_REDIRECT_PRINT='false')，launch_camoufox.py 不需要关心它们。
# 如果 print 被重定向到日志，这些标记也会进入日志。
USER_INPUT_START_MARKER_SERVER = "__USER_INPUT_START__"
USER_INPUT_END_MARKER_SERVER = "__USER_INPUT_END__"

# --- 全局日志控制配置 (这些主要影响 lifespan 中的行为) ---
DEBUG_LOGS_ENABLED = os.environ.get('DEBUG_LOGS_ENABLED', 'false').lower() in ('true', '1', 'yes')
TRACE_LOGS_ENABLED = os.environ.get('TRACE_LOGS_ENABLED', 'false').lower() in ('true', '1', 'yes')
# LOG_INTERVAL = int(os.environ.get('LOG_INTERVAL', '20')) # 这些似乎未在 server.py 中使用
# LOG_TIME_INTERVAL = float(os.environ.get('LOG_TIME_INTERVAL', '3.0'))

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
PSEUDO_STREAM_DELAY = 0.001 # 可以根据需要调整这个值
EDIT_MESSAGE_BUTTON_SELECTOR = 'ms-chat-turn:last-child .actions-container button.toggle-edit-button'
MESSAGE_TEXTAREA_SELECTOR = 'ms-chat-turn:last-child ms-text-chunk ms-autosize-textarea'
FINISH_EDIT_BUTTON_SELECTOR = 'ms-chat-turn:last-child .actions-container button.toggle-edit-button[aria-label="Stop editing"]'

AUTH_PROFILES_DIR = os.path.join(os.path.dirname(__file__), 'auth_profiles')
ACTIVE_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'active')
SAVED_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'saved')
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
APP_LOG_FILE_PATH = os.path.join(LOG_DIR, 'app.log') # server.py 的日志文件

# --- 全局代理设置 (将在 lifespan 中通过 logger 输出) ---
PROXY_SERVER_ENV = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
NO_PROXY_ENV = os.environ.get('NO_PROXY')
# --- 新增: 环境变量控制是否自动保存认证 ---
AUTO_SAVE_AUTH = os.environ.get('AUTO_SAVE_AUTH', '').lower() in ('1', 'true', 'yes')
AUTH_SAVE_TIMEOUT = int(os.environ.get('AUTH_SAVE_TIMEOUT', '30'))  # 默认30秒超时

PLAYWRIGHT_PROXY_SETTINGS: Optional[Dict[str, str]] = None
if PROXY_SERVER_ENV:
    PLAYWRIGHT_PROXY_SETTINGS = {'server': PROXY_SERVER_ENV}
    if NO_PROXY_ENV:
        PLAYWRIGHT_PROXY_SETTINGS['bypass'] = NO_PROXY_ENV.replace(',', ';')
# 移除这里的 print 语句

# --- Constants ---
MODEL_NAME = 'AI-Studio_Camoufox-Proxy'
CHAT_COMPLETION_ID_PREFIX = 'chatcmpl-'
MODELS_ENDPOINT_URL_CONTAINS = "MakerSuiteService/ListModels" # 目标请求URL的一部分
DEFAULT_FALLBACK_MODEL_ID = "gemini-pro" # 如果无法获取列表，使用的默认模型

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
TEMPERATURE_INPUT_SELECTOR = 'div[data-test-id="temperatureSliderContainer"] input[type="number"].slider-input'
MAX_OUTPUT_TOKENS_SELECTOR = '#mat-input-0' # 新增: 最大输出Token输入框选择器
STOP_SEQUENCE_INPUT_SELECTOR = '#mat-mdc-chip-list-input-0' # 新增: 停止序列输入框选择器
MAT_CHIP_REMOVE_BUTTON_SELECTOR = 'mat-chip-set mat-chip-row button[aria-label*="Remove"]' # 新增: Material Chip 移除按钮通用选择器
TOP_P_INPUT_SELECTOR = 'div.settings-item-column:has(h3:text-is("Top P")) input[type="number"].slider-input'


# --- Global State (由 lifespan 管理初始化和清理) ---
playwright_manager: Optional[AsyncPlaywright] = None
browser_instance: Optional[AsyncBrowser] = None
page_instance: Optional[AsyncPage] = None
is_playwright_ready = False
is_browser_connected = False
is_page_ready = False
is_initializing = False # 这个状态由 lifespan 控制

# 新增：用于模型列表的全局变量
global_model_list_raw_json: Optional[List[Any]] = None
parsed_model_list: List[Dict[str, Any]] = [] # 存储解析后的模型列表 [{id: "model_id", ...}, ...]
model_list_fetch_event = asyncio.Event() # 用于指示模型列表是否已获取

# 新增: 模型切换相关的全局变量
current_ai_studio_model_id: Optional[str] = None  # 当前在AI Studio页面上设置的模型ID (可以是名称或ID)
model_switching_lock: Optional[Lock] = None  # 模型切换操作的锁

# 新增: 模型排除列表
excluded_model_ids: Set[str] = set()
EXCLUDED_MODELS_FILENAME = "excluded_models.txt" # 排除列表文件名

request_queue: Optional[Queue] = None
processing_lock: Optional[Lock] = None
worker_task: Optional[Task] = None

logger = logging.getLogger("AIStudioProxyServer") # server.py 使用的 logger
log_ws_manager = None # 将在 lifespan 中初始化

# --- StreamToLogger, WebSocketConnectionManager, WebSocketLogHandler ---
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
                if line.endswith(('\n', '\r')): # 兼容不同系统的换行符
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

class WebSocketConnectionManager:
    """管理所有活动的 WebSocket 日志连接。"""
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, client_id: str, websocket: WebSocket):
        """接受并注册一个新的 WebSocket 连接。"""
        await websocket.accept() # 首先接受连接
        self.active_connections[client_id] = websocket
        logger.info(f"WebSocket 日志客户端已连接: {client_id}")
        # 发送欢迎/连接成功消息
        try:
            await websocket.send_text(json.dumps({
                "type": "connection_status",
                "status": "connected",
                "message": "已连接到实时日志流。",
                "timestamp": datetime.datetime.now().isoformat()
            }))
        except Exception as e: # 处理发送欢迎消息时可能发生的错误
            logger.warning(f"向 WebSocket 客户端 {client_id} 发送欢迎消息失败: {e}")
            # 即使发送欢迎消息失败，连接仍然被认为是建立的

    def disconnect(self, client_id: str):
        """注销一个 WebSocket 连接。"""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"WebSocket 日志客户端已断开: {client_id}")

    async def broadcast(self, message: str):
        """向所有活动的 WebSocket 连接广播消息。"""
        if not self.active_connections:
            return

        disconnected_clients = []
        # 创建连接字典的副本进行迭代，以允许在迭代过程中安全地修改原始字典
        active_conns_copy = list(self.active_connections.items())

        for client_id, connection in active_conns_copy:
            try:
                await connection.send_text(message)
            except WebSocketDisconnect:
                logger.info(f"[WS Broadcast] 客户端 {client_id} 在广播期间断开连接。")
                disconnected_clients.append(client_id)
            except RuntimeError as e: # 例如 "Connection is closed"
                 if "Connection is closed" in str(e):
                     logger.info(f"[WS Broadcast] 客户端 {client_id} 的连接已关闭。")
                     disconnected_clients.append(client_id)
                 else:
                     logger.error(f"广播到 WebSocket {client_id} 时发生运行时错误: {e}")
                     disconnected_clients.append(client_id) # 也将此类错误视为断开连接
            except Exception as e:
                logger.error(f"广播到 WebSocket {client_id} 时发生未知错误: {e}")
                disconnected_clients.append(client_id) # 也将此类错误视为断开连接

        # 清理在广播过程中发现已断开的连接
        if disconnected_clients:
             # logger.info(f"[WS Broadcast] 正在清理已断开的客户端: {disconnected_clients}") # disconnect 方法会记录
             for client_id_to_remove in disconnected_clients:
                 self.disconnect(client_id_to_remove) # 使用自身的 disconnect 方法

class WebSocketLogHandler(logging.Handler):
    """
    一个 logging.Handler 子类，用于将日志记录广播到所有通过 WebSocket 连接的客户端。
    """
    def __init__(self, manager: WebSocketConnectionManager):
        super().__init__()
        self.manager = manager
        # 为 WebSocket 日志条目定义一个简单的格式
        self.formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    def emit(self, record: logging.LogRecord):
        """格式化日志记录并通过 WebSocket 管理器广播它。"""
        # 仅当 manager 有效且有活动连接时才尝试广播
        if self.manager and self.manager.active_connections:
            try:
                log_entry_str = self.format(record)
                # 使用 asyncio.create_task 在事件循环中异步发送，避免阻塞日志记录器
                try:
                     current_loop = asyncio.get_running_loop()
                     current_loop.create_task(self.manager.broadcast(log_entry_str))
                except RuntimeError: # 如果没有正在运行的事件循环 (例如在关闭期间)
                     # 可以选择在此处记录一个普通 print 错误，或静默失败
                     # print(f"WebSocketLogHandler: 没有正在运行的事件循环来广播日志。", file=sys.__stderr__)
                     pass
            except Exception as e:
                # 如果格式化或广播任务创建失败，打印错误到原始 stderr
                print(f"WebSocketLogHandler 错误: 广播日志失败 - {e}", file=sys.__stderr__)

# --- 日志设置函数 (将在 lifespan 中调用) ---
def setup_server_logging(log_level_name: str = "INFO", redirect_print_str: str = "false"):
    """配置 AIStudioProxyServer 的日志记录。由 lifespan 调用。"""
    global logger, log_ws_manager # 确保引用全局变量

    log_level = getattr(logging, log_level_name.upper(), logging.INFO)
    redirect_print = redirect_print_str.lower() in ('true', '1', 'yes')

    # 确保日志相关目录存在
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(ACTIVE_AUTH_DIR, exist_ok=True) # 认证目录也在此确保
    os.makedirs(SAVED_AUTH_DIR, exist_ok=True)

    file_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s')

    # logger 已在全局定义: logger = logging.getLogger("AIStudioProxyServer")
    if logger.hasHandlers(): # 清理旧的处理器，以防重复配置
        logger.handlers.clear()
    logger.setLevel(log_level)
    logger.propagate = False # 通常不希望此 logger 的消息向上传播到根 logger，以避免重复处理

    # 1. 文件处理器 (RotatingFileHandler)
    if os.path.exists(APP_LOG_FILE_PATH):
        try:
            os.remove(APP_LOG_FILE_PATH)
        except OSError as e:
            print(f"警告 (setup_server_logging): 尝试移除旧的 app.log 文件 '{APP_LOG_FILE_PATH}' 失败: {e}。将依赖 mode='w' 进行截断。", file=sys.__stderr__)
    file_handler = logging.handlers.RotatingFileHandler(
        APP_LOG_FILE_PATH, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8', mode='w'
    )
    file_handler.setFormatter(file_log_formatter)
    logger.addHandler(file_handler)

    # 2. WebSocket 处理器
    if log_ws_manager is None: # log_ws_manager 应在 lifespan 中初始化并传递到这里，或通过全局变量访问
        # 如果在此阶段 log_ws_manager 仍为 None，说明初始化流程有问题
        print("严重警告 (setup_server_logging): log_ws_manager 未初始化！WebSocket 日志功能将不可用。", file=sys.__stderr__)
    else:
        ws_handler = WebSocketLogHandler(log_ws_manager)
        ws_handler.setLevel(logging.INFO) # WebSocket 日志可以有自己的级别，例如只发送 INFO 及以上
        logger.addHandler(ws_handler)

    # 新增: 3. 控制台处理器 (StreamHandler) - 将 server.py 的 logger 输出到控制台
    # 这样 logger.info 等调用也会显示在终端。
    # 为了与 launch_camoufox.py 的日志有所区分，格式中添加 [SERVER] 标记。
    console_server_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s [SERVER] - %(message)s')
    console_handler = logging.StreamHandler(sys.stderr) # 输出到标准错误流
    console_handler.setFormatter(console_server_log_formatter)
    console_handler.setLevel(log_level) # 使用与 logger 相同的日志级别
    logger.addHandler(console_handler)

    # 4. 按需重定向 print 输出 (原为第3点)
    original_stdout = sys.stdout # 保存原始流，以便后续恢复
    original_stderr = sys.stderr

    if redirect_print:
        # 使用原始 stderr 打印此提示，确保用户能看到，即使 logger 可能也配置了 StreamHandler 到 stderr
        print("--- 注意：server.py 正在将其 print 输出重定向到日志系统 (文件、WebSocket 和控制台记录器) ---", file=original_stderr)
        
        # 创建特定的 logger 实例来处理重定向的 stdout 和 stderr
        # 这些 logger 将继承 AIStudioProxyServer logger 的处理器
        stdout_redirect_logger = logging.getLogger("AIStudioProxyServer.stdout")
        stdout_redirect_logger.setLevel(logging.INFO) # stdout 内容通常是 INFO 级别
        stdout_redirect_logger.propagate = True # 允许传播到 AIStudioProxyServer logger
        sys.stdout = StreamToLogger(stdout_redirect_logger, logging.INFO)

        stderr_redirect_logger = logging.getLogger("AIStudioProxyServer.stderr")
        stderr_redirect_logger.setLevel(logging.ERROR) # stderr 内容通常是 ERROR 级别
        stderr_redirect_logger.propagate = True
        sys.stderr = StreamToLogger(stderr_redirect_logger, logging.ERROR)
    else:
        # 即使不重定向，也通过原始 stderr 记录这个状态，以明确告知用户
        print("--- server.py 的 print 输出未被重定向到日志系统 (将使用原始 stdout/stderr) ---", file=original_stderr)


    # 设置其他相关库的日志级别，以减少不必要的日志干扰
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO) # 保留 Uvicorn 的错误信息
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING) # Access 日志通常很冗余
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING) # Playwright 日志也非常多

    # 通过配置好的 logger 记录初始化完成信息
    logger.info("=" * 5 + " AIStudioProxyServer 日志系统已在 lifespan 中初始化 " + "=" * 5)
    logger.info(f"日志级别设置为: {logging.getLevelName(log_level)}")
    logger.info(f"日志文件路径: {APP_LOG_FILE_PATH}")
    logger.info(f"控制台日志处理器已添加。") # 新增提示
    logger.info(f"Print 重定向 (由 SERVER_REDIRECT_PRINT 环境变量控制): {'启用' if redirect_print else '禁用'}")
    
    return original_stdout, original_stderr # 返回原始流，以便在 lifespan 结束时恢复

def restore_original_streams(original_stdout, original_stderr):
    """恢复原始的 stdout 和 stderr 流。"""
    sys.stdout = original_stdout
    sys.stderr = original_stderr
    # 此时 logger 可能已关闭或其处理器已移除，所以使用原始 stderr 打印
    print("已恢复 server.py 的原始 stdout 和 stderr 流。", file=sys.__stderr__)


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
    temperature: Optional[float] = None # 新增: 温度参数
    max_output_tokens: Optional[int] = None # 新增: 最大输出Token参数
    stop: Optional[Union[str, List[str]]] = None # 新增: 停止序列参数
    top_p: Optional[float] = None # 新增: Top P 参数

# --- Custom Exception ---
class ClientDisconnectedError(Exception):
    pass

# --- Helper Functions ---
def prepare_combined_prompt(messages: List[Message], req_id: str) -> str:
    # logger.info(f"[{req_id}] (准备提示) 正在从 {len(messages)} 条消息准备组合提示 (包括历史)。")
    # 使用 print 是因为这个函数可能在日志系统完全配置好之前被调用，或者 print 重定向状态未知
    # 如果 SERVER_REDIRECT_PRINT 为 true, print 会进入日志；否则进入控制台。
    # 这是一个设计权衡。如果严格要求所有输出都通过 logger，则此函数内部的 print 也应改为 logger.info。
    # 但考虑到它在请求处理流程中，且其输出对调试重要，保留 print 并依赖 SERVER_REDIRECT_PRINT 控制其去向。
    print(f"[{req_id}] (准备提示) 正在从 {len(messages)} 条消息准备组合提示 (包括历史)。", flush=True)
    combined_parts = []
    system_prompt_content = None
    processed_indices = set()

    first_system_msg_index = -1
    for i, msg in enumerate(messages):
        if msg.role == 'system':
            if isinstance(msg.content, str) and msg.content.strip():
                system_prompt_content = msg.content.strip()
                processed_indices.add(i)
                first_system_msg_index = i
                print(f"[{req_id}] (准备提示) 在索引 {i} 找到系统提示: '{system_prompt_content[:80]}...'")
            else:
                 print(f"[{req_id}] (准备提示) 在索引 {i} 忽略非字符串或空的系统消息。")
                 processed_indices.add(i)
            break

    if system_prompt_content:
        separator = "\\n\\n" if any(idx not in processed_indices for idx in range(len(messages))) else ""
        system_instr_prefix = "系统指令:\\n" # 中文
        combined_parts.append(f"{system_instr_prefix}{system_prompt_content}{separator}")
    else:
        print(f"[{req_id}] (准备提示) 未找到有效的系统提示，继续处理其他消息。")

    turn_separator = "\\n---\\n"
    is_first_turn_after_system = True
    for i, msg in enumerate(messages):
        if i in processed_indices:
            continue
        role = msg.role.capitalize()
        if role == 'System': # 后续的 System 消息被忽略
            print(f"[{req_id}] (准备提示) 跳过在索引 {i} 的后续系统消息。")
            continue
        content_str = ""
        if isinstance(msg.content, str):
            content_str = msg.content
        elif isinstance(msg.content, list):
            text_parts = []
            for item_model in msg.content:
                 if isinstance(item_model, MessageContentItem):
                     if item_model.type == 'text' and isinstance(item_model.text, str):
                          text_parts.append(item_model.text)
                     else:
                           print(f"[{req_id}] (准备提示) 警告: 在索引 {i} 的消息中忽略非文本部分: 类型={item_model.type}")
                 else: # Pydantic 应该已经转换了，但作为后备
                      item_dict = dict(item_model)
                      if item_dict.get('type') == 'text' and isinstance(item_dict.get('text'), str):
                           text_parts.append(item_dict['text'])
                      else:
                           print(f"[{req_id}] (准备提示) 警告: 在索引 {i} 的消息列表中遇到意外的项目格式。项目: {item_model}")
            content_str = "\\n".join(text_parts)
        else:
            print(f"[{req_id}] (准备提示) 警告: 角色 {role} 在索引 {i} 的内容类型意外 ({type(msg.content)})。将转换为字符串。")
            content_str = str(msg.content)

        content_str = content_str.strip()
        if content_str:
            if not is_first_turn_after_system:
                 combined_parts.append(turn_separator)
            # 根据角色添加中文前缀
            role_map = {"User": "用户", "Assistant": "助手", "System": "系统"} # System 理论上不会到这里
            role_prefix_zh = f"{role_map.get(role, role)}:\\n"
            combined_parts.append(f"{role_prefix_zh}{content_str}")
            is_first_turn_after_system = False
        else:
            print(f"[{req_id}] (准备提示) 跳过角色 {role} 在索引 {i} 的空消息。")

    final_prompt = "".join(combined_parts)
    preview_text = final_prompt[:200].replace('\\n', '\\\\n')
    print(f"[{req_id}] (准备提示) 组合提示长度: {len(final_prompt)}。预览: '{preview_text}...'")
    final_newline = "\\n"
    return final_prompt + final_newline if final_prompt else ""

def validate_chat_request(messages: List[Message], req_id: str) -> Dict[str, Optional[str]]:
    if not messages:
        raise ValueError(f"[{req_id}] 无效请求: 'messages' 数组缺失或为空。")
    if not any(msg.role != 'system' for msg in messages):
        raise ValueError(f"[{req_id}] 无效请求: 未找到用户或助手消息。")
    logger.info(f"[{req_id}] (校验) 对 {len(messages)} 条消息的基本校验通过。")
    return {}

async def get_raw_text_content(response_element: Locator, previous_text: str, req_id: str) -> str:
    # (此函数实现与之前版本相同，其内部的 logger 调用会按新配置工作)
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
                    logger.warning(f"[{req_id}] 从可见的 <pre> 获取 innerText 失败: {error_message_first_line}")
                try:
                     raw_text = await response_element.inner_text(timeout=1000)
                except PlaywrightAsyncError as e_parent:
                     if DEBUG_LOGS_ENABLED:
                         logger.warning(f"[{req_id}] 在 <pre> 获取失败后，从父元素获取 inner_text 失败: {e_parent}。返回先前文本。")
                     raw_text = previous_text # 保留之前的值
        else: # pre 元素不可见或不存在
            try:
                 raw_text = await response_element.inner_text(timeout=1500)
            except PlaywrightAsyncError as e_parent:
                 if DEBUG_LOGS_ENABLED:
                     logger.warning(f"[{req_id}] 从父元素获取 inner_text 失败 (无 pre 元素): {e_parent}。返回先前文本。")
                 raw_text = previous_text # 保留之前的值

        if raw_text and isinstance(raw_text, str): # 确保 raw_text 是字符串
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
                # 清理多余的空行
                cleaned_text = "\n".join([line.strip() for line in cleaned_text.splitlines() if line.strip()])
                if DEBUG_LOGS_ENABLED:
                     logger.debug(f"[{req_id}] (清理) 已移除响应文本中的已知UI元素。")
                raw_text = cleaned_text
        return raw_text
    except PlaywrightAsyncError: # 如果 response_element.wait_for 失败等
        return previous_text
    except Exception as e_general:
         logger.warning(f"[{req_id}] getRawTextContent 中发生意外错误: {e_general}。返回先前文本。")
         return previous_text

def generate_sse_chunk(delta: str, req_id: str, model: str) -> str:
    # (代码不变)
    chunk = {
        "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}-{random.randint(100, 999)}",
        "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk)}\n\n"

def generate_sse_stop_chunk(req_id: str, model: str, reason: str = "stop") -> str:
    # (代码不变)
    chunk = {
        "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}-{random.randint(100, 999)}",
        "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]
    }
    return f"data: {json.dumps(chunk)}\n\n"

def generate_sse_error_chunk(message: str, req_id: str, error_type: str = "server_error") -> str:
    # (代码不变)
    error_payload = {"error": {"message": f"[{req_id}] {message}", "type": error_type}}
    return f"data: {json.dumps(error_payload)}\n\n"

async def _initialize_page_logic(browser: AsyncBrowser):
    # (此函数实现与之前版本相同，其内部的 print 和 input 会受 SERVER_REDIRECT_PRINT 影响)
    # 注意：此函数中的 print 语句，如果 SERVER_REDIRECT_PRINT 为 false，会直接输出到运行
    # launch_camoufox.py 的控制台。如果为 true，会进入日志。
    # input() 调用会直接作用于 launch_camoufox.py 的控制台。
    # USER_INPUT_START/END_MARKER_SERVER 标记仍然有用，以便在 print 未重定向时，
    # 如果 launch_camoufox.py 仍需某种方式识别输入段（尽管在此集成模型中它不再直接解析这些标记）。
    logger.info("--- 初始化页面逻辑 (连接到现有浏览器) ---") # 使用 logger
    temp_context: Optional[AsyncBrowserContext] = None # 类型提示
    storage_state_path_to_use: Optional[str] = None
    # 从环境变量获取配置
    launch_mode = os.environ.get('LAUNCH_MODE', 'debug') # 默认为 debug
    # active_auth_json_path = os.environ.get('ACTIVE_AUTH_JSON_PATH') # 在 headless 模式下使用
    # AUTO_SAVE_AUTH 和 AUTH_SAVE_TIMEOUT 已在全局定义

    logger.info(f"   检测到启动模式: {launch_mode}")
    loop = asyncio.get_running_loop()

    if launch_mode == 'headless':
        auth_filename = os.environ.get('ACTIVE_AUTH_JSON_PATH')
        if auth_filename: # 确保 auth_filename 不是 None 或空字符串
            # 假设 ACTIVE_AUTH_JSON_PATH 已经是完整路径或相对于 server.py 的路径
            # 如果它是相对于 auth_profiles/active 的文件名，则需要调整
            # 从 launch_camoufox.py 的修改来看，它传递的是完整路径
            constructed_path = auth_filename 
            if os.path.exists(constructed_path):
                storage_state_path_to_use = constructed_path
                logger.info(f"   无头模式将使用的认证文件: {constructed_path}")
            else:
                logger.error(f"无头模式认证文件无效或不存在: '{constructed_path}'")
                raise RuntimeError(f"无头模式认证文件无效: '{constructed_path}'")
        else:
            logger.error("无头模式需要 ACTIVE_AUTH_JSON_PATH 环境变量，但未设置或为空。")
            # 如果 launch_camoufox.py 确保总会设置（即使是空），那这个错误可能不会触发
            # 但以防万一，保留检查
            raise RuntimeError("无头模式需要 ACTIVE_AUTH_JSON_PATH。")
    elif launch_mode == 'debug':
        logger.info(f"   调试模式: 尝试从环境变量 ACTIVE_AUTH_JSON_PATH 加载认证文件...")
        auth_filepath_from_env = os.environ.get('ACTIVE_AUTH_JSON_PATH')
        if auth_filepath_from_env and os.path.exists(auth_filepath_from_env):
            storage_state_path_to_use = auth_filepath_from_env
            logger.info(f"   调试模式将使用的认证文件 (来自环境变量): {storage_state_path_to_use}")
            # 用户交互已在 launch_camoufox.py 中完成，这里不再需要 input
        elif auth_filepath_from_env: # 环境变量设置了，但文件不存在
            logger.warning(f"   调试模式下环境变量 ACTIVE_AUTH_JSON_PATH 指向的文件不存在: '{auth_filepath_from_env}'。不加载认证文件。")
        else: # 环境变量未设置或为空，意味着用户在启动器中选择不加载
            logger.info("   调试模式下未通过环境变量提供认证文件。将使用浏览器当前状态。")
            # 此处 storage_state_path_to_use 将保持为 None

    elif launch_mode == "direct_debug_no_browser":
        logger.info("   direct_debug_no_browser 模式：不加载 storage_state，不进行浏览器操作。")
    else: # 未知模式
        logger.warning(f"   ⚠️ 警告: 未知的启动模式 '{launch_mode}'。不加载 storage_state。")

    try:
        logger.info("创建新的浏览器上下文...")
        context_options: Dict[str, Any] = {'viewport': {'width': 460, 'height': 800}}
        if storage_state_path_to_use:
            context_options['storage_state'] = storage_state_path_to_use
            logger.info(f"   (使用 storage_state='{os.path.basename(storage_state_path_to_use)}')")
        else:
            logger.info("   (不使用 storage_state)")

        if PLAYWRIGHT_PROXY_SETTINGS:
            context_options['proxy'] = PLAYWRIGHT_PROXY_SETTINGS
            logger.info(f"   (浏览器上下文将使用代理: {PLAYWRIGHT_PROXY_SETTINGS['server']})")
        else:
            logger.info("   (浏览器上下文不使用显式代理配置)")

        temp_context = await browser.new_context(**context_options)

        found_page: Optional[AsyncPage] = None
        pages = temp_context.pages
        target_url_base = f"https://{AI_STUDIO_URL_PATTERN}"
        target_full_url = f"{target_url_base}prompts/new_chat" # 目标是新聊天页面
        login_url_pattern = 'accounts.google.com' # Google 登录页面的 URL 特征
        current_url = ""

        # 查找已打开的符合条件的 AI Studio 页面
        for p_iter in pages: # 使用不同变量名
            try:
                page_url_to_check = p_iter.url # 获取页面 URL
                # 检查页面是否未关闭，且 URL 包含 AI Studio 的基础路径和 /prompts/ 路径段
                if not p_iter.is_closed() and target_url_base in page_url_to_check and "/prompts/" in page_url_to_check:
                    found_page = p_iter
                    current_url = page_url_to_check
                    logger.info(f"   找到已打开的 AI Studio 页面: {current_url}")
                    # 立即为找到的页面添加监听器
                    if found_page: # 确保 found_page 有效
                        logger.info(f"   为已存在的页面 {found_page.url} 添加模型列表响应监听器。")
                        found_page.on("response", _handle_model_list_response)
                    break
            except PlaywrightAsyncError as pw_err_url: # Playwright 操作可能引发的错误
                logger.warning(f"   检查页面 URL 时出现 Playwright 错误: {pw_err_url}")
            except AttributeError as attr_err_url: # 例如页面对象状态异常
                logger.warning(f"   检查页面 URL 时出现属性错误: {attr_err_url}")
            except Exception as e_url_check: # 其他未知错误
                logger.warning(f"   检查页面 URL 时出现其他未预期错误: {e_url_check} (类型: {type(e_url_check).__name__})")


        if not found_page: # 如果没有找到合适的已打开页面
            logger.info(f"-> 未找到合适的现有页面，正在打开新页面并导航到 {target_full_url}...")
            found_page = await temp_context.new_page()
            # 立即为新页面添加监听器，在 goto 之前
            if found_page: # 确保 found_page 有效
                logger.info(f"   为新创建的页面添加模型列表响应监听器 (导航前)。")
                found_page.on("response", _handle_model_list_response)
            try:
                # 等待 DOM 内容加载完成，设置较长超时时间
                await found_page.goto(target_full_url, wait_until="domcontentloaded", timeout=90000)
                current_url = found_page.url
                logger.info(f"-> 新页面导航尝试完成。当前 URL: {current_url}")
            except Exception as new_page_nav_err:
                await save_error_snapshot("init_new_page_nav_fail") # 保存错误快照
                error_str = str(new_page_nav_err)
                # 针对特定网络错误给出更友好的提示
                if "NS_ERROR_NET_INTERRUPT" in error_str: # Firefox 特有的网络中断错误
                    logger.error("\n" + "="*30 + " 网络导航错误提示 " + "="*30)
                    logger.error(f"❌ 导航到 '{target_full_url}' 失败，出现网络中断错误 (NS_ERROR_NET_INTERRUPT)。")
                    logger.error("   这通常表示浏览器在尝试加载页面时连接被意外断开。")
                    logger.error("   可能的原因及排查建议:")
                    logger.error("     1. 网络连接: 请检查你的本地网络连接是否稳定，并尝试在普通浏览器中访问目标网址。")
                    logger.error("     2. AI Studio 服务: 确认 aistudio.google.com 服务本身是否可用。")
                    logger.error("     3. 防火墙/代理/VPN: 检查本地防火墙、杀毒软件、代理或 VPN 设置。")
                    logger.error("     4. Camoufox 服务: 确认 launch_camoufox.py 脚本是否正常运行。")
                    logger.error("     5. 系统资源问题: 确保系统有足够的内存和 CPU 资源。")
                    logger.error("="*74 + "\n")
                raise RuntimeError(f"导航新页面失败: {new_page_nav_err}") from new_page_nav_err

        # 处理登录重定向
        if login_url_pattern in current_url:
            if launch_mode == 'headless':
                logger.error("无头模式下检测到重定向至登录页面，认证可能已失效。请更新认证文件。")
                raise RuntimeError("无头模式认证失败，需要更新认证文件。")
            else: # 调试模式，提示用户手动登录
                print(f"\n{'='*20} 需要操作 {'='*20}", flush=True)
                # USER_INPUT_START_MARKER_SERVER 相关的 input 已移至 launch_camoufox.py
                # print(USER_INPUT_START_MARKER_SERVER, flush=True) 
                login_prompt = "   检测到可能需要登录。如果浏览器显示登录页面，请在浏览器窗口中完成 Google 登录，然后在此处按 Enter 键继续..."
                # 这里的 input 仍然是必要的，用于等待用户在浏览器中操作
                # 但它不再是选择认证文件，而是确认登录操作
                print(USER_INPUT_START_MARKER_SERVER, flush=True) # 保留此标记以兼容可能的外部解析器
                await loop.run_in_executor(None, input, login_prompt)
                print(USER_INPUT_END_MARKER_SERVER, flush=True) # 保留此标记
                logger.info("   用户已操作，正在检查登录状态...")
                try:
                    # 等待 URL 变为 AI Studio 的 URL，超时时间设为3分钟
                    await found_page.wait_for_url(f"**/{AI_STUDIO_URL_PATTERN}**", timeout=180000)
                    current_url = found_page.url # 更新当前 URL
                    if login_url_pattern in current_url: # 如果仍在登录页
                        logger.error("手动登录尝试后，页面似乎仍停留在登录页面。")
                        raise RuntimeError("手动登录尝试后仍在登录页面。")
                    logger.info("   ✅ 登录成功！请不要操作浏览器窗口，等待后续提示。")

                    # 询问是否保存认证状态
                    print("\n" + "="*50, flush=True)
                    print("   【用户交互】需要您的输入!", flush=True)
                    
                    save_auth_prompt = "   是否要将当前的浏览器认证状态保存到文件？ (y/N): "
                    should_save_auth_choice = ''
                    if AUTO_SAVE_AUTH and launch_mode == 'debug': # 自动保存仅在调试模式下有意义
                        logger.info("   自动保存认证模式已启用，将自动保存认证状态...")
                        should_save_auth_choice = 'y'
                    else:
                        # print(USER_INPUT_START_MARKER_SERVER, flush=True) # 标记相关的 input 也移除了
                        print(USER_INPUT_START_MARKER_SERVER, flush=True) # 保留此标记
                        try:
                            auth_save_input_future = loop.run_in_executor(None, input, save_auth_prompt)
                            should_save_auth_choice = await asyncio.wait_for(auth_save_input_future, timeout=AUTH_SAVE_TIMEOUT)
                        except asyncio.TimeoutError:
                            print(f"   输入等待超时({AUTH_SAVE_TIMEOUT}秒)。默认不保存认证状态。", flush=True)
                            should_save_auth_choice = 'n' # 或 ''，下面会处理
                        finally: # 确保结束标记被打印
                            # print(USER_INPUT_END_MARKER_SERVER, flush=True)
                            print(USER_INPUT_END_MARKER_SERVER, flush=True) # 保留此标记
                    
                    if should_save_auth_choice.strip().lower() == 'y':
                        os.makedirs(SAVED_AUTH_DIR, exist_ok=True) # 确保保存目录存在
                        default_auth_filename = f"auth_state_{int(time.time())}.json"
                        
                        # print(USER_INPUT_START_MARKER_SERVER, flush=True) # 相关的 input 也移除了
                        print(USER_INPUT_START_MARKER_SERVER, flush=True) # 保留此标记
                        filename_prompt_str = f"   请输入保存的文件名 (默认为: {default_auth_filename}): "
                        chosen_auth_filename = ''
                        try:
                            filename_input_future = loop.run_in_executor(None, input, filename_prompt_str)
                            chosen_auth_filename = await asyncio.wait_for(filename_input_future, timeout=AUTH_SAVE_TIMEOUT)
                        except asyncio.TimeoutError:
                            print(f"   输入文件名等待超时({AUTH_SAVE_TIMEOUT}秒)。将使用默认文件名: {default_auth_filename}", flush=True)
                        finally:
                            # print(USER_INPUT_END_MARKER_SERVER, flush=True)
                            print(USER_INPUT_END_MARKER_SERVER, flush=True) # 保留此标记

                        final_auth_filename = chosen_auth_filename.strip() or default_auth_filename
                        if not final_auth_filename.endswith(".json"):
                            final_auth_filename += ".json"
                        
                        auth_save_path = os.path.join(SAVED_AUTH_DIR, final_auth_filename)
                        try:
                            await temp_context.storage_state(path=auth_save_path)
                            print(f"   ✅ 认证状态已成功保存到: {auth_save_path}", flush=True)
                        except Exception as save_state_err:
                            logger.error(f"   ❌ 保存认证状态失败: {save_state_err}", exc_info=True)
                            print(f"   ❌ 保存认证状态失败: {save_state_err}", flush=True)
                    else:
                        print("   好的，不保存认证状态。", flush=True)
                    print("="*50 + "\n", flush=True)

                except Exception as wait_login_err:
                    await save_error_snapshot("init_login_wait_fail")
                    logger.error(f"登录提示后未能检测到 AI Studio URL 或保存状态时出错: {wait_login_err}", exc_info=True)
                    raise RuntimeError(f"登录提示后未能检测到 AI Studio URL: {wait_login_err}") from wait_login_err

        elif target_url_base not in current_url or "/prompts/" not in current_url: # 不在登录页，但也不在目标页
            await save_error_snapshot("init_unexpected_page")
            logger.error(f"初始导航后页面 URL 意外: {current_url}。期望包含 '{target_url_base}' 和 '/prompts/'。")
            raise RuntimeError(f"初始导航后出现意外页面: {current_url}。")

        logger.info(f"-> 确认当前位于 AI Studio 对话页面: {current_url}")
        await found_page.bring_to_front() # 将页面带到最前
        try:
            # 等待核心 UI 元素加载完成
            input_wrapper_locator = found_page.locator('ms-prompt-input-wrapper')
            await expect_async(input_wrapper_locator).to_be_visible(timeout=35000)
            await expect_async(found_page.locator(INPUT_SELECTOR)).to_be_visible(timeout=10000)
            logger.info("-> ✅ 核心输入区域可见。")

            model_wrapper_locator = found_page.locator('#mat-select-value-0 mat-select-trigger').first
            model_name_on_page = await model_wrapper_locator.inner_text(timeout=5000) # 增加超时
            logger.info(f"-> 🤖 页面检测到的当前模型: {model_name_on_page}")
            
            result_page_instance = found_page
            result_page_ready = True
            logger.info(f"✅ 页面逻辑初始化成功。")
            return result_page_instance, result_page_ready
        except Exception as input_visible_err:
             await save_error_snapshot("init_fail_input_timeout")
             logger.error(f"页面初始化失败：核心输入区域未在预期时间内变为可见。最后的 URL 是 {found_page.url}", exc_info=True)
             raise RuntimeError(f"页面初始化失败：核心输入区域未在预期时间内变为可见。最后的 URL 是 {found_page.url}") from input_visible_err

    except Exception as e_init_page: # 捕获 _initialize_page_logic 内部所有未处理的异常
        logger.critical(f"❌ 页面逻辑初始化期间发生严重意外错误: {e_init_page}", exc_info=True)
        if temp_context and not temp_context.is_closed(): # 确保上下文存在且未关闭
            try: await temp_context.close()
            except Exception: pass # 忽略关闭时的错误
        await save_error_snapshot("init_unexpected_error") # 尝试保存快照
        raise RuntimeError(f"页面初始化意外错误: {e_init_page}") from e_init_page
    # temp_context 在成功时不关闭，因为 result_page_instance 属于它。
    # 它将在浏览器连接关闭时（在 lifespan 的 finally 块中）被关闭。

async def _close_page_logic():
    # (代码与之前版本相同)
    global page_instance, is_page_ready
    logger.info("--- 运行页面逻辑关闭 --- ") # 使用 logger
    if page_instance and not page_instance.is_closed():
        try:
            await page_instance.close()
            logger.info("   ✅ 页面已关闭")
        except PlaywrightAsyncError as pw_err:
            logger.warning(f"   ⚠️ 关闭页面时出现Playwright错误: {pw_err}")
        except asyncio.TimeoutError as timeout_err: # asyncio.TimeoutError
            logger.warning(f"   ⚠️ 关闭页面时超时: {timeout_err}")
        except Exception as other_err:
            logger.error(f"   ⚠️ 关闭页面时出现意外错误: {other_err} (类型: {type(other_err).__name__})", exc_info=True)
    page_instance = None
    is_page_ready = False
    logger.info("页面逻辑状态已重置。")
    return None, False

async def _handle_model_list_response(response: Any):
    global global_model_list_raw_json, parsed_model_list, model_list_fetch_event, logger, MODELS_ENDPOINT_URL_CONTAINS, DEBUG_LOGS_ENABLED, excluded_model_ids # Ensuring excluded_model_ids is the one used globally

    if MODELS_ENDPOINT_URL_CONTAINS in response.url and response.ok:
        logger.info(f"捕获到潜在的模型列表响应来自: {response.url} (状态: {response.status})")
        try:
            data = await response.json()
            models_array_container = None
            if isinstance(data, list) and data: # 确保 data 非空
                # 针对 [[[fields...]]] 结构
                if isinstance(data[0], list) and data[0] and isinstance(data[0][0], list):
                    logger.info("检测到三层列表结构 data[0][0] is list. models_array_container 设置为 data[0]。")
                    models_array_container = data[0]
                # 针对 [[fields...], [fields...]] 结构
                elif isinstance(data[0], list) and data[0] and isinstance(data[0][0], str): # 假设字段是字符串
                    logger.info("检测到两层列表结构 data[0][0] is str. models_array_container 设置为 data。")
                    models_array_container = data
                # 针对 [{"id":...}, {"id":...}] 结构 (OpenAI 风格)
                elif isinstance(data[0], dict):
                    logger.info("检测到根列表，元素为字典。直接使用 data 作为 models_array_container。")
                    models_array_container = data
                else:
                    logger.warning(f"未知的列表嵌套结构。data[0] 类型: {type(data[0]) if data else 'N/A'}。data[0] 预览: {str(data[0])[:200] if data else 'N/A'}")
                    # 可以考虑将 data 直接赋给 models_array_container 作为最后的尝试，或者直接返回
                    # models_array_container = data # 谨慎使用，可能导致后续解析问题
            elif isinstance(data, dict):
                if 'data' in data and isinstance(data['data'], list):
                    models_array_container = data['data']
                elif 'models' in data and isinstance(data['models'], list):
                    models_array_container = data['models']
                else:
                    for key, value in data.items():
                        if isinstance(value, list) and len(value) > 0 and isinstance(value[0], (dict, list)):
                            models_array_container = value
                            logger.info(f"模型列表数据在 '{key}' 键下通过启发式搜索找到。")
                            break
                    if models_array_container is None:
                        logger.warning("在字典响应中未能自动定位模型列表数组。")
                        if not model_list_fetch_event.is_set(): model_list_fetch_event.set()
                        return
            else:
                logger.warning(f"接收到的模型列表数据既不是列表也不是字典: {type(data)}")
                if not model_list_fetch_event.is_set(): model_list_fetch_event.set()
                return

            if models_array_container is not None:
                new_parsed_list = []
                for entry_in_container in models_array_container:
                    model_fields_list = None
                    if isinstance(entry_in_container, dict):
                        potential_id = entry_in_container.get('id', entry_in_container.get('model_id', entry_in_container.get('modelId')))
                        if potential_id: model_fields_list = entry_in_container
                        else: model_fields_list = list(entry_in_container.values())
                    elif isinstance(entry_in_container, list):
                        model_fields_list = entry_in_container
                    else: 
                        logger.debug(f"Skipping entry of unknown type: {type(entry_in_container)}")
                        continue
                    
                    if not model_fields_list: 
                        logger.debug("Skipping entry because model_fields_list is empty or None.")
                        continue

                    model_id_path_str = None
                    display_name_candidate = ""
                    description_candidate = "N/A"
                    default_max_output_tokens_val = None
                    default_top_p_val = None
                    default_temperature_val = 1.0 
                    supported_max_output_tokens_val = None 

                    current_model_id_for_log = "UnknownModelYet"
                    try:
                        if isinstance(model_fields_list, list):
                            if not (len(model_fields_list) > 0 and isinstance(model_fields_list[0], (str, int, float))):
                                logger.debug(f"Skipping list-based model_fields due to invalid first element: {str(model_fields_list)[:100]}")
                                continue
                            model_id_path_str = str(model_fields_list[0])
                            current_model_id_for_log = model_id_path_str.split('/')[-1] if model_id_path_str and '/' in model_id_path_str else model_id_path_str
                            display_name_candidate = str(model_fields_list[3]) if len(model_fields_list) > 3 else ""
                            description_candidate = str(model_fields_list[4]) if len(model_fields_list) > 4 else "N/A"
                            
                            if len(model_fields_list) > 6 and model_fields_list[6] is not None:
                                try: 
                                    val_int = int(model_fields_list[6])
                                    default_max_output_tokens_val = val_int
                                    supported_max_output_tokens_val = val_int
                                except (ValueError, TypeError):
                                    logger.warning(f"模型 {current_model_id_for_log}: 无法将列表索引6的值 '{model_fields_list[6]}' 解析为 max_output_tokens。")
                            
                            if len(model_fields_list) > 9 and model_fields_list[9] is not None:
                                try: 
                                    raw_top_p = float(model_fields_list[9])
                                    if not (0.0 <= raw_top_p <= 1.0):
                                        logger.warning(f"模型 {current_model_id_for_log}: 原始 top_p值 {raw_top_p} (来自列表索引9) 超出 [0,1] 范围，将裁剪。")
                                        default_top_p_val = max(0.0, min(1.0, raw_top_p))
                                    else:
                                        default_top_p_val = raw_top_p
                                except (ValueError, TypeError):
                                    logger.warning(f"模型 {current_model_id_for_log}: 无法将列表索引9的值 '{model_fields_list[9]}' 解析为 top_p。")

                        elif isinstance(model_fields_list, dict):
                            model_id_path_str = str(model_fields_list.get('id', model_fields_list.get('model_id', model_fields_list.get('modelId'))))
                            current_model_id_for_log = model_id_path_str.split('/')[-1] if model_id_path_str and '/' in model_id_path_str else model_id_path_str
                            display_name_candidate = str(model_fields_list.get('displayName', model_fields_list.get('display_name', model_fields_list.get('name', ''))))
                            description_candidate = str(model_fields_list.get('description', "N/A"))
                            
                            mot_parsed = model_fields_list.get('maxOutputTokens', model_fields_list.get('defaultMaxOutputTokens', model_fields_list.get('outputTokenLimit')))
                            if mot_parsed is not None:
                                try: 
                                    val_int = int(mot_parsed)
                                    default_max_output_tokens_val = val_int
                                    supported_max_output_tokens_val = val_int
                                except (ValueError, TypeError):
                                     logger.warning(f"模型 {current_model_id_for_log}: 无法将字典值 '{mot_parsed}' 解析为 max_output_tokens。")

                            top_p_parsed = model_fields_list.get('topP', model_fields_list.get('defaultTopP'))
                            if top_p_parsed is not None:
                                try: 
                                    raw_top_p = float(top_p_parsed)
                                    if not (0.0 <= raw_top_p <= 1.0):
                                        logger.warning(f"模型 {current_model_id_for_log}: 原始 top_p值 {raw_top_p} (来自字典) 超出 [0,1] 范围，将裁剪。")
                                        default_top_p_val = max(0.0, min(1.0, raw_top_p))
                                    else:
                                        default_top_p_val = raw_top_p
                                except (ValueError, TypeError):
                                    logger.warning(f"模型 {current_model_id_for_log}: 无法将字典值 '{top_p_parsed}' 解析为 top_p。")
                            
                            temp_parsed = model_fields_list.get('temperature', model_fields_list.get('defaultTemperature'))
                            if temp_parsed is not None:
                                try: default_temperature_val = float(temp_parsed)
                                except (ValueError, TypeError):
                                    logger.warning(f"模型 {current_model_id_for_log}: 无法将字典值 '{temp_parsed}' 解析为 temperature。")
                        else: 
                            logger.debug(f"Skipping entry because model_fields_list is not list or dict: {type(model_fields_list)}")
                            continue
                    except Exception as e_parse_fields:
                        logger.error(f"解析模型字段时出错 for entry {str(entry_in_container)[:100]}: {e_parse_fields}")
                        continue 

                    if model_id_path_str and model_id_path_str.lower() != "none":
                        simple_model_id_str = model_id_path_str.split('/')[-1] if '/' in model_id_path_str else model_id_path_str
                        if simple_model_id_str in excluded_model_ids: # Using excluded_model_ids as per assumption
                            logger.info(f"模型 '{simple_model_id_str}' 在排除列表 excluded_model_ids 中，已跳过。")
                            continue
                        
                        final_display_name_str = display_name_candidate if display_name_candidate else simple_model_id_str.replace("-", " ").title()
                        
                        model_entry_dict = {
                            "id": simple_model_id_str, "object": "model", "created": int(time.time()),
                            "owned_by": "ai_studio", "display_name": final_display_name_str,
                            "description": description_candidate, "raw_model_path": model_id_path_str,
                            "default_temperature": default_temperature_val,
                            "default_max_output_tokens": default_max_output_tokens_val,
                            "supported_max_output_tokens": supported_max_output_tokens_val,
                            "default_top_p": default_top_p_val
                        }
                        new_parsed_list.append(model_entry_dict)
                    else:
                        logger.debug(f"Skipping entry due to invalid model_id_path: {model_id_path_str} from entry {str(entry_in_container)[:100]}")
                
                if new_parsed_list:
                    parsed_model_list = sorted(new_parsed_list, key=lambda m: m.get('display_name', '').lower())
                    global_model_list_raw_json = json.dumps({"data": parsed_model_list, "object": "list"})
                    if DEBUG_LOGS_ENABLED:
                        log_output = f"成功解析和更新模型列表。总共解析模型数: {len(parsed_model_list)}.\n"
                        for i, item in enumerate(parsed_model_list[:min(3, len(parsed_model_list))]):
                            log_output += f"  Model {i+1}: ID={item.get('id')}, Name={item.get('display_name')}, Temp={item.get('default_temperature')}, MaxTokDef={item.get('default_max_output_tokens')}, MaxTokSup={item.get('supported_max_output_tokens')}, TopP={item.get('default_top_p')}\n"
                        logger.info(log_output)
                    if not model_list_fetch_event.is_set(): model_list_fetch_event.set()
                elif not parsed_model_list: 
                    logger.warning("解析后模型列表仍然为空。")
                    if not model_list_fetch_event.is_set(): model_list_fetch_event.set()
            else: 
                logger.warning("models_array_container 为 None，无法解析模型列表。")
                if not model_list_fetch_event.is_set(): model_list_fetch_event.set()

        except json.JSONDecodeError as json_err:
            logger.error(f"解析模型列表JSON失败: {json_err}. 响应 (前500字): {await response.text()[:500]}")
        except Exception as e_handle_list_resp:
            logger.exception(f"处理模型列表响应时发生未知错误: {e_handle_list_resp}")
        finally:
            if not model_list_fetch_event.is_set():
                logger.info("处理模型列表响应结束，强制设置 model_list_fetch_event。")
                model_list_fetch_event.set()

async def signal_camoufox_shutdown():
    # (此函数在 server.py 中可能不再需要，应由 launch_camoufox.py 控制 Camoufox 进程)
    # 但如果 Camoufox 是一个独立的外部服务，则此逻辑可能仍然相关。
    # 当前假设 Camoufox 是由 launch_camoufox.py 管理的内部进程。
    logger.info("   尝试发送关闭信号到 Camoufox 服务器 (此功能可能已由父进程处理)...")
    ws_endpoint = os.environ.get('CAMOUFOX_WS_ENDPOINT')
    if not ws_endpoint:
        logger.warning("   ⚠️ 无法发送关闭信号：未找到 CAMOUFOX_WS_ENDPOINT 环境变量。")
        return
    if not browser_instance or not browser_instance.is_connected():
        logger.warning("   ⚠️ 浏览器实例已断开或未初始化，跳过关闭信号发送。")
        return
    # 实际的关闭信号发送逻辑取决于 Camoufox 如何接收关闭指令。
    # 这里只是一个占位符。
    try:
        # 例如，如果 Camoufox 有一个特殊的 WebSocket 消息或 HTTP 端点用于关闭：
        # await send_shutdown_command_to_camoufox(ws_endpoint)
        await asyncio.sleep(0.2) # 模拟操作
        logger.info("   ✅ (模拟) 关闭信号已处理。")
    except Exception as e:
        logger.error(f"   ⚠️ 发送关闭信号过程中捕获异常: {e}", exc_info=True)


# --- Lifespan Context Manager (负责初始化和清理) ---
@asynccontextmanager
async def lifespan(app_param: FastAPI): # app_param 未使用
    global playwright_manager, browser_instance, page_instance, worker_task
    global is_playwright_ready, is_browser_connected, is_page_ready, is_initializing
    global logger, log_ws_manager, model_list_fetch_event, current_ai_studio_model_id, excluded_model_ids
    global request_queue, processing_lock, model_switching_lock # 将这些也声明为 global 以便赋值

    true_original_stdout, true_original_stderr = sys.stdout, sys.stderr
    initial_stdout_before_redirect, initial_stderr_before_redirect = sys.stdout, sys.stderr

    # 确保 log_ws_manager 在日志设置前初始化
    if log_ws_manager is None:
        log_ws_manager = WebSocketConnectionManager()

    log_level_env = os.environ.get('SERVER_LOG_LEVEL', 'INFO')
    redirect_print_env = os.environ.get('SERVER_REDIRECT_PRINT', 'false')
    
    initial_stdout_before_redirect, initial_stderr_before_redirect = setup_server_logging(
        log_level_name=log_level_env,
        redirect_print_str=redirect_print_env
    )

    # 在此处初始化 asyncio 同步原语，以确保它们绑定到正确的事件循环
    request_queue = asyncio.Queue()
    processing_lock = asyncio.Lock()
    model_switching_lock = asyncio.Lock()
    model_list_fetch_event = asyncio.Event()

    if PLAYWRIGHT_PROXY_SETTINGS:
        logger.info(f"--- 代理配置检测到 (由 server.py 的 lifespan 记录) ---")
        logger.info(f"   将使用代理服务器: {PLAYWRIGHT_PROXY_SETTINGS['server']}")
        if 'bypass' in PLAYWRIGHT_PROXY_SETTINGS:
            logger.info(f"   绕过代理的主机: {PLAYWRIGHT_PROXY_SETTINGS['bypass']}")
        logger.info(f"-----------------------")
    else:
        logger.info("--- 未检测到 HTTP_PROXY 或 HTTPS_PROXY 环境变量，不使用代理 (由 server.py 的 lifespan 记录) ---")

    # 新增: 加载模型排除列表
    load_excluded_models(EXCLUDED_MODELS_FILENAME)

    is_initializing = True
    logger.info("\n" + "="*60 + "\n          🚀 AI Studio Proxy Server (FastAPI App Lifespan) 🚀\n" + "="*60)
    logger.info(f"FastAPI 应用生命周期: 启动中...")
    try:
        logger.info(f"   启动 Playwright...")
        playwright_manager = await async_playwright().start()
        is_playwright_ready = True
        logger.info(f"   ✅ Playwright 已启动。")

        ws_endpoint = os.environ.get('CAMOUFOX_WS_ENDPOINT')
        launch_mode = os.environ.get('LAUNCH_MODE', 'unknown')

        if not ws_endpoint:
            if launch_mode == "direct_debug_no_browser":
                logger.warning("CAMOUFOX_WS_ENDPOINT 未设置，但 LAUNCH_MODE 表明不需要浏览器。跳过浏览器连接。")
                is_browser_connected = False
                is_page_ready = False
                model_list_fetch_event.set() # 没有页面，无法获取，直接设置事件
            else:
                logger.error("未找到 CAMOUFOX_WS_ENDPOINT 环境变量。Playwright 将无法连接到浏览器。")
                raise ValueError("CAMOUFOX_WS_ENDPOINT 环境变量缺失。")
        else:
            logger.info(f"   连接到 Camoufox 服务器 (浏览器 WebSocket 端点) 于: {ws_endpoint}")
            try:
                browser_instance = await playwright_manager.firefox.connect(ws_endpoint, timeout=30000)
                is_browser_connected = True
                logger.info(f"   ✅ 已连接到浏览器实例: 版本 {browser_instance.version}")
                
                # _initialize_page_logic 返回 page 实例，并将其赋值给全局 page_instance
                temp_page_instance, temp_is_page_ready = await _initialize_page_logic(browser_instance)
                if temp_page_instance and temp_is_page_ready:
                    page_instance = temp_page_instance
                    is_page_ready = temp_is_page_ready
                    
                    # 检查并处理初始 localStorage 和模型状态
                    await _handle_initial_model_state_and_storage(page_instance)

                else: # _initialize_page_logic 失败
                    is_page_ready = False
                    if not model_list_fetch_event.is_set(): model_list_fetch_event.set()


            except Exception as connect_err:
                logger.error(f"未能连接到 Camoufox 服务器 (浏览器) 或初始化页面失败: {connect_err}", exc_info=True)
                if launch_mode != "direct_debug_no_browser":
                    raise RuntimeError(f"未能连接到 Camoufox 或初始化页面: {connect_err}") from connect_err
                else:
                    is_browser_connected = False
                    is_page_ready = False
                    if not model_list_fetch_event.is_set(): model_list_fetch_event.set() # 没有页面，直接设置

        # 等待模型列表捕获或超时
        if is_page_ready and is_browser_connected and not model_list_fetch_event.is_set():
            logger.info("等待模型列表捕获 (最多等待15秒)...")
            try:
                await asyncio.wait_for(model_list_fetch_event.wait(), timeout=15.0) # 增加等待时间
                if model_list_fetch_event.is_set():
                    logger.info("模型列表事件已触发。")
                else: # 超时但事件未设置（理论上wait_for会抛TimeoutError）
                    logger.warning("模型列表事件等待后仍未设置。")
            except asyncio.TimeoutError:
                logger.warning("等待模型列表捕获超时。将使用默认或空列表。")
            finally: # 确保事件最终被设置，避免后续阻塞
                if not model_list_fetch_event.is_set():
                    model_list_fetch_event.set()
        elif not (is_page_ready and is_browser_connected): # 如果页面/浏览器没准备好，也设置事件
             if not model_list_fetch_event.is_set(): model_list_fetch_event.set()


        if (is_page_ready and is_browser_connected) or launch_mode == "direct_debug_no_browser":
             logger.info(f"   启动请求处理 Worker...")
             worker_task = asyncio.create_task(queue_worker())
             logger.info(f"   ✅ 请求处理 Worker 已启动。")
        elif launch_mode == "direct_debug_no_browser":
            logger.warning("浏览器和页面未就绪 (direct_debug_no_browser 模式)，请求处理 Worker 未启动。API 可能功能受限。")
        else:
             logger.error("页面或浏览器初始化失败，无法启动 Worker。")
             if not model_list_fetch_event.is_set(): model_list_fetch_event.set() # 确保事件设置
             raise RuntimeError("页面或浏览器初始化失败，无法启动 Worker。")

        logger.info(f"✅ FastAPI 应用生命周期: 启动完成。服务已就绪。")
        is_initializing = False
        yield

    except Exception as startup_err:
        logger.critical(f"❌ FastAPI 应用生命周期: 启动期间发生严重错误: {startup_err}", exc_info=True)
        if not model_list_fetch_event.is_set(): model_list_fetch_event.set() # 错误情况下也设置
        if worker_task and not worker_task.done(): worker_task.cancel()
        if browser_instance and browser_instance.is_connected():
            try: await browser_instance.close()
            except: pass
        if playwright_manager:
            try: await playwright_manager.stop()
            except: pass
        raise RuntimeError(f"应用程序启动失败: {startup_err}") from startup_err
    finally:
        is_initializing = False # 重置状态
        logger.info(f"\nFastAPI 应用生命周期: 关闭中...")
        if worker_task and not worker_task.done():
             logger.info(f"   正在取消请求处理 Worker...")
             worker_task.cancel()
             try:
                 await asyncio.wait_for(worker_task, timeout=5.0)
                 logger.info(f"   ✅ 请求处理 Worker 已停止/取消。")
             except asyncio.TimeoutError: logger.warning(f"   ⚠️ Worker 等待超时。")
             except asyncio.CancelledError: logger.info(f"   ✅ 请求处理 Worker 已确认取消。")
             except Exception as wt_err: logger.error(f"   ❌ 等待 Worker 停止时出错: {wt_err}", exc_info=True)

        if page_instance and not page_instance.is_closed(): # 在关闭页面前，确保移除监听器
            try:
                # 尝试移除，以防 _handle_model_list_response 未成功执行或未移除
                # page_instance.remove_listener("response", _handle_model_list_response) # 原有代码
                # logger.info("Lifespan 清理：尝试移除模型列表响应监听器。") # 原有代码
                logger.info("Lifespan 清理：移除模型列表响应监听器。")
                page_instance.remove_listener("response", _handle_model_list_response)
            except Exception as e: # 比如监听器不存在的错误
                logger.debug(f"Lifespan 清理：移除监听器时发生非严重错误或监听器本不存在: {e}")
        
        if page_instance: 
            await _close_page_logic() # 这会设置 page_instance = None

        if browser_instance:
            logger.info(f"   正在关闭与浏览器实例的连接...")
            try:
                if browser_instance.is_connected():
                    await browser_instance.close()
                    logger.info(f"   ✅ 浏览器连接已关闭。")
                else: logger.info(f"   ℹ️ 浏览器先前已断开连接。")
            except Exception as close_err: logger.error(f"   ❌ 关闭浏览器连接时出错: {close_err}", exc_info=True)
            finally: browser_instance = None; is_browser_connected = False; is_page_ready = False

        if playwright_manager:
            logger.info(f"   停止 Playwright...")
            try:
                await playwright_manager.stop()
                logger.info(f"   ✅ Playwright 已停止。")
            except Exception as stop_err: logger.error(f"   ❌ 停止 Playwright 时出错: {stop_err}", exc_info=True)
            finally: playwright_manager = None; is_playwright_ready = False
        
        restore_original_streams(initial_stdout_before_redirect, initial_stderr_before_redirect)
        restore_original_streams(true_original_stdout, true_original_stderr) # 再次确保恢复到最原始的
        logger.info(f"✅ FastAPI 应用生命周期: 关闭完成。")


# --- FastAPI App 定义 ---
app = FastAPI(
    title="AI Studio Proxy Server (集成模式)",
    description="通过 Playwright与 AI Studio 交互的代理服务器。",
    version="0.6.0-integrated",
    lifespan=lifespan
)

# --- API Endpoints ---
@app.get("/", response_class=FileResponse)
async def read_index():
    index_html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if not os.path.exists(index_html_path):
        logger.error(f"index.html not found at {index_html_path}")
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_html_path)

@app.get("/webui.css")
async def get_css():
    css_path = os.path.join(os.path.dirname(__file__), "webui.css")
    if not os.path.exists(css_path):
        logger.error(f"webui.css not found at {css_path}")
        raise HTTPException(status_code=404, detail="webui.css not found")
    return FileResponse(css_path, media_type="text/css")

@app.get("/webui.js")
async def get_js():
    js_path = os.path.join(os.path.dirname(__file__), "webui.js")
    if not os.path.exists(js_path):
        logger.error(f"webui.js not found at {js_path}")
        raise HTTPException(status_code=404, detail="webui.js not found")
    return FileResponse(js_path, media_type="application/javascript")

@app.get("/api/info")
async def get_api_info(request: Request):
    server_port = request.url.port
    if not server_port and hasattr(request.app.state, 'server_port'):
        server_port = request.app.state.server_port
    if not server_port: # 最终后备
        # 尝试从环境变量获取，如果 launch_camoufox.py 设置了它
        server_port = os.environ.get('SERVER_PORT_INFO', '8000')


    host = request.headers.get('host') or f"127.0.0.1:{server_port}"
    scheme = request.headers.get('x-forwarded-proto', 'http')
    base_url = f"{scheme}://{host}"
    api_base = f"{base_url}/v1"

    # 使用全局 current_ai_studio_model_id (如果有效) 作为模型名称，否则回退到 MODEL_NAME
    effective_model_name = current_ai_studio_model_id if current_ai_studio_model_id else MODEL_NAME
    
    return JSONResponse(content={
        "model_name": effective_model_name, # 使用获取到的或默认的模型名称
        "api_base_url": api_base,
        "server_base_url": base_url,
        "api_key_required": False,
        "message": "API Key is not required."
    })

@app.get("/health")
async def health_check():
    is_worker_running = bool(worker_task and not worker_task.done())
    launch_mode = os.environ.get('LAUNCH_MODE', 'unknown')
    browser_page_critical = launch_mode != "direct_debug_no_browser"

    core_ready_conditions = [not is_initializing, is_playwright_ready]
    if browser_page_critical:
        core_ready_conditions.extend([is_browser_connected, is_page_ready])
    
    is_core_ready = all(core_ready_conditions)
    status_val = "OK" if is_core_ready and is_worker_running else "Error"
    q_size = request_queue.qsize() if request_queue else -1
    
    status_message_parts = []
    if is_initializing: status_message_parts.append("初始化进行中")
    if not is_playwright_ready: status_message_parts.append("Playwright 未就绪")
    if browser_page_critical:
        if not is_browser_connected: status_message_parts.append("浏览器未连接")
        if not is_page_ready: status_message_parts.append("页面未就绪")
    if not is_worker_running: status_message_parts.append("Worker 未运行")

    status = {
        "status": status_val,
        "message": "",
        "details": {
            "playwrightReady": is_playwright_ready,
            "browserConnected": is_browser_connected,
            "pageReady": is_page_ready,
            "initializing": is_initializing,
            "workerRunning": is_worker_running,
            "queueLength": q_size,
            "launchMode": launch_mode,
            "browserAndPageCritical": browser_page_critical
        }
    }
    if status_val == "OK":
        status["message"] = f"服务运行中;队列长度: {q_size}。"
        return JSONResponse(content=status, status_code=200)
    else:
        status["message"] = f"服务不可用;问题: {(', '.join(status_message_parts) if status_message_parts else '未知原因')}. 队列长度: {q_size}."
        return JSONResponse(content=status, status_code=503)

@app.get("/v1/models")
async def list_models():
    logger.info("[API] 收到 /v1/models 请求。")
    # 如果事件未设置且页面实例存在，尝试触发一次获取
    if not model_list_fetch_event.is_set() and page_instance and not page_instance.is_closed():
        logger.info("/v1/models: 模型列表事件未设置或列表为空，尝试页面刷新以触发捕获...")
        try:
            # 检查监听器是否已附加，如果未附加，则添加。
            listener_attached = False
            # Playwright的事件监听器存储方式可能因版本而异
            # _events 属性是非公开API，但可用于调试或此种检查
            if hasattr(page_instance, '_events') and "response" in page_instance._events:
                for handler_slot_or_func in page_instance._events["response"]:
                    # 在Playwright 1.30+版本中，监听器被包装在HandlerSlot对象中
                    actual_handler = getattr(handler_slot_or_func, 'handler', handler_slot_or_func)
                    if actual_handler == _handle_model_list_response:
                        listener_attached = True
                        break
            
            if not listener_attached:
                logger.info("/v1/models: 响应监听器似乎不存在或已被移除，尝试重新添加。")
                page_instance.on("response", _handle_model_list_response)


            await page_instance.reload(wait_until="domcontentloaded", timeout=20000)
            logger.info(f"页面已刷新。等待模型列表事件 (最多10秒)...")
            await asyncio.wait_for(model_list_fetch_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("/v1/models: 刷新后等待模型列表事件超时。")
        except PlaywrightAsyncError as reload_err:
            logger.error(f"/v1/models: 刷新页面失败: {reload_err}")
        except Exception as e: 
            logger.error(f"/v1/models: 尝试触发模型列表捕获时发生错误: {e}")
        finally: # 无论如何，确保事件最终被设置，避免后续请求卡住
            if not model_list_fetch_event.is_set():
                logger.info("/v1/models: 尝试捕获后，强制设置模型列表事件。")
                model_list_fetch_event.set()


    if parsed_model_list:
        # 过滤掉排除列表中的模型
        final_model_list = [m for m in parsed_model_list if m.get("id") not in excluded_model_ids]
        logger.info(f"返回过滤后的 {len(final_model_list)} 个模型 (原缓存 {len(parsed_model_list)} 个)。排除的有: {excluded_model_ids.intersection(set(m.get('id') for m in parsed_model_list))}")
        return {"object": "list", "data": final_model_list}
    else:
        logger.warning("模型列表为空或未成功获取。返回默认后备模型。")
        # 返回符合 OpenAI API 风格的列表，即使是后备
        fallback_model_obj = {
            "id": DEFAULT_FALLBACK_MODEL_ID, 
            "object": "model",
            "created": int(time.time()), 
            "owned_by": "camoufox-proxy-fallback",
            "display_name": DEFAULT_FALLBACK_MODEL_ID.replace("-", " ").title(),
            "description": "Default fallback model.",
            "raw_model_path": f"models/{DEFAULT_FALLBACK_MODEL_ID}"
        }
        return {"object": "list", "data": [fallback_model_obj]}

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
    logger.info(f"[{req_id}] (Helper) 尝试通过编辑按钮获取响应...") # logger
    edit_button = page.locator(EDIT_MESSAGE_BUTTON_SELECTOR)
    textarea = page.locator(MESSAGE_TEXTAREA_SELECTOR)
    finish_edit_button = page.locator(FINISH_EDIT_BUTTON_SELECTOR)

    try:
        # 1. Click the Edit button
        logger.info(f"[{req_id}]   - 定位并点击编辑按钮...") # logger
        try:
            # Direct Playwright calls with timeout
            await expect_async(edit_button).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("编辑响应 - 编辑按钮可见后: ")
            await edit_button.click(timeout=CLICK_TIMEOUT_MS)
            logger.info(f"[{req_id}]   - 编辑按钮已点击。") # logger
        except Exception as edit_btn_err:
            logger.error(f"[{req_id}]   - 编辑按钮不可见或点击失败: {edit_btn_err}") # logger
            await save_error_snapshot(f"edit_response_edit_button_failed_{req_id}")
            return None

        check_client_disconnected("编辑响应 - 点击编辑按钮后: ")
        await asyncio.sleep(0.3) # Use asyncio.sleep
        check_client_disconnected("编辑响应 - 点击编辑按钮后延时后: ")

        # 2. Get content from textarea
        logger.info(f"[{req_id}]   - 从文本区域获取内容...") # logger
        response_content = None
        textarea_failed = False # Flag to track if textarea read failed
        try:
            # Direct Playwright call with timeout
            await expect_async(textarea).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("编辑响应 - 文本区域可见后: ")

            # Try getting content from data-value attribute first
            try:
                # Direct evaluate call (no specific timeout in Playwright evaluate)
                data_value_content = await textarea.evaluate('el => el.getAttribute("data-value")')
                check_client_disconnected("编辑响应 - evaluate data-value 后: ")
                if data_value_content is not None:
                    response_content = str(data_value_content)
            except Exception as data_val_err:
                logger.warning(f"[{req_id}]   - 获取 data-value 失败: {data_val_err}") # logger warning
                check_client_disconnected("编辑响应 - evaluate data-value 错误后: ")

            # If data-value failed or returned empty, try input_value
            if not response_content:
                try:
                    # Direct input_value call with timeout
                    input_val_content = await textarea.input_value(timeout=CLICK_TIMEOUT_MS)
                    check_client_disconnected("编辑响应 - input_value 后: ")
                    if input_val_content is not None:
                        response_content = str(input_val_content)
                except Exception as input_val_err:
                     logger.warning(f"[{req_id}]   - 获取 input_value 失败: {input_val_err}") # logger warning
                     check_client_disconnected("编辑响应 - input_value 错误后: ")

            # Now check the final result from either method
            if response_content is not None and response_content.strip():
                response_content = response_content.strip()
                content_preview = response_content[:100].replace('\\n', '\\\\n')
                logger.info(f"[{req_id}]   - ✅ 最终成功获取内容 (长度={len(response_content)}): '{content_preview}...'") # logger
            else:
                if response_content is None:
                    logger.warning(f"[{req_id}]   - 所有方法 (data-value, input_value) 内容获取均失败或返回 None。") # logger
                else:
                    logger.warning(f"[{req_id}]   - 所有方法 (data-value, input_value) 内容获取返回空字符串。") # logger
                textarea_failed = True
                response_content = None

        except Exception as textarea_err:
            logger.error(f"[{req_id}]   - 定位或处理文本区域时失败: {textarea_err}") # logger
            textarea_failed = True
            response_content = None
            check_client_disconnected("编辑响应 - 获取文本区域错误后: ")

        # 3. Click the Finish Editing button
        if not textarea_failed:
            logger.info(f"[{req_id}]   - 定位并点击完成编辑按钮...") # logger
            try:
                # Direct Playwright calls with timeout
                await expect_async(finish_edit_button).to_be_visible(timeout=CLICK_TIMEOUT_MS)
                check_client_disconnected("编辑响应 - 完成按钮可见后: ")
                await finish_edit_button.click(timeout=CLICK_TIMEOUT_MS)
                logger.info(f"[{req_id}]   - 完成编辑按钮已点击。") # logger
            except Exception as finish_btn_err:
                logger.warning(f"[{req_id}]   - 完成编辑按钮不可见或点击失败: {finish_btn_err}") # logger
                await save_error_snapshot(f"edit_response_finish_button_failed_{req_id}")

            check_client_disconnected("编辑响应 - 点击完成编辑后: ")
            await asyncio.sleep(0.2) # Use asyncio.sleep
            check_client_disconnected("编辑响应 - 点击完成编辑后延时后: ")
        else:
             logger.info(f"[{req_id}]   - 跳过点击完成编辑按钮，因为文本区域读取失败。") # logger

        return response_content if not textarea_failed else None

    except ClientDisconnectedError:
        logger.info(f"[{req_id}] (Helper Edit) 客户端断开连接。") # logger
        raise
    except Exception as e:
        logger.exception(f"[{req_id}] 通过编辑按钮获取响应过程中发生意外错误") # logger
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
    logger.info(f"[{req_id}] (Helper) 尝试通过复制按钮获取响应...") # logger
    more_options_button = page.locator(MORE_OPTIONS_BUTTON_SELECTOR).last # Target last message
    copy_button_primary = page.locator(COPY_MARKDOWN_BUTTON_SELECTOR)
    copy_button_alt = page.locator(COPY_MARKDOWN_BUTTON_SELECTOR_ALT)

    try:
        # 1. Hover over the last message to reveal options
        logger.info(f"[{req_id}]   - 尝试悬停最后一条消息以显示选项...") # logger
        last_message_container = page.locator('ms-chat-turn').last
        try:
            # Direct hover call with timeout
            await last_message_container.hover(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("复制响应 - 悬停后: ")
            await asyncio.sleep(0.5) # Use asyncio.sleep
            check_client_disconnected("复制响应 - 悬停后延时后: ")
            logger.info(f"[{req_id}]   - 已悬停。") # logger
        except Exception as hover_err:
            logger.warning(f"[{req_id}]   - 悬停失败: {hover_err}。尝试直接查找按钮...") # logger
            check_client_disconnected("复制响应 - 悬停失败后: ")
            # Continue, maybe buttons are already visible

        # 2. Click "More options" button
        logger.info(f"[{req_id}]   - 定位并点击 '更多选项' 按钮...") # logger
        try:
            # Direct Playwright calls with timeout
            await expect_async(more_options_button).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("复制响应 - 更多选项按钮可见后: ")
            await more_options_button.click(timeout=CLICK_TIMEOUT_MS)
            logger.info(f"[{req_id}]   - '更多选项' 已点击。") # logger
        except Exception as more_opts_err:
            logger.error(f"[{req_id}]   - '更多选项' 按钮不可见或点击失败: {more_opts_err}") # logger
            await save_error_snapshot(f"copy_response_more_options_failed_{req_id}")
            return None

        check_client_disconnected("复制响应 - 点击更多选项后: ")
        await asyncio.sleep(0.5) # Use asyncio.sleep
        check_client_disconnected("复制响应 - 点击更多选项后延时后: ")

        # 3. Find and click "Copy Markdown" button (try primary, then alt)
        logger.info(f"[{req_id}]   - 定位并点击 '复制 Markdown' 按钮...") # logger
        copy_success = False
        try:
            # Try primary selector
            await expect_async(copy_button_primary).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("复制响应 - 主复制按钮可见后: ")
            await copy_button_primary.click(timeout=CLICK_TIMEOUT_MS, force=True)
            copy_success = True
            logger.info(f"[{req_id}]   - 已点击 '复制 Markdown' (主选择器)。") # logger
        except Exception as primary_copy_err:
            logger.warning(f"[{req_id}]   - 主复制按钮选择器失败 ({primary_copy_err})，尝试备选...") # logger
            check_client_disconnected("复制响应 - 主复制按钮失败后: ")
            try:
                # Try alternative selector
                await expect_async(copy_button_alt).to_be_visible(timeout=CLICK_TIMEOUT_MS)
                check_client_disconnected("复制响应 - 备选复制按钮可见后: ")
                await copy_button_alt.click(timeout=CLICK_TIMEOUT_MS, force=True)
                copy_success = True
                logger.info(f"[{req_id}]   - 已点击 '复制 Markdown' (备选选择器)。") # logger
            except Exception as alt_copy_err:
                logger.error(f"[{req_id}]   - 备选 '复制 Markdown' 按钮失败: {alt_copy_err}") # logger
                await save_error_snapshot(f"copy_response_copy_button_failed_{req_id}")
                return None

        if not copy_success:
             logger.error(f"[{req_id}]   - 未能点击任何 '复制 Markdown' 按钮。") # logger
             return None

        check_client_disconnected("复制响应 - 点击复制按钮后: ")
        await asyncio.sleep(0.5) # Use asyncio.sleep
        check_client_disconnected("复制响应 - 点击复制按钮后延时后: ")

        # 4. Read clipboard content
        logger.info(f"[{req_id}]   - 正在读取剪贴板内容...") # logger
        try:
            # Direct evaluate call (no specific timeout needed)
            clipboard_content = await page.evaluate('navigator.clipboard.readText()')
            check_client_disconnected("复制响应 - 读取剪贴板后: ")

            if clipboard_content:
                content_preview = clipboard_content[:100].replace('\n', '\\\\n')
                logger.info(f"[{req_id}]   - ✅ 成功获取剪贴板内容 (长度={len(clipboard_content)}): '{content_preview}...'") # logger
                return clipboard_content
            else:
                logger.error(f"[{req_id}]   - 剪贴板内容为空。") # logger
                return None
        except Exception as clipboard_err:
            if "clipboard-read" in str(clipboard_err):
                 logger.error(f"[{req_id}]   - 读取剪贴板失败: 可能是权限问题。错误: {clipboard_err}") # logger
            else:
                 logger.error(f"[{req_id}]   - 读取剪贴板失败: {clipboard_err}") # logger
            await save_error_snapshot(f"copy_response_clipboard_read_failed_{req_id}")
            return None

    except ClientDisconnectedError:
        logger.info(f"[{req_id}] (Helper Copy) 客户端断开连接。") # logger
        raise
    except Exception as e:
        logger.exception(f"[{req_id}] 复制响应过程中发生意外错误") # logger
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
             logger.exception(f"[{req_id}] (Helper Wait) 状态检查中发生意外错误") # logger
             await save_error_snapshot(f"wait_completion_state_check_unexpected_{req_id}")
             await asyncio.sleep(POLLING_INTERVAL_STREAM / 1000) # Still use sleep here
             continue

        # --- Logging and Continuation Logic ---
        is_final_state = spinner_hidden and input_empty and button_disabled
        if not is_final_state:
            if DEBUG_LOGS_ENABLED:
                reason = "Spinner not hidden" if not spinner_hidden else ("Input not empty" if not input_empty else "Submit button not disabled")
                error_info = f" (Last Check Error: {type(state_check_error).__name__})" if state_check_error else ""
                logger.debug(f"[{req_id}] (Helper Wait) 基础状态未满足 ({reason}{error_info})。继续轮询...") # logger debug
            # Use standard asyncio.sleep with stream interval
            await asyncio.sleep(POLLING_INTERVAL_STREAM / 1000)
            continue

        # --- If base conditions met, check for Edit Button --- (Mirroring original stream logic)
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
                        logger.debug(f"[{req_id}] (Helper Wait)   - 尝试聚焦响应元素...") # logger debug
                    # Revert focus click to direct call if strict matching is required
                    await response_element.click(timeout=1000, position={'x': 10, 'y': 10}, force=True)
                    last_focus_attempt_time = current_time
                    await asyncio.sleep(0.1) # Use asyncio.sleep
                except (PlaywrightAsyncError, asyncio.TimeoutError) as focus_err:
                     if DEBUG_LOGS_ENABLED:
                          logger.debug(f"[{req_id}] (Helper Wait)   - 聚焦响应元素失败 (忽略): {type(focus_err).__name__}") # logger debug
                except ClientDisconnectedError: raise
                except Exception as unexpected_focus_err:
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
                    logger.warning(f"[{req_id}] (Helper Wait)   - is_visible 检查Playwright错误(忽略): {pw_vis_err}") # logger warning
                    is_visible = False

                check_client_disconnected("等待完成 - 编辑按钮 is_visible 检查后: ")

                if is_visible:
                    logger.info(f"[{req_id}] (Helper Wait) ✅ 编辑按钮已出现 (is_visible)，确认响应完成。") # logger
                    edit_button_visible = True
                    return True
                else:
                      if DEBUG_LOGS_ENABLED and (time.time() - edit_button_check_start) > 1.0:
                           logger.debug(f"[{req_id}] (Helper Wait)   - 编辑按钮尚不可见... (is_visible returned False or timed out)") # logger debug

            except ClientDisconnectedError: raise
            except Exception as unexpected_btn_err:
                 logger.warning(f"[{req_id}] (Helper Wait)   - 检查编辑按钮时意外错误: {unexpected_btn_err}") # logger warning

            # Wait before next check using asyncio.sleep
            await asyncio.sleep(POLLING_INTERVAL_STREAM / 1000)
        # --- End of Edit Button Check Loop ---

        # If edit button didn't appear within SILENCE_TIMEOUT_MS after base state met
        if not edit_button_visible:
            logger.warning(f"[{req_id}] (Helper Wait) 基础状态满足后，编辑按钮未在 {SILENCE_TIMEOUT_MS}ms 内出现。判定为超时。") # logger
            await save_error_snapshot(f"wait_completion_edit_button_timeout_{req_id}")
            return False

    # --- End of Main While Loop (Overall Timeout) ---
    logger.error(f"[{req_id}] (Helper Wait) 等待响应完成超时 ({RESPONSE_COMPLETION_TIMEOUT}ms)。") # logger
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
    logger.info(f"[{req_id}] (Helper GetContent) 开始获取最终响应内容...") # logger

    # 1. Try getting content via Edit Button first (more reliable)
    response_content = await get_response_via_edit_button(
        page, req_id, check_client_disconnected
    )

    if response_content is not None:
        logger.info(f"[{req_id}] (Helper GetContent) ✅ 成功通过编辑按钮获取内容。") # logger
        return response_content

    # 2. If Edit Button failed, fall back to Copy Button
    logger.warning(f"[{req_id}] (Helper GetContent) 编辑按钮方法失败或返回空，回退到复制按钮方法...") # logger
    response_content = await get_response_via_copy_button(
        page, req_id, check_client_disconnected
    )

    if response_content is not None:
        logger.info(f"[{req_id}] (Helper GetContent) ✅ 成功通过复制按钮获取内容。") # logger
        return response_content

    # 3. If both methods failed
    logger.error(f"[{req_id}] (Helper GetContent) 所有获取响应内容的方法均失败。") # logger
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
    logger.info(f"[{req_id}] (Refactored Process) 开始处理请求...") # logger
    logger.info(f"[{req_id}]   请求参数 - Model: {request.model}, Stream: {request.stream}")
    logger.info(f"[{req_id}]   请求参数 - Temperature: {request.temperature}")
    logger.info(f"[{req_id}]   请求参数 - Max Output Tokens: {request.max_output_tokens}")
    logger.info(f"[{req_id}]   请求参数 - Stop Sequences: {request.stop}")
    logger.info(f"[{req_id}]   请求参数 - Top P: {request.top_p}")
    is_streaming = request.stream
    page: Optional[AsyncPage] = page_instance # Use global instance
    completion_event: Optional[Event] = None # For streaming
    
    # --- 新增: 模型验证和切换逻辑 ---
    requested_model = request.model
    model_id_to_use = None
    needs_model_switching = False
    
    if requested_model and requested_model != MODEL_NAME: # 如果指定了具体模型且不是代理模型名
        # 从请求模型中提取真正的模型ID (例如从 "gemini-1.5-pro-latest" 提取)
        requested_model_parts = requested_model.split('/')
        requested_model_id = requested_model_parts[-1] if len(requested_model_parts) > 1 else requested_model
        
        # 强制验证模型
        logger.info(f"[{req_id}] 请求使用模型: {requested_model_id}")
        
        if parsed_model_list: # 如果我们有已知模型列表
            valid_model_ids = [m.get("id") for m in parsed_model_list]
            if requested_model_id not in valid_model_ids:
                logger.error(f"[{req_id}] ❌ 无效的模型ID: {requested_model_id}。可用模型: {valid_model_ids}")
                raise HTTPException(status_code=400, detail=f"[{req_id}] Invalid model '{requested_model_id}'. Available models: {', '.join(valid_model_ids)}")
            
        model_id_to_use = requested_model_id
        
        # 检查是否需要切换模型
        global current_ai_studio_model_id
        if current_ai_studio_model_id != model_id_to_use:
            needs_model_switching = True
            logger.info(f"[{req_id}] 需要切换模型: 当前={current_ai_studio_model_id} -> 目标={model_id_to_use}")
        else:
            logger.info(f"[{req_id}] 请求模型与当前模型相同 ({model_id_to_use})，无需切换")
    else:
        logger.info(f"[{req_id}] 未指定具体模型或使用代理模型名称，将使用当前模型: {current_ai_studio_model_id or '未知'}")

    # --- Setup Disconnect Handling --- (Same as before)
    client_disconnected_event = Event()
    disconnect_check_task = None
    input_field_locator = page.locator(INPUT_SELECTOR)
    submit_button_locator = page.locator(SUBMIT_BUTTON_SELECTOR)

    async def check_disconnect_periodically():
        while not client_disconnected_event.is_set():
            try:
                if await http_request.is_disconnected():
                    logger.info(f"[{req_id}] (Disco Check Task) 客户端断开。设置事件并尝试停止。") # logger
                    client_disconnected_event.set()
                    try: # Attempt to click stop button
                        if await submit_button_locator.is_enabled(timeout=1500):
                             if await input_field_locator.input_value(timeout=1500) == '':
                                 logger.info(f"[{req_id}] (Disco Check Task)   点击停止...") # logger
                                 await submit_button_locator.click(timeout=3000, force=True)
                    except Exception as click_err: logger.warning(f"[{req_id}] (Disco Check Task) 停止按钮点击失败: {click_err}") # logger warning
                    if not result_future.done(): result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端在处理期间关闭了请求"))
                    break
                await asyncio.sleep(1.0)
            except asyncio.CancelledError: break
            except Exception as e:
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
        
        # --- 新增: 执行模型切换 ---
        if needs_model_switching and model_id_to_use:
            async with model_switching_lock:  # 使用锁确保一次只有一个请求在切换模型
                model_before_switch_attempt = current_ai_studio_model_id # 用于日志和回退
                # 再次检查当前模型，因为在获取锁的过程中可能已经被其他请求更新
                if current_ai_studio_model_id != model_id_to_use:
                    logger.info(f"[{req_id}] 获取锁后准备切换: 当前内存中模型={current_ai_studio_model_id}, 目标={model_id_to_use}")
                    
                    switch_success = await switch_ai_studio_model(page, model_id_to_use, req_id)
                    
                    if switch_success:
                        current_ai_studio_model_id = model_id_to_use # 更新全局为目标模型 ID
                        logger.info(f"[{req_id}] ✅ 模型切换成功。全局模型状态已更新为: {current_ai_studio_model_id}")
                    else:
                        logger.warning(f"[{req_id}] ❌ 模型切换至 {model_id_to_use} 失败 (AI Studio 未接受或覆盖了更改)。")
                        
                        # 确定切换失败后 localStorage 中的实际模型
                        active_model_id_after_fail = model_before_switch_attempt # 默认回退到尝试切换前的模型
                        try:
                            final_prefs_str_after_fail = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
                            if final_prefs_str_after_fail:
                                final_prefs_obj_after_fail = json.loads(final_prefs_str_after_fail)
                                model_path_in_final_prefs = final_prefs_obj_after_fail.get("promptModel")
                                if model_path_in_final_prefs and isinstance(model_path_in_final_prefs, str):
                                    active_model_id_after_fail = model_path_in_final_prefs.split('/')[-1]
                        except Exception as read_final_prefs_err:
                            logger.error(f"[{req_id}] 切换失败后读取最终 localStorage 出错: {read_final_prefs_err}")
                        
                        current_ai_studio_model_id = active_model_id_after_fail # 更新全局状态为实际生效的模型
                        logger.info(f"[{req_id}] 全局模型状态在切换失败后设置为 (或保持为): {current_ai_studio_model_id}")

                        # 获取页面实际显示的名称用于错误提示
                        actual_displayed_model_name = "未知 (无法读取)"
                        try:
                            model_wrapper_locator = page.locator('#mat-select-value-0 mat-select-trigger').first
                            actual_displayed_model_name = await model_wrapper_locator.inner_text(timeout=3000)
                        except Exception:
                            pass # 允许读取失败，使用默认值
                                        
                        raise HTTPException(
                            status_code=422, # Unprocessable Entity
                            detail=f"[{req_id}] AI Studio 未能应用所请求的模型 '{model_id_to_use}' 或该模型不受支持。请选择 AI Studio 网页界面中可用的模型。当前实际生效的模型 ID 为 '{current_ai_studio_model_id}', 页面显示为 '{actual_displayed_model_name}'."
                        )
                else:
                    logger.info(f"[{req_id}] 获取锁后发现模型已是目标模型 {current_ai_studio_model_id}，无需切换")

        # --- 1. Validation & Prompt Prep --- (Use logger for validation message)
        try: validate_chat_request(request.messages, req_id)
        except ValueError as e: raise HTTPException(status_code=400, detail=f"[{req_id}] 无效请求: {e}")
        # Validation log is already inside validate_chat_request using print, change it there too?
        # For now, assume prepare_combined_prompt handles its own logging via print->logger
        prepared_prompt = prepare_combined_prompt(request.messages, req_id)
        check_client_disconnected("After Prompt Prep: ")

        # --- 2. Clear Chat --- (Revert to direct calls, use logger for messages)
        logger.info(f"[{req_id}] (Refactored Process) 开始清空聊天记录...") # logger
        try:
            clear_chat_button = page.locator(CLEAR_CHAT_BUTTON_SELECTOR)
            confirm_button = page.locator(CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR)
            overlay_locator = page.locator('div.cdk-overlay-backdrop') # Locator for the overlay
            proceed_with_clear_clicks = False
            try:
                # Direct call with timeout
                await expect_async(clear_chat_button).to_be_enabled(timeout=3000) # Increased timeout slightly
                proceed_with_clear_clicks = True
            except Exception as e:
                is_new_chat_url = '/prompts/new_chat' in page.url.rstrip('/')
                if is_new_chat_url:
                    logger.info(f"[{req_id}] 清空按钮不可用 (预期)。") # logger
                else:
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
                    # logger.debug(f"[{req_id}] Confirm button visible and overlay hidden. Proceeding to click confirm.")
                except Exception as confirm_wait_err:
                    # Modify error message to be more accurate
                    logger.error(f"[{req_id}] Error waiting for confirm button visibility: {confirm_wait_err}")
                    await save_error_snapshot(f"clear_chat_confirm_wait_error_{req_id}")
                    raise PlaywrightAsyncError(f"Confirm button wait failed: {confirm_wait_err}") from confirm_wait_err

                check_client_disconnected("After Confirm Button/Overlay Wait: ")
                await confirm_button.click(timeout=5000)
                check_client_disconnected("After Confirm Button Click: ")
                logger.info(f"[{req_id}] 清空确认按钮已点击。") # logger

                last_response_container = page.locator(RESPONSE_CONTAINER_SELECTOR).last
                await asyncio.sleep(0.5) # Use asyncio.sleep
                check_client_disconnected("After Clear Post-Delay: ")
                try:
                    # Direct call with timeout
                    await expect_async(last_response_container).to_be_hidden(timeout=CLEAR_CHAT_VERIFY_TIMEOUT_MS - 500)
                    logger.info(f"[{req_id}] ✅ 聊天已成功清空 (验证通过)。") # logger
                except Exception as verify_err:
                    logger.warning(f"[{req_id}] ⚠️ 警告: 清空聊天验证失败: {verify_err}") # logger
        except (PlaywrightAsyncError, asyncio.TimeoutError, ClientDisconnectedError) as clear_err:
            if isinstance(clear_err, ClientDisconnectedError): raise
            logger.error(f"[{req_id}] ❌ 错误: 清空聊天阶段出错: {clear_err}") # logger
            await save_error_snapshot(f"clear_chat_error_{req_id}")
        except Exception as clear_exc:
            logger.exception(f"[{req_id}] ❌ 错误: 清空聊天阶段意外错误") # logger
            await save_error_snapshot(f"clear_chat_unexpected_{req_id}")
        check_client_disconnected("After Clear Chat Logic: ")

        # --- 新增: 调整温度设置 ---
        if request.temperature is not None and page and not page.is_closed():
            logger.info(f"[{req_id}] (Refactored Process) 检查并调整温度设置...")
            requested_temp = request.temperature
            
            # 将温度限制在 [0.0, 2.0] 范围内
            clamped_temp = max(0.0, min(2.0, requested_temp))
            if clamped_temp != requested_temp:
                logger.warning(f"[{req_id}] 请求的温度 {requested_temp} 超出范围 [0, 2]，已调整为 {clamped_temp}")
            
            temp_input_locator = page.locator(TEMPERATURE_INPUT_SELECTOR)
            try:
                await expect_async(temp_input_locator).to_be_visible(timeout=5000)
                check_client_disconnected("温度调整 - 输入框可见后: ")
                
                current_temp_str = await temp_input_locator.input_value(timeout=3000)
                check_client_disconnected("温度调整 - 读取输入框值后: ")
                
                current_temp_float = float(current_temp_str)
                logger.info(f"[{req_id}] 页面当前温度: {current_temp_float}, 请求调整后温度: {clamped_temp}")

                # 使用容差比较浮点数
                if abs(current_temp_float - clamped_temp) > 0.001: 
                    logger.info(f"[{req_id}] 页面温度 ({current_temp_float}) 与请求温度 ({clamped_temp}) 不同，正在更新...")
                    await temp_input_locator.fill(str(clamped_temp), timeout=5000)
                    check_client_disconnected("温度调整 - 填充输入框后: ")
                    
                    # 验证更改 (可选但推荐)
                    await asyncio.sleep(0.1) # 短暂等待值更新
                    new_temp_str = await temp_input_locator.input_value(timeout=3000)
                    new_temp_float = float(new_temp_str)
                    if abs(new_temp_float - clamped_temp) < 0.001:
                        logger.info(f"[{req_id}] ✅ 温度已成功更新为: {new_temp_float}")
                    else:
                        logger.warning(f"[{req_id}] ⚠️ 温度更新后验证失败。页面显示: {new_temp_float}, 期望: {clamped_temp}")
                else:
                    logger.info(f"[{req_id}] 页面温度 ({current_temp_float}) 与请求温度 ({clamped_temp}) 一致或在容差范围内，无需更改。")

            except ValueError as ve:
                logger.error(f"[{req_id}] 转换温度值为浮点数时出错: '{current_temp_str if 'current_temp_str' in locals() else '未知值'}'. 错误: {ve}")
                await save_error_snapshot(f"temperature_value_error_{req_id}")
            except PlaywrightAsyncError as pw_err:
                logger.error(f"[{req_id}] ❌ 操作温度输入框时发生Playwright错误: {pw_err}")
                await save_error_snapshot(f"temperature_playwright_error_{req_id}")
                # 此处可以选择是否将此视为请求的致命错误并抛出HTTPException
            except ClientDisconnectedError:
                logger.info(f"[{req_id}] 客户端在调整温度时断开连接。")
                raise # 重新抛出以确保被外部捕获
            except Exception as e_temp:
                logger.exception(f"[{req_id}] ❌ 调整温度时发生未知错误")
                await save_error_snapshot(f"temperature_unknown_error_{req_id}")
            
            check_client_disconnected("温度调整 - 逻辑完成后: ")
        # --- 结束调整温度设置 ---

        # --- 新增: 调整最大输出Token设置 ---
        if request.max_output_tokens is not None and page and not page.is_closed():
            logger.info(f"[{req_id}] (Refactored Process) 检查并调整最大输出 Token 设置...")
            requested_max_tokens = request.max_output_tokens
            
            min_val_for_tokens = 1
            # 默认上限，如果模型数据中没有提供
            max_val_for_tokens_from_model = 65536 # 一个较高的通用值

            # model_id_to_use 应该在模型切换逻辑后被正确设置
            if model_id_to_use and parsed_model_list:
                current_model_data = next((m for m in parsed_model_list if m.get("id") == model_id_to_use), None)
                if current_model_data and current_model_data.get("supported_max_output_tokens") is not None:
                    # 确保取出的值是有效的数字
                    try:
                        supported_tokens = int(current_model_data["supported_max_output_tokens"])
                        if supported_tokens > 0:
                            max_val_for_tokens_from_model = supported_tokens
                            logger.info(f"[{req_id}] 模型 {model_id_to_use} 支持的最大输出Tokens: {max_val_for_tokens_from_model}")
                        else:
                            logger.warning(f"[{req_id}] 模型 {model_id_to_use} 的 supported_max_output_tokens 值 ({current_model_data['supported_max_output_tokens']}) 无效，将使用默认上限 {max_val_for_tokens_from_model}。")
                    except (ValueError, TypeError):
                        logger.warning(f"[{req_id}] 模型 {model_id_to_use} 的 supported_max_output_tokens 值 ({current_model_data['supported_max_output_tokens']}) 不是有效整数，将使用默认上限 {max_val_for_tokens_from_model}。")
                else:
                    logger.warning(f"[{req_id}] 未在parsed_model_list中找到模型 {model_id_to_use} 的supported_max_output_tokens数据，或值为None，将使用默认上限 {max_val_for_tokens_from_model}。")
            else:
                logger.warning(f"[{req_id}] model_id_to_use ('{model_id_to_use}') 未设置或 parsed_model_list 为空，无法获取模型特定上限，将使用默认上限 {max_val_for_tokens_from_model}。")

            # 使用从模型数据获取的上限（或后备值）进行钳制
            clamped_max_tokens = max(min_val_for_tokens, min(max_val_for_tokens_from_model, requested_max_tokens))
            
            if clamped_max_tokens != requested_max_tokens:
                logger.warning(f"[{req_id}] 请求的最大输出 Tokens {requested_max_tokens} 超出模型范围 [{min_val_for_tokens}, {max_val_for_tokens_from_model}]，已调整为 {clamped_max_tokens}")
            
            max_tokens_input_locator = page.locator(MAX_OUTPUT_TOKENS_SELECTOR)
            try:
                await expect_async(max_tokens_input_locator).to_be_visible(timeout=5000)
                check_client_disconnected("最大输出Token调整 - 输入框可见后: ")
                
                current_max_tokens_str = await max_tokens_input_locator.input_value(timeout=3000)
                check_client_disconnected("最大输出Token调整 - 读取输入框值后: ")
                
                current_max_tokens_int = int(current_max_tokens_str) # 转换为整数
                logger.info(f"[{req_id}] 页面当前最大输出 Tokens: {current_max_tokens_int}, 请求调整后最大输出 Tokens: {clamped_max_tokens}")

                if current_max_tokens_int != clamped_max_tokens: 
                    logger.info(f"[{req_id}] 页面最大输出 Tokens ({current_max_tokens_int}) 与请求最大输出 Tokens ({clamped_max_tokens}) 不同，正在更新...")
                    await max_tokens_input_locator.fill(str(clamped_max_tokens), timeout=5000)
                    check_client_disconnected("最大输出Token调整 - 填充输入框后: ")
                    
                    # 验证更改 (可选但推荐)
                    await asyncio.sleep(0.1) # 短暂等待值更新
                    new_max_tokens_str = await max_tokens_input_locator.input_value(timeout=3000)
                    new_max_tokens_int = int(new_max_tokens_str)
                    if new_max_tokens_int == clamped_max_tokens:
                        logger.info(f"[{req_id}] ✅ 最大输出 Tokens 已成功更新为: {new_max_tokens_int}")
                    else:
                        logger.warning(f"[{req_id}] ⚠️ 最大输出 Tokens 更新后验证失败。页面显示: {new_max_tokens_int}, 期望: {clamped_max_tokens}")
                else:
                    logger.info(f"[{req_id}] 页面最大输出 Tokens ({current_max_tokens_int}) 与请求最大输出 Tokens ({clamped_max_tokens}) 一致，无需更改。")

            except ValueError as ve:
                logger.error(f"[{req_id}] 转换最大输出 Tokens 值为整数时出错: '{current_max_tokens_str if 'current_max_tokens_str' in locals() else '未知值'}'. 错误: {ve}")
                await save_error_snapshot(f"max_tokens_value_error_{req_id}")
            except PlaywrightAsyncError as pw_err:
                logger.error(f"[{req_id}] ❌ 操作最大输出 Tokens 输入框时发生Playwright错误: {pw_err}")
                await save_error_snapshot(f"max_tokens_playwright_error_{req_id}")
            except ClientDisconnectedError:
                logger.info(f"[{req_id}] 客户端在调整最大输出 Tokens 时断开连接。")
                raise 
            except Exception as e_max_tokens:
                logger.exception(f"[{req_id}] ❌ 调整最大输出 Tokens 时发生未知错误")
                await save_error_snapshot(f"max_tokens_unknown_error_{req_id}")
            
            check_client_disconnected("最大输出Token调整 - 逻辑完成后: ")
        # --- 结束调整最大输出Token ---

        # --- 新增: 设置停止序列 ---
        if request.stop is not None and page and not page.is_closed():
            logger.info(f"[{req_id}] (Refactored Process) 检查并设置停止序列...")
            
            stop_sequences = []
            if isinstance(request.stop, str):
                stop_sequences = [request.stop]
            elif isinstance(request.stop, list):
                stop_sequences = [s for s in request.stop if isinstance(s, str) and s.strip()] # 过滤空字符串

            if not stop_sequences and request.stop is not None: # 如果原始 stop 不为 None 但处理后为空列表
                 logger.info(f"[{req_id}] 请求的停止序列为空或只包含无效条目，将尝试清空页面上的停止序列。")
            
            stop_input_locator = page.locator(STOP_SEQUENCE_INPUT_SELECTOR)
            remove_chip_buttons_locator = page.locator(MAT_CHIP_REMOVE_BUTTON_SELECTOR)

            try:
                # 1. 清空已有的停止序列
                # 点击页面上已有的 stop sequence chip 的移除按钮
                # 从最后一个开始移除，避免因 DOM 变化导致定位问题
                logger.info(f"[{req_id}] 尝试清空已有的停止序列...")
                initial_chip_count = await remove_chip_buttons_locator.count()
                logger.debug(f"[{req_id}] 发现 {initial_chip_count} 个可移除的停止序列标签。")
                
                # 增加一个循环计数器和最大移除次数，防止无限循环
                removed_count = 0
                max_removals = initial_chip_count + 5 # 允许一些动态添加/移除的余地
                
                while await remove_chip_buttons_locator.count() > 0 and removed_count < max_removals:
                    check_client_disconnected("停止序列清除 - 循环开始: ")
                    try:
                        # 总是点击第一个找到的移除按钮，因为 DOM 会在点击后更新
                        await remove_chip_buttons_locator.first.click(timeout=2000)
                        removed_count += 1
                        logger.debug(f"[{req_id}] 已移除一个停止序列标签 (已移除 {removed_count} 个)。")
                        await asyncio.sleep(0.15) # 短暂等待UI更新
                    except PlaywrightAsyncError as pe_remove_inner:
                        logger.warning(f"[{req_id}] 移除一个停止序列标签时出错 (可能是最后一个或不可见): {pe_remove_inner}。退出清除循环。")
                        break # 如果点击失败，可能已经没有可见/可交互的移除了
                    except Exception as e_remove_inner:
                        logger.error(f"[{req_id}] 移除停止序列标签时发生意外错误: {e_remove_inner}。退出清除循环。")
                        break
                logger.info(f"[{req_id}] 已有停止序列清空完成 (或尝试清空)。实际移除 {removed_count} 个。")

                check_client_disconnected("停止序列清除 - 完成后: ")

                # 2. 添加新的停止序列
                if stop_sequences: # 只有当提供了有效的停止序列时才添加
                    logger.info(f"[{req_id}] 添加 {len(stop_sequences)} 个新的停止序列: {stop_sequences}")
                    await expect_async(stop_input_locator).to_be_visible(timeout=5000)
                    check_client_disconnected("停止序列添加 - 输入框可见后: ")

                    for seq in stop_sequences:
                        if not seq.strip(): continue # 再次确保不添加空字符串
                        logger.debug(f"[{req_id}] 正在添加停止序列: '{seq}'")
                        await stop_input_locator.fill(seq, timeout=3000)
                        check_client_disconnected(f"停止序列添加 - 输入框填充 '{seq}' 后: ")
                        await stop_input_locator.press("Enter", timeout=3000)
                        check_client_disconnected(f"停止序列添加 - 为 '{seq}' 按下 Enter 后: ")
                        # 等待输入框清空或 chip 出现 (简单延时作为替代)
                        await asyncio.sleep(0.2) 
                        # 验证输入框是否清空
                        current_input_value = await stop_input_locator.input_value(timeout=1000)
                        if current_input_value:
                            logger.warning(f"[{req_id}] 添加停止序列 '{seq}' 后，输入框未完全清空 (值为: '{current_input_value}')。可能未成功添加为 chip。")
                        else:
                            logger.debug(f"[{req_id}] 停止序列 '{seq}' 已通过 Enter 提交。")
                    logger.info(f"[{req_id}] ✅ 新停止序列添加完成。")
                else:
                    logger.info(f"[{req_id}] 没有提供新的有效停止序列来添加。")

            except PlaywrightAsyncError as pw_err:
                logger.error(f"[{req_id}] ❌ 操作停止序列时发生Playwright错误: {pw_err}")
                await save_error_snapshot(f"stop_sequence_playwright_error_{req_id}")
            except ClientDisconnectedError:
                logger.info(f"[{req_id}] 客户端在调整停止序列时断开连接。")
                raise
            except Exception as e_stop_seq:
                logger.exception(f"[{req_id}] ❌ 设置停止序列时发生未知错误")
                await save_error_snapshot(f"stop_sequence_unknown_error_{req_id}")
            
            check_client_disconnected("停止序列调整 - 逻辑完成后: ")
        # --- 结束设置停止序列 ---

        # --- 新增: 调整 Top P 设置 ---
        if request.top_p is not None and page and not page.is_closed():
            logger.info(f"[{req_id}] (Refactored Process) 检查并调整 Top P 设置...")
            requested_top_p = request.top_p
            
            # 根据 HTML 属性进行范围限制: min="0" max="1"
            clamped_top_p = max(0.0, min(1.0, requested_top_p))
            if abs(clamped_top_p - requested_top_p) > 1e-9: # 比较浮点数时使用容差
                logger.warning(f"[{req_id}] 请求的 Top P {requested_top_p} 超出范围 [0, 1]，已调整为 {clamped_top_p}")
            
            top_p_input_locator = page.locator(TOP_P_INPUT_SELECTOR)
            try:
                await expect_async(top_p_input_locator).to_be_visible(timeout=5000)
                check_client_disconnected("Top P 调整 - 输入框可见后: ")
                
                current_top_p_str = await top_p_input_locator.input_value(timeout=3000)
                check_client_disconnected("Top P 调整 - 读取输入框值后: ")
                
                current_top_p_float = float(current_top_p_str)
                logger.info(f"[{req_id}] 页面当前 Top P: {current_top_p_float}, 请求调整后 Top P: {clamped_top_p}")

                # 使用容差比较浮点数
                if abs(current_top_p_float - clamped_top_p) > 1e-9: 
                    logger.info(f"[{req_id}] 页面 Top P ({current_top_p_float}) 与请求 Top P ({clamped_top_p}) 不同，正在更新...")
                    await top_p_input_locator.fill(str(clamped_top_p), timeout=5000)
                    check_client_disconnected("Top P 调整 - 填充输入框后: ")
                    
                    # 验证更改 (可选但推荐)
                    await asyncio.sleep(0.1) # 短暂等待值更新
                    new_top_p_str = await top_p_input_locator.input_value(timeout=3000)
                    new_top_p_float = float(new_top_p_str)
                    if abs(new_top_p_float - clamped_top_p) < 1e-9:
                        logger.info(f"[{req_id}] ✅ Top P 已成功更新为: {new_top_p_float}")
                    else:
                        logger.warning(f"[{req_id}] ⚠️ Top P 更新后验证失败。页面显示: {new_top_p_float}, 期望: {clamped_top_p}")
                else:
                    logger.info(f"[{req_id}] 页面 Top P ({current_top_p_float}) 与请求 Top P ({clamped_top_p}) 一致或在容差范围内，无需更改。")

            except ValueError as ve:
                logger.error(f"[{req_id}] 转换 Top P 值为浮点数时出错: '{current_top_p_str if 'current_top_p_str' in locals() else '未知值'}'. 错误: {ve}")
                await save_error_snapshot(f"top_p_value_error_{req_id}")
            except PlaywrightAsyncError as pw_err:
                logger.error(f"[{req_id}] ❌ 操作 Top P 输入框时发生Playwright错误: {pw_err}")
                await save_error_snapshot(f"top_p_playwright_error_{req_id}")
            except ClientDisconnectedError:
                logger.info(f"[{req_id}] 客户端在调整 Top P 时断开连接。")
                raise
            except Exception as e_top_p:
                logger.exception(f"[{req_id}] ❌ 调整 Top P 时发生未知错误")
                await save_error_snapshot(f"top_p_unknown_error_{req_id}")
            
            check_client_disconnected("Top P 调整 - 逻辑完成后: ")
        # --- 结束调整 Top P ---

        # --- 3. Fill & Submit Prompt --- (Use logger)
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
                logger.info(f"[{req_id}]   - 快捷键提交成功。") # logger
            except Exception as shortcut_err:
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
                logger.info(f"[{req_id}]   - 点击提交成功。") # logger

            if not submitted_successfully:
                 raise PlaywrightAsyncError("Failed to submit prompt via shortcut or click.")

        except (PlaywrightAsyncError, asyncio.TimeoutError, ClientDisconnectedError) as submit_err:
            if isinstance(submit_err, ClientDisconnectedError): raise
            logger.error(f"[{req_id}] ❌ 错误: 填充或提交提示时出错: {submit_err}") # logger
            await save_error_snapshot(f"submit_prompt_error_{req_id}")
            raise HTTPException(status_code=502, detail=f"[{req_id}] Failed to submit prompt to AI Studio: {submit_err}")
        except Exception as submit_exc:
            logger.exception(f"[{req_id}] ❌ 错误: 填充或提交提示时意外错误") # logger
            await save_error_snapshot(f"submit_prompt_unexpected_{req_id}")
            raise HTTPException(status_code=500, detail=f"[{req_id}] Unexpected error during prompt submission: {submit_exc}")
        check_client_disconnected("After Submit Logic: ")

        # --- 4. Locate Response Element --- (Use logger)
        logger.info(f"[{req_id}] (Refactored Process) 定位响应元素...") # logger
        response_container = page.locator(RESPONSE_CONTAINER_SELECTOR).last
        response_element = response_container.locator(RESPONSE_TEXT_SELECTOR)
        try:
            # Direct calls with timeout
            await expect_async(response_container).to_be_attached(timeout=20000)
            check_client_disconnected("After Response Container Attached: ")
            await expect_async(response_element).to_be_attached(timeout=90000)
            logger.info(f"[{req_id}]   - 响应元素已定位。") # logger
        except (PlaywrightAsyncError, asyncio.TimeoutError, ClientDisconnectedError) as locate_err:
            if isinstance(locate_err, ClientDisconnectedError): raise
            logger.error(f"[{req_id}] ❌ 错误: 定位响应元素失败或超时: {locate_err}") # logger
            await save_error_snapshot(f"response_locate_error_{req_id}")
            raise HTTPException(status_code=502, detail=f"[{req_id}] Failed to locate AI Studio response element: {locate_err}")
        except Exception as locate_exc:
            logger.exception(f"[{req_id}] ❌ 错误: 定位响应元素时意外错误") # logger
            await save_error_snapshot(f"response_locate_unexpected_{req_id}")
            raise HTTPException(status_code=500, detail=f"[{req_id}] Unexpected error locating response element: {locate_exc}")
        check_client_disconnected("After Locate Response: ")

        # --- 5. Wait for Completion --- (Uses helper, which was reverted internally)
        logger.info(f"[{req_id}] (Refactored Process) 等待响应生成完成...") # logger
        completion_detected = await _wait_for_response_completion(
            page, req_id, response_element, None, check_client_disconnected, None # Pass None for unused helpers
        )
        if not completion_detected:
            raise HTTPException(status_code=504, detail=f"[{req_id}] AI Studio response generation timed out.")
        check_client_disconnected("After Wait Completion: ")

        # --- 6. Check for Page Errors --- (Use logger)
        logger.info(f"[{req_id}] (Refactored Process) 检查页面错误提示...") # logger
        page_error = await detect_and_extract_page_error(page, req_id)
        if page_error:
            logger.error(f"[{req_id}] ❌ 错误: AI Studio 页面返回错误: {page_error}") # logger
            await save_error_snapshot(f"page_error_detected_{req_id}")
            raise HTTPException(status_code=502, detail=f"[{req_id}] AI Studio Error: {page_error}")
        check_client_disconnected("After Page Error Check: ")

        # --- 7. Get Final Content --- (Uses helpers, which were reverted internally)
        logger.info(f"[{req_id}] (Refactored Process) 获取最终响应内容...") # logger
        final_content = await _get_final_response_content(
            page, req_id, check_client_disconnected # Pass only needed args
        )
        if final_content is None:
            raise HTTPException(status_code=500, detail=f"[{req_id}] Failed to extract final response content from AI Studio.")
        check_client_disconnected("After Get Content: ")

        # --- 8. Format and Return Result --- (Use logger)
        logger.info(f"[{req_id}] (Refactored Process) 格式化并设置结果 (模式: {'流式' if is_streaming else '非流式'})...") # logger
        if is_streaming:
            completion_event = Event() # Create event for streaming

            async def create_stream_generator(event_to_set: Event, content_to_stream: str) -> AsyncGenerator[str, None]:
                """Closure to generate SSE stream from final content."""
                logger.info(f"[{req_id}] (Stream Gen) 开始伪流式输出 ({len(content_to_stream)} chars)...") # logger
                try:
                    char_count = 0
                    total_chars = len(content_to_stream)
                    for i in range(0, total_chars):
                        if client_disconnected_event.is_set():
                            logger.info(f"[{req_id}] (Stream Gen) 断开连接，停止。") # logger
                            break
                        delta = content_to_stream[i]
                        yield generate_sse_chunk(delta, req_id, MODEL_NAME)
                        char_count += 1
                        if char_count % 100 == 0 or char_count == total_chars:
                            if DEBUG_LOGS_ENABLED:
                                pass # Keep the structure, but no log needed here now
                        await asyncio.sleep(PSEUDO_STREAM_DELAY) # Use asyncio.sleep

                    yield generate_sse_stop_chunk(req_id, MODEL_NAME)
                    yield "data: [DONE]\n\n"
                    logger.info(f"[{req_id}] (Stream Gen) ✅ 伪流式响应发送完毕。") # logger
                except asyncio.CancelledError:
                    logger.info(f"[{req_id}] (Stream Gen) 流生成器被取消。") # logger
                except Exception as e:
                    logger.exception(f"[{req_id}] (Stream Gen) ❌ 伪流式生成过程中出错") # logger
                    try: yield generate_sse_error_chunk(f"Stream generation error: {e}", req_id); yield "data: [DONE]\n\n"
                    except: pass
                finally:
                    logger.info(f"[{req_id}] (Stream Gen) 设置完成事件。") # logger
                    if not event_to_set.is_set(): event_to_set.set()

            stream_generator_func = create_stream_generator(completion_event, final_content)
            if not result_future.done():
                result_future.set_result(StreamingResponse(stream_generator_func, media_type="text/event-stream"))
                logger.info(f"[{req_id}] (Refactored Process) 流式响应生成器已设置。") # logger
            else:
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
                logger.info(f"[{req_id}] (Refactored Process) 非流式 JSON 响应已设置。") # logger
            else:
                logger.warning(f"[{req_id}] (Refactored Process) Future 已完成/取消，无法设置 JSON 结果。") # logger
            return None

    # --- Exception Handling --- (Use logger)
    except ClientDisconnectedError as disco_err:
        logger.info(f"[{req_id}] (Refactored Process) 捕获到客户端断开连接信号: {disco_err}") # logger
        if not result_future.done():
             result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] Client disconnected during processing."))
    except HTTPException as http_err:
        logger.warning(f"[{req_id}] (Refactored Process) 捕获到 HTTP 异常: {http_err.status_code} - {http_err.detail}") # logger
        if not result_future.done(): result_future.set_exception(http_err)
    except PlaywrightAsyncError as pw_err:
        logger.error(f"[{req_id}] (Refactored Process) 捕获到 Playwright 错误: {pw_err}") # logger
        await save_error_snapshot(f"process_playwright_error_{req_id}")
        if not result_future.done(): result_future.set_exception(HTTPException(status_code=502, detail=f"[{req_id}] Playwright interaction failed: {pw_err}"))
    except asyncio.TimeoutError as timeout_err:
        logger.error(f"[{req_id}] (Refactored Process) 捕获到操作超时: {timeout_err}") # logger
        await save_error_snapshot(f"process_timeout_error_{req_id}")
        if not result_future.done(): result_future.set_exception(HTTPException(status_code=504, detail=f"[{req_id}] Operation timed out: {timeout_err}"))
    except asyncio.CancelledError:
        logger.info(f"[{req_id}] (Refactored Process) 任务被取消。") # logger
        if not result_future.done(): result_future.cancel("Processing task cancelled")
    except Exception as e:
        logger.exception(f"[{req_id}] (Refactored Process) 捕获到意外错误") # logger
        await save_error_snapshot(f"process_unexpected_error_{req_id}")
        if not result_future.done(): result_future.set_exception(HTTPException(status_code=500, detail=f"[{req_id}] Unexpected server error: {e}"))
    finally:
        # --- Cleanup Disconnect Task --- (Use logger)
        if disconnect_check_task and not disconnect_check_task.done():
            disconnect_check_task.cancel()
            try: await disconnect_check_task
            except asyncio.CancelledError: pass
            except Exception as task_clean_err: logger.error(f"[{req_id}] 清理任务时出错: {task_clean_err}") # logger
        logger.info(f"[{req_id}] (Refactored Process) 处理完成。") # logger
        if is_streaming and completion_event and not completion_event.is_set() and (result_future.done() and result_future.exception() is not None):
             logger.warning(f"[{req_id}] (Refactored Process) 流式请求异常，确保完成事件已设置。") # logger
             completion_event.set()
        return completion_event

# --- Main Chat Endpoint --- (Enqueue request)
@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, http_request: Request):
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    logger.info(f"[{req_id}] 收到 /v1/chat/completions 请求 (Stream={request.stream})")
    logger.debug(f"[{req_id}] 完整请求参数: {request.model_dump_json(indent=2)}")

    launch_mode = os.environ.get('LAUNCH_MODE', 'unknown')
    browser_page_critical = launch_mode != "direct_debug_no_browser"
    
    # 检查核心服务是否就绪
    service_unavailable = is_initializing or \
                          not is_playwright_ready or \
                          (browser_page_critical and (not is_page_ready or not is_browser_connected)) or \
                          not worker_task or worker_task.done()

    if service_unavailable:
        status_code = 503
        # 构建更详细的错误信息
        error_details = []
        if is_initializing: error_details.append("初始化进行中")
        if not is_playwright_ready: error_details.append("Playwright 未就绪")
        if browser_page_critical:
            if not is_browser_connected: error_details.append("浏览器未连接")
            if not is_page_ready: error_details.append("页面未就绪")
        if not worker_task or worker_task.done(): error_details.append("Worker 未运行")
        
        detail = f"[{req_id}] 服务当前不可用 ({', '.join(error_details)}). 请稍后重试."
        logger.error(f"[{req_id}] 服务不可用详情: {detail}")
        raise HTTPException(status_code=status_code, detail=detail, headers={"Retry-After": "30"})

    result_future = Future()
    request_item = {
        "req_id": req_id, "request_data": request, "http_request": http_request,
        "result_future": result_future, "enqueue_time": time.time(), "cancelled": False
    }
    await request_queue.put(request_item)
    logger.info(f"[{req_id}] 请求已加入队列 (当前队列长度: {request_queue.qsize()})")
    try:
        timeout_seconds = RESPONSE_COMPLETION_TIMEOUT / 1000 + 120
        result = await asyncio.wait_for(result_future, timeout=timeout_seconds)
        logger.info(f"[{req_id}] Worker 处理完成，返回结果。")
        return result
    except asyncio.TimeoutError:
        logger.error(f"[{req_id}] ❌ 等待 Worker 响应超时 ({timeout_seconds}s)。")
        raise HTTPException(status_code=504, detail=f"[{req_id}] Request processing timed out waiting for worker response.")
    except asyncio.CancelledError: # 通常由客户端断开连接触发
        logger.info(f"[{req_id}] 请求 Future 被取消 (可能由客户端断开连接触发)。")
        # Worker 内部的 check_disconnect_periodically 应该已经设置了 499 异常
        # 但这里作为后备，如果 Future 被直接取消
        if not result_future.done() or result_future.exception() is None:
             # 如果 future 没有被 worker 设置异常，我们在这里设置一个
             raise HTTPException(status_code=499, detail=f"[{req_id}] Request cancelled by client or server.")
        else: # 如果 future 已经被 worker 设置了异常 (例如 HTTPException)，重新抛出它
             raise result_future.exception()
    except HTTPException as http_err: # 由 worker 明确抛出的 HTTP 异常
        # logger.warning(f"[{req_id}] Worker 抛出 HTTP 异常 {http_err.status_code}，重新抛出。") # Worker 内部已记录
        raise http_err
    except Exception as e: # 其他意外错误
        logger.exception(f"[{req_id}] ❌ 等待 Worker 响应时发生意外错误")
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
    # (代码不变)
    logger.info(f"[{req_id}] 收到取消请求。")
    cancelled = await cancel_queued_request(req_id)
    if cancelled:
        return JSONResponse(content={"success": True, "message": f"Request {req_id} marked as cancelled in queue."})
    else:
        return JSONResponse(
            content={"success": False, "message": f"Request {req_id} not found in queue (it might be processing or already finished)."},
            status_code=404
        )

@app.get("/v1/queue")
async def get_queue_status():
    # (代码不变)
    queue_items = []
    items_to_requeue = []
    try:
        while True:
            item = request_queue.get_nowait()
            items_to_requeue.append(item)
            req_id = item.get("req_id", "unknown")
            timestamp = item.get("enqueue_time", 0)
            is_streaming = item.get("request_data").stream if hasattr(item.get("request_data", {}), "stream") else False
            cancelled = item.get("cancelled", False)
            queue_items.append({
                "req_id": req_id, "enqueue_time": timestamp,
                "wait_time_seconds": round(time.time() - timestamp, 2) if timestamp else None,
                "is_streaming": is_streaming, "cancelled": cancelled
            })
    except asyncio.QueueEmpty:
        pass
    finally:
        for item in items_to_requeue:
            await request_queue.put(item)
    return JSONResponse(content={
        "queue_length": len(queue_items),
        "is_processing_locked": processing_lock.locked(),
        "items": sorted(queue_items, key=lambda x: x.get("enqueue_time", 0))
    })

@app.websocket("/ws/logs")
async def websocket_log_endpoint(websocket: WebSocket):
    if not log_ws_manager:
        try:
            await websocket.accept()
            await websocket.send_text(json.dumps({
                "type": "error", "status": "disconnected",
                "message": "日志服务内部错误 (管理器未初始化)。",
                "timestamp": datetime.datetime.now().isoformat()}))
            await websocket.close(code=1011)
        except Exception: pass
        return

    client_id = str(uuid.uuid4())
    try:
        await log_ws_manager.connect(client_id, websocket)
        while True:
            data = await websocket.receive_text()
            if data.lower() == "ping":
                 await websocket.send_text(json.dumps({"type": "pong", "timestamp": datetime.datetime.now().isoformat()}))
    except WebSocketDisconnect:
        # logger.info(f"日志客户端 {client_id} 已断开。") # disconnect 方法会记录
        pass # disconnect 方法会处理日志记录
    except Exception as e:
        logger.error(f"日志 WebSocket (客户端 {client_id}) 发生异常: {e}", exc_info=True)
    finally:
        if log_ws_manager: # 确保 manager 仍然存在
            log_ws_manager.disconnect(client_id)

# --- 移除独立的 __main__ Uvicorn 启动逻辑 ---
if __name__ == "__main__":
    print("错误: server.py 不应直接作为主脚本运行。", file=sys.stderr)
    print("请使用 launch_camoufox.py (用于调试) 或 start.py (用于后台服务) 来启动。", file=sys.stderr)
    print("\n如果确实需要直接运行 server.py 进行底层测试 (不推荐):", file=sys.stderr)
    print("  1. 确保已设置必要的环境变量，如 CAMOUFOX_WS_ENDPOINT, LAUNCH_MODE, SERVER_REDIRECT_PRINT, SERVER_LOG_LEVEL。", file=sys.stderr)
    print("  2. 然后可以尝试: python -m uvicorn server:app --host 0.0.0.0 --port <端口号>", file=sys.stderr)
    print("     例如: LAUNCH_MODE=direct_debug_no_browser SERVER_REDIRECT_PRINT=false python -m uvicorn server:app --port 8000", file=sys.stderr)
    sys.exit(1)

# --- 添加模型切换的辅助函数 ---
async def switch_ai_studio_model(page: AsyncPage, model_id: str, req_id: str) -> bool:
    """
    在AI Studio页面上切换模型。
    
    Args:
        page: Playwright页面对象
        model_id: 目标模型ID (例如 "gemini-1.5-pro")
        req_id: 请求ID，用于日志记录
        
    Returns:
        bool: 切换是否成功
    """
    logger.info(f"[{req_id}] 开始切换模型到: {model_id}")
    
    original_prefs_str: Optional[str] = None
    original_prompt_model: Optional[str] = None
    # 定义固定的新聊天 URL
    new_chat_url = f"https://{AI_STUDIO_URL_PATTERN}prompts/new_chat"

    try:
        # 1. 读取并备份当前设置
        original_prefs_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
        if original_prefs_str:
            try:
                original_prefs_obj = json.loads(original_prefs_str)
                original_prompt_model = original_prefs_obj.get("promptModel")
                logger.info(f"[{req_id}] 切换前 localStorage.promptModel 为: {original_prompt_model or '未设置'}")
            except json.JSONDecodeError:
                logger.warning(f"[{req_id}] 无法解析原始的 aiStudioUserPreference JSON 字符串。")
                original_prefs_str = None # 视为无效

        current_prefs_for_modification = json.loads(original_prefs_str) if original_prefs_str else {}
        
        # 2. 修改模型设置
        full_model_path = f"models/{model_id}"
        if current_prefs_for_modification.get("promptModel") == full_model_path:
            logger.info(f"[{req_id}] 模型已经设置为 {model_id} (localStorage 中已是目标值)，无需切换")
            # 即使无需切换localStorage，也确保导航到new_chat页面，以统一行为
            if page.url != new_chat_url:
                 logger.info(f"[{req_id}] 当前 URL 不是 new_chat ({page.url})，导航到 {new_chat_url}")
                 await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=30000)
                 await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=30000)
            return True
            
        logger.info(f"[{req_id}] 从 {current_prefs_for_modification.get('promptModel', '未知')} 更新 localStorage.promptModel 为 {full_model_path}")
        current_prefs_for_modification["promptModel"] = full_model_path
        
        # 3. 保存修改后的设置到 localStorage
        await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", json.dumps(current_prefs_for_modification))
        
        # 4. 导航到新聊天页面应用新设置
        logger.info(f"[{req_id}] localStorage 已更新，导航到 '{new_chat_url}' 应用新模型...")
        await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=30000)
        
        # 5. 等待页面重新加载完成 (核心元素可见)
        input_field = page.locator(INPUT_SELECTOR)
        await expect_async(input_field).to_be_visible(timeout=30000)
        logger.info(f"[{req_id}] 页面已导航到新聊天并加载完成，输入框可见")
        
        # 6. 验证 AI Studio 是否接受了模型更改 (检查 localStorage 的最终状态)
        final_prefs_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
        final_prompt_model_in_storage: Optional[str] = None
        if final_prefs_str:
            try:
                final_prefs_obj = json.loads(final_prefs_str)
                final_prompt_model_in_storage = final_prefs_obj.get("promptModel")
            except json.JSONDecodeError:
                logger.warning(f"[{req_id}] 无法解析刷新后的 aiStudioUserPreference JSON 字符串。")

        if final_prompt_model_in_storage == full_model_path:
            logger.info(f"[{req_id}] ✅ AI Studio localStorage 中模型已成功设置为: {full_model_path}")
            
            page_display_match = False
            expected_display_name_for_target_id = None
            actual_displayed_model_name_on_page = "无法读取" # Default value

            if parsed_model_list:
                for m_obj in parsed_model_list:
                    if m_obj.get("id") == model_id:
                        expected_display_name_for_target_id = m_obj.get("display_name")
                        break
            
            if not expected_display_name_for_target_id:
                logger.warning(f"[{req_id}] 无法在parsed_model_list中找到目标ID '{model_id}' 的显示名称，跳过页面显示名称验证。这可能不准确。")
                page_display_match = True 
            else:
                try:
                    model_wrapper_locator = page.locator('#mat-select-value-0 mat-select-trigger').first
                    actual_displayed_model_name_on_page_raw = await model_wrapper_locator.inner_text(timeout=5000)
                    actual_displayed_model_name_on_page = actual_displayed_model_name_on_page_raw.strip()

                    normalized_actual_display = actual_displayed_model_name_on_page.lower()
                    normalized_expected_display = expected_display_name_for_target_id.strip().lower()
                    
                    if normalized_actual_display == normalized_expected_display:
                        page_display_match = True
                        logger.info(f"[{req_id}] ✅ 页面显示模型 ('{actual_displayed_model_name_on_page}') 与期望 ('{expected_display_name_for_target_id}') 一致。")
                    else:
                        logger.error(f"[{req_id}] ❌ 页面显示模型 ('{actual_displayed_model_name_on_page}') 与期望 ('{expected_display_name_for_target_id}') 不一致。(Raw page: '{actual_displayed_model_name_on_page_raw}')")
                except Exception as e_disp:
                    logger.warning(f"[{req_id}] 读取页面显示的当前模型名称时出错: {e_disp}。将无法验证页面显示。")
            
            if page_display_match:
                return True
            else:
                logger.error(f"[{req_id}] ❌ 模型切换失败，因为页面显示的模型与期望不符 (即使localStorage可能已更改)。")
        else:
            logger.error(f"[{req_id}] ❌ AI Studio 未接受模型更改 (localStorage)。期望='{full_model_path}', 实际='{final_prompt_model_in_storage or '未设置或无效'}'.")

        logger.info(f"[{req_id}] 模型切换失败。尝试恢复到页面当前实际显示的模型的状态...")

        current_displayed_name_for_revert_raw = "无法读取"
        current_displayed_name_for_revert_stripped = "无法读取"
        try:
            model_wrapper_locator_for_revert = page.locator('#mat-select-value-0 mat-select-trigger').first
            current_displayed_name_for_revert_raw = await model_wrapper_locator_for_revert.inner_text(timeout=5000)
            current_displayed_name_for_revert_stripped = current_displayed_name_for_revert_raw.strip()
            logger.info(f"[{req_id}] 恢复：页面当前显示的模型名称 (原始: '{current_displayed_name_for_revert_raw}', 清理后: '{current_displayed_name_for_revert_stripped}')")
        except Exception as e_read_disp_revert:
            logger.warning(f"[{req_id}] 恢复：读取页面当前显示模型名称失败: {e_read_disp_revert}。将尝试回退到原始localStorage。")
            if original_prefs_str:
                logger.info(f"[{req_id}] 恢复：由于无法读取当前页面显示，尝试将 localStorage 恢复到原始状态: '{original_prompt_model or '未设置'}'")
                await page.evaluate("(origPrefs) => localStorage.setItem('aiStudioUserPreference', origPrefs)", original_prefs_str)
                logger.info(f"[{req_id}] 恢复：导航到 '{new_chat_url}' 以应用恢复的原始 localStorage 设置...")
                await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=20000)
                await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=20000)
                logger.info(f"[{req_id}] 恢复：页面已导航到新聊天并加载，已尝试应用原始 localStorage。")
            else:
                logger.warning(f"[{req_id}] 恢复：无有效的原始 localStorage 状态可恢复，也无法读取当前页面显示。")
            return False

        model_id_to_revert_to = None
        if parsed_model_list and current_displayed_name_for_revert_stripped != "无法读取":
            normalized_current_display_for_revert = current_displayed_name_for_revert_stripped.lower()
            for m_obj in parsed_model_list:
                parsed_list_display_name = m_obj.get("display_name", "").strip().lower()
                if parsed_list_display_name == normalized_current_display_for_revert:
                    model_id_to_revert_to = m_obj.get("id")
                    logger.info(f"[{req_id}] 恢复：页面显示名称 '{current_displayed_name_for_revert_stripped}' 对应模型ID: {model_id_to_revert_to}")
                    break
            if not model_id_to_revert_to:
                logger.warning(f"[{req_id}] 恢复：无法在 parsed_model_list 中找到与页面显示名称 '{current_displayed_name_for_revert_stripped}' 匹配的模型ID。")
        else:
            if current_displayed_name_for_revert_stripped == "无法读取":
                 logger.warning(f"[{req_id}] 恢复：因无法读取页面显示名称，故不能从 parsed_model_list 转换ID。")
            else:
                 logger.warning(f"[{req_id}] 恢复：parsed_model_list 为空，无法从显示名称 '{current_displayed_name_for_revert_stripped}' 转换模型ID。")

        if model_id_to_revert_to:
            base_prefs_for_final_revert = {}
            try:
                current_ls_content_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
                if current_ls_content_str:
                    base_prefs_for_final_revert = json.loads(current_ls_content_str)
                elif original_prefs_str:
                    base_prefs_for_final_revert = json.loads(original_prefs_str)
            except json.JSONDecodeError:
                logger.warning(f"[{req_id}] 恢复：解析现有 localStorage 以构建恢复偏好失败。")
            
            path_to_revert_to = f"models/{model_id_to_revert_to}"
            base_prefs_for_final_revert["promptModel"] = path_to_revert_to
            
            logger.info(f"[{req_id}] 恢复：准备将 localStorage.promptModel 设置回页面实际显示的模型的路径: '{path_to_revert_to}'")
            await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", json.dumps(base_prefs_for_final_revert))
            
            logger.info(f"[{req_id}] 恢复：导航到 '{new_chat_url}' 以应用恢复到 '{model_id_to_revert_to}' 的 localStorage 设置...")
            await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=30000)
            await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=30000)
            logger.info(f"[{req_id}] 恢复：页面已导航到新聊天并加载。localStorage 应已设置为反映模型 '{model_id_to_revert_to}'。")
        else:
            logger.error(f"[{req_id}] 恢复：无法将模型恢复到页面显示的状态，因为未能从显示名称 '{current_displayed_name_for_revert_stripped}' 确定有效模型ID。")
            if original_prefs_str:
                logger.warning(f"[{req_id}] 恢复：作为最终后备，尝试恢复到原始 localStorage: '{original_prompt_model or '未设置'}'")
                await page.evaluate("(origPrefs) => localStorage.setItem('aiStudioUserPreference', origPrefs)", original_prefs_str)
                logger.info(f"[{req_id}] 恢复：导航到 '{new_chat_url}' 以应用最终后备的原始 localStorage。")
                await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=20000)
                await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=20000)
                logger.info(f"[{req_id}] 恢复：页面已导航到新聊天并加载，已应用最终后备的原始 localStorage。")
            else:
                logger.warning(f"[{req_id}] 恢复：无有效的原始 localStorage 状态可作为最终后备。")
                
        return False
            
    except Exception as e:
        logger.exception(f"[{req_id}] ❌ 切换模型过程中发生严重错误")
        await save_error_snapshot(f"model_switch_error_{req_id}")
        try:
            if original_prefs_str:
                logger.info(f"[{req_id}] 发生异常，尝试恢复 localStorage 至: {original_prompt_model or '未设置'}")
                await page.evaluate("(origPrefs) => localStorage.setItem('aiStudioUserPreference', origPrefs)", original_prefs_str)
                logger.info(f"[{req_id}] 异常恢复：导航到 '{new_chat_url}' 以应用恢复的 localStorage。")
                await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=15000)
                await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=15000)
        except Exception as recovery_err:
            logger.error(f"[{req_id}] 异常后恢复 localStorage 失败: {recovery_err}")
        return False

# 新增: 加载排除模型列表的函数
def load_excluded_models(filename: str):
    """从指定文件加载模型ID到排除列表。"""
    global excluded_model_ids, logger
    excluded_file_path = os.path.join(os.path.dirname(__file__), filename)
    try:
        if os.path.exists(excluded_file_path):
            with open(excluded_file_path, 'r', encoding='utf-8') as f:
                loaded_ids = {line.strip() for line in f if line.strip()}
            if loaded_ids:
                excluded_model_ids.update(loaded_ids)
                logger.info(f"✅ 从 '{filename}' 加载了 {len(loaded_ids)} 个模型到排除列表: {excluded_model_ids}")
            else:
                logger.info(f"'{filename}' 文件为空或不包含有效的模型 ID，排除列表未更改。")
        else:
            logger.info(f"模型排除列表文件 '{filename}' 未找到，排除列表为空。")
    except Exception as e:
        logger.error(f"❌ 从 '{filename}' 加载排除模型列表时出错: {e}", exc_info=True)

# 新增: 处理初始模型状态和 localStorage 的函数
async def _handle_initial_model_state_and_storage(page: AsyncPage):
    """检查初始 localStorage，如果为空或 isAdvancedOpen 不为 true，则尝试根据页面显示设置，
       强制 isAdvancedOpen=true, 并重新加载页面，然后再次同步全局模型状态。
    """
    global current_ai_studio_model_id, logger, parsed_model_list, model_list_fetch_event, INPUT_SELECTOR # 确保 INPUT_SELECTOR 在此作用域可用
    
    logger.info("--- (新) 处理初始模型状态, localStorage 和 isAdvancedOpen ---")
    needs_reload_and_storage_update = False
    reason_for_reload = ""

    try:
        initial_prefs_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
        
        if not initial_prefs_str:
            needs_reload_and_storage_update = True
            reason_for_reload = "localStorage.aiStudioUserPreference 未找到。"
            logger.info(f"   判定需要刷新和存储更新: {reason_for_reload}")
        else:
            logger.info("   localStorage 中找到 'aiStudioUserPreference'。正在解析...")
            try:
                pref_obj = json.loads(initial_prefs_str)
                prompt_model_path = pref_obj.get("promptModel")
                is_advanced_open_in_storage = pref_obj.get("isAdvancedOpen")

                # 检查 promptModel 是否有效 (非空字符串)
                is_prompt_model_valid = isinstance(prompt_model_path, str) and prompt_model_path.strip()

                if not is_prompt_model_valid:
                    needs_reload_and_storage_update = True
                    reason_for_reload = "localStorage.promptModel 无效或未设置。"
                    logger.info(f"   判定需要刷新和存储更新: {reason_for_reload}")
                elif is_advanced_open_in_storage is not True: # 严格检查布尔值 True
                    needs_reload_and_storage_update = True
                    reason_for_reload = f"localStorage.isAdvancedOpen ({is_advanced_open_in_storage}) 不为 True。"
                    logger.info(f"   判定需要刷新和存储更新: {reason_for_reload}")
                else:
                    # localStorage 有效, isAdvancedOpen 为 true, promptModel 有效
                    current_ai_studio_model_id = prompt_model_path.split('/')[-1]
                    logger.info(f"   ✅ localStorage 有效且 isAdvancedOpen=true。初始模型 ID 从 localStorage 设置为: {current_ai_studio_model_id}")
            except json.JSONDecodeError:
                needs_reload_and_storage_update = True
                reason_for_reload = "解析 localStorage.aiStudioUserPreference JSON 失败。"
                logger.error(f"   判定需要刷新和存储更新: {reason_for_reload}")

        if needs_reload_and_storage_update:
            logger.info(f"   执行刷新和存储更新流程，原因: {reason_for_reload}")
            
            logger.info("   步骤 1: 调用 _set_model_from_page_display(set_storage=True) 更新 localStorage 和全局模型 ID...")
            await _set_model_from_page_display(page, set_storage=True)
            
            current_page_url = page.url
            logger.info(f"   步骤 2: 重新加载页面 ({current_page_url}) 以应用 isAdvancedOpen=true...")
            try:
                await page.goto(current_page_url, wait_until="domcontentloaded", timeout=30000)
                await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=30000) # 确保页面可用
                logger.info(f"   ✅ 页面已成功重新加载到: {page.url}")
            except Exception as reload_err:
                logger.error(f"   ❌ 页面重新加载失败: {reload_err}. 后续模型状态可能不准确。", exc_info=True)
                await save_error_snapshot("initial_storage_reload_fail")
                # 如果重新加载失败，后续的 _set_model_from_page_display 仍然会尝试运行，但可能基于一个未完全加载的页面

            logger.info("   步骤 3: 重新加载后，再次调用 _set_model_from_page_display(set_storage=False) 以同步全局模型 ID...")
            await _set_model_from_page_display(page, set_storage=False)
            
            logger.info(f"   ✅ 刷新和存储更新流程完成。最终全局模型 ID: {current_ai_studio_model_id}")
        else:
            logger.info("   localStorage 状态良好 (isAdvancedOpen=true, promptModel有效)，无需刷新页面。")

    except Exception as e:
        logger.error(f"❌ (新) 处理初始模型状态和 localStorage 时发生严重错误: {e}", exc_info=True)
        try:
            logger.warning("   由于发生错误，尝试回退仅从页面显示设置全局模型 ID (不写入localStorage)...")
            await _set_model_from_page_display(page, set_storage=False)
        except Exception as fallback_err:
            logger.error(f"   回退设置模型ID也失败: {fallback_err}")

async def _set_model_from_page_display(page: AsyncPage, set_storage: bool = False):
    """尝试从页面显示的元素读取模型，更新全局状态，并可选地设置 localStorage。"""
    global current_ai_studio_model_id, logger, parsed_model_list, model_list_fetch_event
    try:
        logger.info("   尝试从页面显示元素读取当前模型名称...")
        model_wrapper_locator = page.locator('#mat-select-value-0 mat-select-trigger').first
        displayed_model_name_from_page_raw = await model_wrapper_locator.inner_text(timeout=7000)
        
        # 新增：去除首尾空格
        displayed_model_name = displayed_model_name_from_page_raw.strip()

        logger.info(f"   页面当前显示模型名称 (原始: '{displayed_model_name_from_page_raw}', 清理后: '{displayed_model_name}')")

        # 尝试将显示名称转换为模型ID
        found_model_id_from_display = None
        if not model_list_fetch_event.is_set():
            logger.info("   等待模型列表数据 (最多5秒) 以便转换显示名称...")
            try: await asyncio.wait_for(model_list_fetch_event.wait(), timeout=5.0)
            except asyncio.TimeoutError: logger.warning("   等待模型列表超时，可能无法准确转换显示名称为ID。")

        if parsed_model_list:
            for model_obj in parsed_model_list:
                # 确保比较时双方都是清理过的，或假设 parsed_model_list 中的 display_name 是干净的
                if model_obj.get("display_name") and model_obj.get("display_name").strip() == displayed_model_name:
                    found_model_id_from_display = model_obj.get("id")
                    logger.info(f"   显示名称 '{displayed_model_name}' 对应模型 ID: {found_model_id_from_display}")
                    break
            if not found_model_id_from_display:
                 logger.warning(f"   未在已知模型列表中找到与显示名称 '{displayed_model_name}' 匹配的 ID。")
        else:
            logger.warning("   模型列表尚不可用，无法将显示名称转换为ID。")

        # 更新全局 current_ai_studio_model_id
        # 优先使用转换得到的 ID，其次是原始显示名称 (如果转换失败)
        new_model_value = found_model_id_from_display if found_model_id_from_display else displayed_model_name
        if current_ai_studio_model_id != new_model_value:
            current_ai_studio_model_id = new_model_value
            logger.info(f"   全局 current_ai_studio_model_id 已更新为: {current_ai_studio_model_id}")
        else:
            logger.info(f"   全局 current_ai_studio_model_id ('{current_ai_studio_model_id}') 与从页面获取的值一致，未更改。")

        if set_storage:
            logger.info(f"   准备为页面状态设置 localStorage (确保 isAdvancedOpen=true)...")
            
            # 尝试获取现有偏好以进行更新，或从新的空字典开始
            existing_prefs_for_update_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
            prefs_to_set = {}
            if existing_prefs_for_update_str:
                try:
                    prefs_to_set = json.loads(existing_prefs_for_update_str)
                except json.JSONDecodeError:
                    logger.warning("   解析现有 localStorage.aiStudioUserPreference 失败，将创建新的偏好设置。")
            
            # 强制 isAdvancedOpen 为 true
            prefs_to_set["isAdvancedOpen"] = True
            logger.info(f"     强制 isAdvancedOpen: true")
            # 新增：强制 areToolsOpen 为 false
            prefs_to_set["areToolsOpen"] = False
            logger.info(f"     强制 areToolsOpen: false")

            if found_model_id_from_display:
                new_prompt_model_path = f"models/{found_model_id_from_display}"
                prefs_to_set["promptModel"] = new_prompt_model_path
                logger.info(f"     设置 promptModel 为: {new_prompt_model_path} (基于找到的ID)")
            elif "promptModel" not in prefs_to_set: 
                # 如果无法从页面显示中找到模型ID，并且localStorage中也没有现有的promptModel，
                # 则不主动设置promptModel，以避免写入AI Studio无法识别的值。
                # 主要目标是修复isAdvancedOpen。
                logger.warning(f"     无法从页面显示 '{displayed_model_name}' 找到模型ID，且 localStorage 中无现有 promptModel。promptModel 将不会被主动设置以避免潜在问题。")

            # 确保其他必要的默认键存在，以防 prefs_to_set 是一个空对象或不完整的对象
            default_keys_if_missing = {
                "bidiModel": "models/gemini-1.0-pro-001", # 一个合理的默认值
                "isSafetySettingsOpen": False, 
                # "areToolsOpen": True, # 现在会在上面显式设置为 False
                "hasShownSearchGroundingTos": False, 
                "autosaveEnabled": True, 
                "theme": "system",
                "bidiOutputFormat": 3, 
                "isSystemInstructionsOpen": False, 
                "warmWelcomeDisplayed": True,
                "getCodeLanguage": "Node.js", 
                "getCodeHistoryToggle": False, 
                "fileCopyrightAcknowledged": True
            }
            for key, val_default in default_keys_if_missing.items():
                if key not in prefs_to_set:
                    prefs_to_set[key] = val_default
            
            await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", json.dumps(prefs_to_set))
            logger.info(f"   ✅ localStorage.aiStudioUserPreference 已更新。isAdvancedOpen: {prefs_to_set.get('isAdvancedOpen')}, areToolsOpen: {prefs_to_set.get('areToolsOpen')}, promptModel: '{prefs_to_set.get('promptModel', '未设置/保留原样')}'。")
        # 原本的 elif set_storage and not found_model_id_from_display 分支已被合并到上面的 if set_storage 逻辑中

    except Exception as e_set_disp:
        logger.error(f"   尝试从页面显示设置模型时出错: {e_set_disp}", exc_info=True)
