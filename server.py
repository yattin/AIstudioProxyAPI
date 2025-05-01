# server.py
import asyncio
import random
import time
import json # Added for potential JSON operations
from typing import List, Optional, Dict, Any, Union, AsyncGenerator
import os
import traceback # Keep traceback import
from contextlib import asynccontextmanager # Import asynccontextmanager
import sys # Import sys for exiting
import platform # To check OS type
# Removed argparse import
# import argparse

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from pydantic import BaseModel, Field
# Assuming camoufox is installed and provides sync/async APIs
# Adjust the import based on actual library structure if needed
from camoufox.sync_api import Camoufox as CamoufoxSync
# Import the async module directly
import camoufox.async_api
from playwright.sync_api import Page as SyncPage, Browser as SyncBrowser, Playwright as SyncPlaywright, Error as PlaywrightSyncError, expect as expect_sync # Added expect
from playwright.async_api import Page as AsyncPage, Browser as AsyncBrowser, Playwright as AsyncPlaywright, Error as PlaywrightAsyncError, expect as expect_async, BrowserContext as AsyncBrowserContext # Added expect, BrowserContext
from playwright.async_api import async_playwright # Import standard async_playwright

# --- ANSI Colors Removed ---
# ...

# --- Configuration (Mirrored from server.cjs, adjust as needed) ---
# SERVER_PORT = 2048 # Port will be handled by uvicorn when running
AI_STUDIO_URL_PATTERN = 'aistudio.google.com/'
RESPONSE_COMPLETION_TIMEOUT = 300000 # 5 minutes total timeout (in ms)
POLLING_INTERVAL = 300 # ms - Standard polling interval
POLLING_INTERVAL_STREAM = 200 # ms - Stream-specific polling interval
SILENCE_TIMEOUT_MS = 1500 # ms
# v2.12: Timeout for secondary checks *after* spinner disappears
POST_SPINNER_CHECK_DELAY_MS = 500 # Spinner消失后稍作等待再检查其他状态
FINAL_STATE_CHECK_TIMEOUT_MS = 1500 # 检查按钮和输入框最终状态的超时
SPINNER_CHECK_TIMEOUT_MS = 1000 # 检查Spinner状态的超时
POST_COMPLETION_BUFFER = 1000 # JSON模式下可以缩短检查后等待时间
# !! 新增：清空验证相关常量 !! (Mirrored)
CLEAR_CHAT_VERIFY_TIMEOUT_MS = 5000 # 等待清空生效的总超时时间 (ms)
CLEAR_CHAT_VERIFY_INTERVAL_MS = 300 # 检查清空状态的轮询间隔 (ms)

# --- Configuration ---
STORAGE_STATE_PATH = os.path.join(os.path.dirname(__file__), "auth_state.json") # Path to save/load auth state
# Remove USER_DATA_DIR and related path logic as persistence doesn't work
# USER_DATA_DIR = os.path.join(os.path.dirname(__file__), "camoufox_profile")
# CAMOUFOX_CACHE_DIR = "/Users/aq/Library/Caches/camoufox"
# CAMOUFOX_EXECUTABLE_PATH = os.path.join(CAMOUFOX_CACHE_DIR, "Camoufox.app", "Contents", "MacOS", "Camoufox")

# --- Constants (Mirrored from server.cjs) ---
MODEL_NAME = 'google-ai-studio-via-camoufox-fastapi' # Updated model name
CHAT_COMPLETION_ID_PREFIX = 'chatcmpl-'

# --- Selectors (Mirrored from server.cjs, verify if still valid in Firefox/Camoufox) ---
INPUT_SELECTOR = 'ms-prompt-input-wrapper textarea'
SUBMIT_BUTTON_SELECTOR = 'button[aria-label="Run"]'
RESPONSE_CONTAINER_SELECTOR = 'ms-chat-turn .chat-turn-container.model'
RESPONSE_TEXT_SELECTOR = 'ms-cmark-node.cmark-node'
LOADING_SPINNER_SELECTOR = 'button[aria-label="Run"] svg .stoppable-spinner'
ERROR_TOAST_SELECTOR = 'div.toast.warning, div.toast.error'
# !! 新增：清空聊天记录相关选择器 !! (Mirrored)
CLEAR_CHAT_BUTTON_SELECTOR = 'button[aria-label="Clear chat"][data-test-clear="outside"]:has(span.material-symbols-outlined:has-text("refresh"))'
CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR = 'button.mdc-button:has-text("Continue")'

# --- Global State (Modified) ---
playwright_manager: Optional[AsyncPlaywright] = None # To manage playwright itself
browser_instance: Optional[AsyncBrowser] = None # Store the browser instance connected via WebSocket
context_instance: Optional[AsyncBrowserContext] = None # Context is temporary within init
page_instance: Optional[AsyncPage] = None
is_playwright_ready = False # Renamed from is_camoufox_ready
is_browser_connected = False
is_page_ready = False
is_initializing = False
# Removed cli_args global variable
# cli_args = None
# TODO: Implement request queue and processing state if needed (using asyncio.Queue for async)


# --- Pydantic Models for API validation ---
class MessageContentItem(BaseModel):
    type: str
    text: Optional[str] = None
    # Add image_url field if needed for vision models later
    # image_url: Optional[ImageUrl] = None

class Message(BaseModel):
    role: str
    content: Union[str, List[MessageContentItem]] # Handle text and OpenAI vision format

class ChatCompletionRequest(BaseModel):
    messages: List[Message]
    model: Optional[str] = MODEL_NAME # Optional, but helps if client sends it
    stream: Optional[bool] = False
    # Add other potential OpenAI compatible fields if needed (temperature, etc.)


# --- Helper Functions (Ported/Adapted from server.cjs) ---

def prepare_ai_studio_prompt(user_prompt: str, system_prompt: Optional[str] = None) -> str:
    # (Ported from server.cjs prepareAIStudioPrompt)
    # Start with the base instruction as a normal string
    base_instruction = """
IMPORTANT: Your entire response MUST be a single JSON object. Do not include any text outside of this JSON object.
The JSON object must have a single key named "response".
Inside the value of the "response" key (which is a string), you MUST put the exact marker "<<<START_RESPONSE>>>"" at the very beginning of your actual answer. There should be NO text before this marker within the response string.
"""

    system_instruction = ""
    if system_prompt and system_prompt.strip():
        # Use f-string formatting safely here
        system_instruction = f"System Instruction: {system_prompt}\n"

    # Use a regular multiline string for the examples and final prompt
    # Use single quotes for the outer triple quotes to avoid conflict with internal double quotes
    # Simplify escaping inside the python code example
    prompt_template = '''
Example 1:
User asks: "What is the capital of France?"
Your response MUST be:
{
  "response": "<<<START_RESPONSE>>>The capital of France is Paris."
}

Example 2:
User asks: "Write a python function to add two numbers"
Your response MUST be:
{
  "response": "<<<START_RESPONSE>>>```python\ndef add(a, b):\n  return a + b\n```"
}

Now, answer the following user prompt, ensuring your output strictly adheres to the JSON format AND the start marker requirement described above:

User Prompt: "{user_prompt_placeholder}"

Your JSON Response:
'''

    # Combine the parts and replace the placeholder
    full_prompt = base_instruction
    if system_instruction:
        full_prompt += "\n" + system_instruction # Add newline before system instruction
    full_prompt += prompt_template.replace("{user_prompt_placeholder}", user_prompt)

    return full_prompt

def prepare_ai_studio_prompt_stream(user_prompt: str, system_prompt: Optional[str] = None) -> str:
    # (Ported from server.cjs prepareAIStudioPromptStream)
    # vNEXT: Use Markdown Code Block for streaming
    base_instruction = """
IMPORTANT: For this streaming request, your entire response MUST be enclosed in a single markdown code block (like ``` block ```).
Inside this code block, your actual answer text MUST start immediately after the exact marker "<<<START_RESPONSE>>>".
Start your response exactly with "```\n<<<START_RESPONSE>>>" followed by your answer content.
Continue outputting your answer content. You SHOULD include the final closing "```" at the very end of your full response stream.
"""

    system_instruction = ""
    if system_prompt and system_prompt.strip():
        system_instruction = f"System Instruction: {system_prompt}\n"

    # Use a regular multiline string for the examples and final prompt
    # Use single quotes for the outer triple quotes
    prompt_template = '''
Example 1 (Streaming):
User asks: "What is the capital of France?"
Your streamed response MUST look like this over time:
Stream part 1: ```\n<<<START_RESPONSE>>>The capital
Stream part 2:  of France is
Stream part 3:  Paris.\n```

Example 2 (Streaming):
User asks: "Write a python function to add two numbers"
Your streamed response MUST look like this over time:
Stream part 1: ```\n<<<START_RESPONSE>>>```python\ndef add(a, b):
Stream part 2: \n  return a + b\n
Stream part 3: ```\n```

Now, answer the following user prompt, ensuring your output strictly adheres to the markdown code block, start marker, and streaming requirements described above:

User Prompt: "{user_prompt_placeholder}"

Your Response (Streaming, within a markdown code block):
'''

    # Combine the parts and replace the placeholder
    full_prompt = base_instruction
    if system_instruction:
        full_prompt += "\n" + system_instruction
    full_prompt += prompt_template.replace("{user_prompt_placeholder}", user_prompt)

    return full_prompt

def validate_chat_request(messages: List[Message], req_id: str) -> Dict[str, Optional[str]]:
    # (Ported and adapted from server.cjs validateChatRequest)
    if not messages:
        raise ValueError(f"[{req_id}] Invalid request: 'messages' array is missing or empty.")

    user_message = next((msg for msg in reversed(messages) if msg.role == 'user'), None)
    if not user_message:
        raise ValueError(f"[{req_id}] Invalid request: No user message found.")

    user_prompt_content_input = user_message.content
    processed_user_prompt = ""

    if user_prompt_content_input is None:
        print(f"[{req_id}] (Validation) Warning: Last user message content is null. Treating as empty string.")
        processed_user_prompt = ""
    elif isinstance(user_prompt_content_input, str):
        processed_user_prompt = user_prompt_content_input
    elif isinstance(user_prompt_content_input, list): # Handle OpenAI vision format
        print(f"[{req_id}] (Validation) Info: Last user message content is an array. Processing text parts...")
        text_parts = []
        unsupported_parts = False
        for item_model in user_prompt_content_input:
            item = item_model.dict() # Convert Pydantic model to dict
            if item.get('type') == 'text' and isinstance(item.get('text'), str):
                text_parts.append(item['text'])
            elif item.get('type') == 'image_url':
                print(f"[{req_id}] (Validation) Warning: Found 'image_url'. This proxy cannot process images. Ignoring.")
                unsupported_parts = True
            else:
                print(f"[{req_id}] (Validation) Warning: Found unexpected item in content array: {item}. Converting to JSON string.")
                try:
                    text_parts.append(json.dumps(item))
                    unsupported_parts = True
                except Exception as e:
                    print(f"[{req_id}] (Validation) Error stringifying array item: {e}. Skipping.")
        processed_user_prompt = "\n".join(text_parts)
        if unsupported_parts:
            print(f"[{req_id}] (Validation) Warning: Some parts ignored (e.g., images).")
        if not processed_user_prompt:
            print(f"[{req_id}] (Validation) Warning: Processed array content resulted in an empty prompt.")
    else:
         print(f"[{req_id}] (Validation) Warning: User message content is unexpected type ({type(user_prompt_content_input)}). Converting to string.")
         processed_user_prompt = str(user_prompt_content_input)

    system_message = next((msg for msg in messages if msg.role == 'system'), None)
    processed_system_prompt = None
    if system_message:
        if isinstance(system_message.content, str):
            processed_system_prompt = system_message.content
        else:
             print(f"[{req_id}] (Validation) Warning: System prompt content is not a string. Ignoring.")

    return {
        "userPrompt": processed_user_prompt,
        "systemPrompt": processed_system_prompt
    }

async def get_raw_text_content(response_element, previous_text: str, req_id: str) -> str:
    # (Ported/Adapted from server.cjs getRawTextContent - using async Playwright)
    # Attempts to get text from <pre> first, then falls back to the main element
    try:
        await response_element.wait_for(state='attached', timeout=1500)
        pre_element = response_element.locator('pre').last
        raw_text = previous_text # Default to previous if all attempts fail
        try:
            await pre_element.wait_for(state='attached', timeout=500)
            raw_text = await pre_element.inner_text(timeout=1000)
        except PlaywrightAsyncError:
            # If <pre> fails, try the parent response element's inner_text
            # print(f"[{req_id}] (Info) Failed to get text from <pre>, falling back to parent.")
            try:
                 raw_text = await response_element.inner_text(timeout=2000)
            except PlaywrightAsyncError as e_parent:
                 print(f"[{req_id}] (Warn) getRawTextContent (inner_text) failed on both <pre> and parent: {e_parent}. Returning previous.")
                 raw_text = previous_text # Return previous if parent also fails
        return raw_text
    except PlaywrightAsyncError as e_attach:
        print(f"[{req_id}] (Warn) getRawTextContent failed waiting for response element attach: {e_attach}. Returning previous.")
        return previous_text
    except Exception as e_general:
         print(f"[{req_id}] (Warn) getRawTextContent unexpected error: {e_general}. Returning previous.")
         return previous_text

def generate_sse_chunk(delta: str, req_id: str, model: str) -> str:
    chunk = {
        "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}-{random.randint(100, 999)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk)}\n\n"

def generate_sse_stop_chunk(req_id: str, model: str, reason: str = "stop") -> str:
     chunk = {
        "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}-{random.randint(100, 999)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]
    }
     return f"data: {json.dumps(chunk)}\n\n"

def generate_sse_error_chunk(message: str, req_id: str, error_type: str = "server_error") -> str:
    error_payload = {"error": {"message": f"[{req_id}] {message}", "type": error_type}}
    return f"data: {json.dumps(error_payload)}\n\n"


# --- Helper Functions (Pre-checks) ---
def check_dependencies():
    """Checks if FastAPI/Uvicorn and Playwright are installed."""
    print("--- 步骤 1: 检查服务器依赖项 ---")
    required = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn[standard]",
        "playwright": "playwright"
    }
    missing = []
    modules_ok = True

    for mod_name, install_name in required.items():
        print(f"   - 检查 {mod_name}... ", end="")
        try:
            __import__(mod_name)
            print("✓ 已找到")
        except ImportError:
            print("❌ 未找到")
            missing.append(install_name)
            modules_ok = False

    if not modules_ok:
        print("\n❌ 错误: 缺少必要的 Python 库!")
        print("   请运行以下命令安装:")
        install_cmd = f"pip install {' '.join(missing)}"
        print(f"   {install_cmd}")
        sys.exit(1)
    else:
        print("✅ 服务器依赖检查通过.")
    print("---\n")


# --- Page Initialization Logic --- (Translate print statements)
async def _initialize_page_logic(browser: AsyncBrowser):
    global page_instance, is_page_ready
    print("--- 初始化页面逻辑 (连接到现有浏览器) ---") # 中文

    temp_context = None
    loaded_state = None

    if os.path.exists(STORAGE_STATE_PATH):
        print(f"找到现有状态文件: {STORAGE_STATE_PATH}. 尝试加载...") # 中文
        try:
            with open(STORAGE_STATE_PATH, 'r') as f:
                loaded_state = json.load(f)
            print("存储状态加载成功。") # 中文
        except Exception as e:
            print(f"警告: 从 {STORAGE_STATE_PATH} 加载存储状态失败: {e}. 将在没有已保存状态的情况下继续。") # 中文
            loaded_state = None
    else:
        print("未找到现有存储状态文件。如果需要，将尝试全新登录。") # 中文

    try:
        print(f"使用已连接的浏览器实例。版本: {browser.version}") # 中文

        print("创建新的浏览器上下文" + (" (使用已加载状态)。" if loaded_state else "。") ) # 中文
        try:
            viewport_size = {'width': 460, 'height': 800}
            print(f"   尝试设置视口大小: {viewport_size}") # 中文
            temp_context = await browser.new_context(
                storage_state=loaded_state,
                viewport=viewport_size
            )
        except Exception as context_err:
            print(f"警告: 使用已加载状态创建上下文失败: {context_err}. 尝试不使用状态...") # 中文
            if loaded_state:
                loaded_state = None
                temp_context = await browser.new_context()
            else:
                raise
        print("新的浏览器上下文已创建。") # 中文
        if not temp_context:
            raise RuntimeError("未能创建浏览器上下文。") # 中文

        found_page = None
        pages = temp_context.pages
        print(f"-> 在上下文中找到 {len(pages)} 个现有页面。正在搜索 AI Studio ({AI_STUDIO_URL_PATTERN})...") # 中文
        target_url_base = f"https://{AI_STUDIO_URL_PATTERN}"
        target_full_url = f"{target_url_base}prompts/new_chat"
        login_url_pattern = 'accounts.google.com'
        current_url = ""

        for p in pages:
            try:
                page_url_check = p.url
                print(f"   检查页面: {page_url_check}") # 中文
                if not p.is_closed() and target_url_base in page_url_check:
                    print(f"-> 找到潜在的 AI Studio 页面: {page_url_check}") # 中文
                    found_page = p
                    current_url = page_url_check
                    if "/prompts/" not in current_url:
                       print(f"   导航现有页面到 {target_full_url}...") # 中文
                       try:
                           await p.goto(target_full_url, wait_until="domcontentloaded", timeout=35000)
                           current_url = p.url
                           print(f"   导航成功: {current_url}") # 中文
                           if login_url_pattern in current_url:
                                 print("警告: 现有页面重定向到登录页。") # 中文
                                 await p.close()
                                 found_page = None
                                 current_url = ""
                                 break
                       except Exception as nav_err:
                           print(f"   警告: 在现有页面上导航失败: {nav_err}.") # 中文
                           found_page = None
                           current_url = ""
                    break
            except Exception as e:
                if not p.is_closed():
                    print(f"   警告: 检查页面 URL 时出错: {e}") # 中文

        if not found_page:
            print(f"-> 正在打开新页面...") # 中文
            found_page = await temp_context.new_page()
            print(f"   导航新页面到 {target_full_url}...") # 中文
            await found_page.goto(target_full_url, wait_until="domcontentloaded", timeout=60000)
            current_url = found_page.url
            print(f"-> 新页面导航尝试完成。当前 URL: {current_url}") # 中文

        if login_url_pattern in current_url:
            print("\n🛑 需要操作: 已重定向到 Google 登录！(登录状态可能丢失或过期) 🛑") # 中文
            print("   请在浏览器窗口 (由 camoufox 服务器管理) 中登录您的 Google 账户。") # 中文
            input("   在您登录并看到 AI Studio 后，在此处按 Enter 键...") # 中文

            print("   继续... 等待浏览器 URL 包含 AI Studio 模式...") # 中文
            try:
                await found_page.wait_for_url(f"**/{AI_STUDIO_URL_PATTERN}**", timeout=20000)
                current_url = found_page.url
                print(f"   登录后确认 URL: {current_url}") # 中文
                if login_url_pattern in current_url:
                    raise RuntimeError("手动登录尝试后仍在登录页面。") # 中文

                print("   登录成功！正在保存认证状态...") # 中文
                try:
                    await temp_context.storage_state(path=STORAGE_STATE_PATH)
                    print(f"   认证状态已保存到: {STORAGE_STATE_PATH}") # 中文
                except Exception as save_err:
                    print(f"   警告: 保存认证状态失败: {save_err}") # 中文

            except Exception as wait_err:
                print(f"   登录尝试后等待 AI Studio URL 时出错: {wait_err}") # 中文
                last_known_url = found_page.url
                raise RuntimeError(f"登录提示后未能检测到 AI Studio URL。最后已知 URL: {last_known_url}. 错误: {wait_err}") # 中文

        elif target_url_base not in current_url:
            print(f"\n⚠️ 警告: 最初到达意外页面: {current_url}") # 中文
            if loaded_state:
                 print("   这可能是由于加载的存储状态无效。尝试删除状态文件。") # 中文
            raise RuntimeError(f"初始导航后出现意外页面: {current_url}") # 中文

        print(f"-> 已确认页面是 AI Studio: {current_url}") # 中文
        await found_page.bring_to_front()
        print("-> 已尝试将页面置于前台。") # 中文
        await expect_async(found_page.locator(INPUT_SELECTOR)).to_be_visible(timeout=15000)
        print("-> 核心输入区域可见。") # 中文

        page_instance = found_page
        is_page_ready = True
        print(f"✅ 页面逻辑初始化成功。") # 中文

    except RuntimeError as e:
        print(f"❌ 页面逻辑初始化失败: {e}") # 中文
        page_instance = None
        is_page_ready = False
        raise e
    except Exception as e:
        print(f"❌ 常规页面逻辑初始化失败: {e}") # 中文
        traceback.print_exc()
        page_instance = None
        is_page_ready = False
        raise e

# --- Page Shutdown Logic --- (Translate print statements)
async def _close_page_logic():
    global page_instance, is_page_ready
    print("--- 运行页面逻辑关闭 --- ") # 中文
    page_instance = None
    is_page_ready = False
    print("页面逻辑状态已重置。") # 中文

# --- Lifespan context manager --- (Translate print statements)
@asynccontextmanager
async def lifespan(app_param: FastAPI):
    global playwright_manager, browser_instance, page_instance
    global is_playwright_ready, is_browser_connected, is_page_ready, is_initializing

    is_initializing = True
    print("\n" + "="*60)
    print(f"          🚀 AI Studio Proxy Server (Python/FastAPI) 🚀")
    print("="*60)
    print(f"FastAPI 生命周期: 启动中...") # 中文
    try:
        print(f"   启动 Playwright...") # 中文
        playwright_manager = await async_playwright().start()
        is_playwright_ready = True
        print(f"   ✅ Playwright 已启动。") # 中文

        ws_endpoint = os.environ.get('CAMOUFOX_WS_ENDPOINT')
        if not ws_endpoint:
             raise ValueError("未找到或环境变量 CAMOUFOX_WS_ENDPOINT 为空。") # 中文

        print(f"   连接到 Camoufox 服务器于: {ws_endpoint}") # 中文
        try:
            browser_instance = await playwright_manager.firefox.connect(ws_endpoint, timeout=30000)
            is_browser_connected = True
            print(f"   ✅ 已连接到浏览器实例: 版本 {browser_instance.version}") # 中文
        except Exception as connect_err:
            print(f"   ❌ 连接到 Camoufox 服务器 {ws_endpoint} 时出错: {connect_err}") # 中文
            is_browser_connected = False
            raise RuntimeError(f"未能连接到 Camoufox 服务器") from connect_err # 中文

        await _initialize_page_logic(browser_instance)

        print(f"✅ FastAPI 生命周期: 启动完成。") # 中文
        is_initializing = False
        yield

    except Exception as startup_err:
        print(f"❌ FastAPI 生命周期: 启动期间出错: {startup_err}") # 中文
        is_initializing = False
        if browser_instance and browser_instance.is_connected():
            try: await browser_instance.close()
            except: pass
        if playwright_manager:
            try: await playwright_manager.stop()
            except: pass
        raise RuntimeError(f"应用程序启动失败: {startup_err}") from startup_err # 中文
    finally:
        is_initializing = False

    print(f"\nFastAPI 生命周期: 关闭中...") # 中文
    await _close_page_logic()

    if browser_instance and browser_instance.is_connected():
        print(f"   正在关闭与浏览器实例的连接...") # 中文
        try:
            await browser_instance.close()
            print(f"   ✅ 浏览器连接已关闭。") # 中文
        except Exception as close_err:
            print(f"   ❌ 关闭浏览器连接时出错: {close_err}") # 中文
        finally:
            browser_instance = None
            is_browser_connected = False
    else:
        print(f"   ⚠️ 未找到活动的浏览器连接以关闭。") # 中文

    if playwright_manager:
        print(f"   停止 Playwright...") # 中文
        try:
            await playwright_manager.stop()
            print(f"   ✅ Playwright 已停止。") # 中文
        except Exception as stop_err:
            print(f"   ❌ 停止 Playwright 时出错: {stop_err}") # 中文
        finally:
            playwright_manager = None
            is_playwright_ready = False
    else:
        print(f"   ⚠️ 未找到 Playwright 管理器。") # 中文

    print(f"✅ FastAPI 生命周期: 关闭完成。") # 中文


# --- FastAPI App ---
app = FastAPI(
    title="AI Studio Proxy Server (Python/FastAPI/Camoufox)",
    description="A proxy server to interact with Google AI Studio using Playwright and Camoufox.",
    version="0.1.0-py",
    lifespan=lifespan # Use the updated lifespan context manager
)

# --- Serve Static HTML for Web UI --- (New Route)
@app.get("/", response_class=FileResponse)
async def read_index():
    # Assumes index.html is in the same directory as server.py
    index_html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if not os.path.exists(index_html_path):
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_html_path)

# --- API Endpoints --- (Translate print statements)
@app.get("/health")
async def health_check():
    status_val = "OK" if is_playwright_ready and is_browser_connected and is_page_ready else "Error"
    status = {
        "status": status_val,
        "message": "",
        "playwrightReady": is_playwright_ready,
        "browserConnected": is_browser_connected,
        "pageReady": is_page_ready,
        "initializing": is_initializing,
    }
    if status_val == "OK":
        status["message"] = "服务运行中，Playwright 活动，浏览器已连接，页面已初始化。" # 中文
        return JSONResponse(content=status, status_code=200)
    else:
        reasons = []
        if not is_playwright_ready: reasons.append("Playwright 未初始化") # 中文
        if not is_browser_connected: reasons.append("浏览器断开或不可用") # 中文
        if not is_page_ready: reasons.append("目标页面未初始化或未就绪") # 中文
        if is_initializing: reasons.append("初始化当前正在进行中") # 中文
        status["message"] = f"服务不可用。问题: {', '.join(reasons)}." # 中文
        return JSONResponse(content=status, status_code=503)

@app.get("/v1/models")
async def list_models():
    print("[API] 收到 /v1/models 请求。") # 中文
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "camoufox-proxy",
                # Add other fields if needed by client
                "permission": [],
                "root": MODEL_NAME,
                "parent": None,
            }
        ]
    }

# --- Helper: Detect Error ---
async def detect_and_extract_page_error(page: AsyncPage, req_id: str):
    """检查可见的错误/警告提示框并提取消息。"""
    error_toast_locator = page.locator(ERROR_TOAST_SELECTOR).last
    try:
        await error_toast_locator.wait_for(state='visible', timeout=1500)
        print(f"[{req_id}]    检测到错误/警告提示框元素。") # 中文
        message_locator = error_toast_locator.locator('span.content-text')
        error_message = await message_locator.text_content(timeout=1000)
        if error_message:
             print(f"[{req_id}]    提取的错误消息: {error_message}") # 中文
             return error_message.strip()
        else:
             print(f"[{req_id}]    警告: 检测到提示框，但无法从 span.content-text 提取特定消息。") # 中文
             return "检测到错误提示框，但无法提取特定消息。" # 中文
    except PlaywrightAsyncError:
        return None
    except Exception as e:
        print(f"[{req_id}]    警告: 检查页面错误时出错: {e}") # 中文
        return None

# --- Helper: Try Parse JSON ---
def try_parse_json(text: str, req_id: str):
    """Attempts to find and parse the outermost JSON object/array in text."""
    if not text or not isinstance(text, str):
        return None
    text = text.strip()

    start_index = -1
    end_index = -1

    first_brace = text.find('{')
    first_bracket = text.find('[')

    # Prioritize object if both found and object starts earlier
    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        start_index = first_brace
        end_index = text.rfind('}')
    elif first_bracket != -1:
        start_index = first_bracket
        end_index = text.rfind(']')

    if start_index == -1 or end_index == -1 or end_index < start_index:
        # print(f"[{req_id}] (JSON Parse) Could not find valid start/end markers.") # Optional debug
        return None

    json_text = text[start_index : end_index + 1]

    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        # print(f"[{req_id}] (JSON Parse) Failed for extracted text: {e}") # Optional debug
        return None

# --- Snapshot Helper --- (Translate logs)
async def save_error_snapshot(error_name: str = 'error'):
    """发生错误时保存屏幕截图和 HTML 快照。"""
    name_parts = error_name.split('_')
    req_id = name_parts[-1] if len(name_parts) > 1 and len(name_parts[-1]) == 7 else None
    base_error_name = error_name if not req_id else '_'.join(name_parts[:-1])
    log_prefix = f"[{req_id}]" if req_id else "[无请求ID]" # 中文

    if not browser_instance or not browser_instance.is_connected() or not page_instance or page_instance.is_closed():
        print(f"{log_prefix} 无法保存快照 ({base_error_name})，浏览器/页面不可用。") # 中文
        return

    print(f"{log_prefix} 尝试保存错误快照 ({base_error_name})...") # 中文
    timestamp = int(time.time() * 1000)
    error_dir = os.path.join(os.path.dirname(__file__), 'errors_py')
    try:
        if not os.path.exists(error_dir):
            os.makedirs(error_dir, exist_ok=True)

        filename_suffix = f"{req_id}_{timestamp}" if req_id else f"{timestamp}"
        filename_base = f"{base_error_name}_{filename_suffix}"
        screenshot_path = os.path.join(error_dir, f"{filename_base}.png")
        html_path = os.path.join(error_dir, f"{filename_base}.html")

        try:
            await page_instance.screenshot(path=screenshot_path, full_page=True, timeout=15000)
            print(f"{log_prefix}   快照已保存到: {screenshot_path}") # 中文
        except Exception as ss_err:
            print(f"{log_prefix}   保存屏幕截图失败 ({base_error_name}): {ss_err}") # 中文

        try:
            content = await page_instance.content()
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"{log_prefix}   HTML 已保存到: {html_path}") # 中文
        except Exception as html_err:
            print(f"{log_prefix}   保存 HTML 失败 ({base_error_name}): {html_err}") # 中文

    except Exception as dir_err:
        print(f"{log_prefix}   创建错误目录或保存快照时出错: {dir_err}") # 中文

# --- Main Chat Completion Logic --- (Remove Clear Chat, add delay, translate logs)
async def process_chat_request(req_id: str, request: ChatCompletionRequest, http_request: Request):
    print(f"[{req_id}] 处理聊天请求...") # 中文
    is_streaming = request.stream

    if not page_instance or page_instance.is_closed() or not is_page_ready:
        print(f"[{req_id}] 错误: 页面在处理期间变得无效 (is_closed={page_instance.is_closed()}, is_page_ready={is_page_ready}).") # 中文
        raise HTTPException(status_code=503, detail=f"[{req_id}] AI Studio 页面在处理过程中丢失或未就绪。") # 中文

    page = page_instance

    # 1. Validation
    try:
         validation_result = validate_chat_request(request.messages, req_id)
         user_prompt = validation_result["userPrompt"]
         system_prompt = validation_result["systemPrompt"]
         if user_prompt is None:
             raise ValueError("处理后的用户提示意外为 None。") # 中文
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"[{req_id}] 无效请求: {e}") # 中文

    print(f"[{req_id}] 用户提示 (已验证, 长度={len(user_prompt)}): '{user_prompt[:80]}...'") # 中文
    if system_prompt:
        print(f"[{req_id}] 系统提示 (已验证, 长度={len(system_prompt)}): '{system_prompt[:80]}...'") # 中文

    # 2. Prepare Prompt
    if is_streaming:
         prepared_prompt = prepare_ai_studio_prompt_stream(user_prompt, system_prompt)
         print(f"[{req_id}] 准备好的流式提示 (开始): '{prepared_prompt[:150]}...'") # 中文
    else:
         prepared_prompt = prepare_ai_studio_prompt(user_prompt, system_prompt)
         print(f"[{req_id}] 准备好的非流式提示 (开始): '{prepared_prompt[:150]}...'") # 中文

    # --- Client Disconnect Handling --- (Translate logs)
    client_disconnected = False
    disconnect_event = asyncio.Event()
    disconnect_task = None
    async def check_disconnect():
        nonlocal client_disconnected, disconnect_task
        try:
            while True:
                disconnected = await http_request.is_disconnected()
                if disconnected:
                    client_disconnected = True
                    disconnect_event.set()
                    print(f"[{req_id}] 客户端断开连接 (通过轮询检测到)。") # 中文
                    break
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        except Exception as e:
             if not client_disconnected:
                 client_disconnected = True
                 disconnect_event.set()
                 print(f"[{req_id}] 客户端断开连接 (通过异常检测到: {type(e).__name__})。") # 中文

    disconnect_task = asyncio.create_task(check_disconnect())
    # --- End Client Disconnect Handling ---

    try:
        # --- REMOVED Clear Chat Logic --- 

        # 3. Interact and Submit (Modified: Use Keyboard Shortcut first)
        print(f"[{req_id}] 填充提示并点击提交...") # 中文
        input_field = page.locator(INPUT_SELECTOR)
        submit_button = page.locator(SUBMIT_BUTTON_SELECTOR)

        await expect_async(input_field).to_be_visible(timeout=10000)
        await input_field.fill(prepared_prompt, timeout=60000)
        await expect_async(submit_button).to_be_enabled(timeout=10000)

        print(f"[{req_id}] 等待一小段时间让UI稳定...") # 中文
        await page.wait_for_timeout(200) # Add small delay

        # --- Try submitting with Control+Enter first ---
        submitted_successfully = False
        try:
            print(f"[{req_id}] 尝试使用 Control+Enter 快捷键提交...") # 中文
            await page.keyboard.press('Control+Enter')
            # Heuristic check: See if input field clears quickly after sending
            await expect_async(input_field).to_have_value('', timeout=2000) 
            print(f"[{req_id}] 快捷键提交成功 (输入框已清空)。") # 中文
            submitted_successfully = True
        except PlaywrightAsyncError as key_press_error:
            print(f"[{req_id}] 警告: Control+Enter 快捷键提交失败或未及时清空输入框: {key_press_error.message.split('\n')[0]}") # 中文
            # Fallback to clicking the button

        # --- Fallback to clicking if shortcut failed ---
        if not submitted_successfully:
            print(f"[{req_id}] 快捷键提交失败，回退到模拟点击提交按钮...") # 中文
            print(f"[{req_id}] 确保提交按钮在视图中...") # 中文
            try:
                await submit_button.scroll_into_view_if_needed(timeout=5000)
                print(f"[{req_id}] 提交按钮已滚动到视图中 (如果需要)。") # 中文
            except Exception as scroll_err:
                print(f"[{req_id}] 警告: 将提交按钮滚动到视图中失败: {scroll_err}") # 中文

            print(f"[{req_id}] 点击提交按钮 (force=True)...") # 中文
            try:
                 await submit_button.click(timeout=10000, force=True)
                 # Add a slightly longer check after click fallback
                 await expect_async(input_field).to_have_value('', timeout=3000)
                 print(f"[{req_id}] 模拟点击提交成功 (输入框已清空)。") # 中文
                 submitted_successfully = True
            except PlaywrightAsyncError as click_error:
                 print(f"[{req_id}] ❌ 错误: 模拟点击提交按钮也失败了: {click_error.message.split('\n')[0]}") # 中文
                 await save_error_snapshot(f"submit_fallback_click_fail_{req_id}")
                 raise click_error # Re-raise the error if both methods fail

        # 4. Locate Response Element
        print(f"[{req_id}] 定位响应元素...") # 中文
        response_element = page.locator(RESPONSE_CONTAINER_SELECTOR).last.locator(RESPONSE_TEXT_SELECTOR)
        # Increase timeout slightly for response element appearance after potential submit delay
        await expect_async(response_element).to_be_attached(timeout=20000) 
        print(f"[{req_id}] 响应元素已定位。") # 中文

        # 5. Handle Response (Streaming or Non-streaming)
        if is_streaming:
            print(f"[{req_id}] 处理 SSE 流...") # 中文
            async def stream_generator():
                last_raw_text = ""
                last_sent_response_content = ""
                response_started = False
                spinner_disappeared = False
                last_text_change_timestamp = time.time() * 1000
                stream_finished_naturally = False
                start_time = time.time() * 1000
                spinner_locator = page.locator(LOADING_SPINNER_SELECTOR)
                start_marker = '<<<START_RESPONSE>>>'
                loop_counter = 0
                last_scroll_time = 0 # Track last scroll time
                scroll_interval_ms = 3000 # Scroll every 3 seconds

                try:
                    while time.time() * 1000 - start_time < RESPONSE_COMPLETION_TIMEOUT:
                        current_loop_time_ms = time.time() * 1000 # Get current time in ms
                        if client_disconnected:
                             print(f"[{req_id}] 由于客户端断开连接，停止流生成器。") # 中文
                             break

                        loop_start_time = time.time() * 1000
                        loop_counter += 1

                        # --- Periodic Scroll --- 
                        if current_loop_time_ms - last_scroll_time > scroll_interval_ms:
                            try:
                                # print(f"[{req_id}] (Stream) Scrolling to bottom...") # Optional debug log
                                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                                last_scroll_time = current_loop_time_ms
                            except Exception as scroll_e:
                                print(f"[{req_id}] (Stream) 警告: 滚动到底部失败: {scroll_e}")
                        # --- End Periodic Scroll ---

                        if loop_counter % 10 == 0:
                             page_err_stream_periodic = await detect_and_extract_page_error(page, req_id)
                             if page_err_stream_periodic:
                                  print(f"[{req_id}] ❌ 流处理期间检测到错误 (周期性检查): {page_err_stream_periodic}") # 中文
                                  await save_error_snapshot(f"page_error_stream_periodic_{req_id}")
                                  yield generate_sse_error_chunk(f"AI Studio 错误: {page_err_stream_periodic}", req_id, "upstream_error") # 中文
                                  yield "data: [DONE]\n\n"
                                  return
                        
                        current_raw_text = await get_raw_text_content(response_element, last_raw_text, req_id)

                        if current_raw_text != last_raw_text:
                            last_text_change_timestamp = time.time() * 1000
                            potential_new_delta = ""
                            current_content_after_marker = ""

                            marker_index = current_raw_text.find(start_marker)
                            if marker_index != -1:
                                if not response_started:
                                    print(f"[{req_id}]    (流) 找到起始标记 '{start_marker}'.") # 中文
                                    response_started = True
                                current_content_after_marker = current_raw_text[marker_index + len(start_marker):]
                                potential_new_delta = current_content_after_marker[len(last_sent_response_content):]
                            elif response_started:
                                 potential_new_delta = ""
                                 print(f"[{req_id}] 警告: 起始标记在被看到后消失了。") # 中文

                            if potential_new_delta:
                                yield generate_sse_chunk(potential_new_delta, req_id, MODEL_NAME)
                                last_sent_response_content += potential_new_delta

                            last_raw_text = current_raw_text

                        if not spinner_disappeared:
                             try:
                                 if await spinner_locator.is_hidden():
                                     spinner_disappeared = True
                                     last_text_change_timestamp = time.time() * 1000
                                     print(f"[{req_id}]    Spinner 已隐藏。检查静默状态...") # 中文
                             except PlaywrightAsyncError:
                                 pass
                        
                        is_silent = spinner_disappeared and (time.time() * 1000 - last_text_change_timestamp > SILENCE_TIMEOUT_MS)
                        if is_silent:
                            print(f"[{req_id}] 检测到静默。完成流。") # 中文
                            stream_finished_naturally = True
                            break

                        loop_duration = time.time() * 1000 - loop_start_time
                        wait_time = max(0, POLLING_INTERVAL_STREAM - loop_duration) / 1000
                        await asyncio.sleep(wait_time)

                    if client_disconnected:
                         yield generate_sse_stop_chunk(req_id, MODEL_NAME, "client_disconnect")
                         return

                    page_err_stream_final = await detect_and_extract_page_error(page, req_id)
                    if page_err_stream_final:
                        print(f"[{req_id}] ❌ 在完成流之前检测到错误: {page_err_stream_final}") # 中文
                        await save_error_snapshot(f"page_error_stream_final_{req_id}")
                        yield generate_sse_error_chunk(f"AI Studio 错误: {page_err_stream_final}", req_id, "upstream_error") # 中文
                        yield "data: [DONE]\n\n"
                        return
                    
                    if stream_finished_naturally:
                        final_raw_text = await get_raw_text_content(response_element, last_raw_text, req_id)
                        final_content_after_marker = ""
                        final_marker_index = final_raw_text.find(start_marker)
                        if final_marker_index != -1:
                             final_content_after_marker = final_raw_text[final_marker_index + len(start_marker):]
                        final_delta = final_content_after_marker[len(last_sent_response_content):]
                        if final_delta:
                             print(f"[{req_id}] 发送最终增量 (长度: {len(final_delta)})") # 中文
                             yield generate_sse_chunk(final_delta, req_id, MODEL_NAME)

                        yield generate_sse_stop_chunk(req_id, MODEL_NAME)
                        print(f"[{req_id}] ✅ 流自然完成。") # 中文
                    else: 
                        print(f"[{req_id}] ⚠️ 流在 {RESPONSE_COMPLETION_TIMEOUT / 1000} 秒后超时。") # 中文
                        await save_error_snapshot(f"streaming_timeout_{req_id}")
                        yield generate_sse_error_chunk("流处理在服务器上超时。", req_id) # 中文
                        yield generate_sse_stop_chunk(req_id, MODEL_NAME, "timeout")

                    yield "data: [DONE]\n\n"

                except asyncio.CancelledError:
                     print(f"[{req_id}] 流生成器已取消 (可能客户端断开连接)。") # 中文
                     yield "data: [DONE]\n\n"
                except Exception as e:
                    print(f"[{req_id}] ❌ 流式生成期间出错: {e}") # 中文
                    await save_error_snapshot(f"streaming_error_{req_id}")
                    traceback.print_exc()
                    yield generate_sse_error_chunk(f"流式处理期间服务器错误: {e}", req_id) # 中文
                    yield "data: [DONE]\n\n"

            return StreamingResponse(stream_generator(), media_type="text/event-stream")

        else: # Non-streaming
            print(f"[{req_id}] 处理非流式响应...") # 中文
            start_time_ns = time.time()
            final_state_reached = False
            final_state_check_initiated = False
            spinner_locator = page.locator(LOADING_SPINNER_SELECTOR)
            input_field = page.locator(INPUT_SELECTOR)
            submit_button = page.locator(SUBMIT_BUTTON_SELECTOR)
            last_scroll_time_ns = 0 # Track last scroll time
            scroll_interval_ms_ns = 3000 # Scroll every 3 seconds

            while time.time() - start_time_ns < RESPONSE_COMPLETION_TIMEOUT / 1000:
                current_loop_time_ms_ns = time.time() * 1000
                if client_disconnected:
                    print(f"[{req_id}] 由于客户端断开连接，非流式处理已取消。") # 中文
                    raise HTTPException(status_code=499, detail=f"[{req_id}] 客户端关闭了请求") # 中文

                # --- Periodic Scroll --- 
                if current_loop_time_ms_ns - last_scroll_time_ns > scroll_interval_ms_ns:
                    try:
                        # print(f"[{req_id}] (Non-Stream) Scrolling to bottom...") # Optional debug log
                        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                        last_scroll_time_ns = current_loop_time_ms_ns
                    except Exception as scroll_e:
                        print(f"[{req_id}] (Non-Stream) 警告: 滚动到底部失败: {scroll_e}")
                # --- End Periodic Scroll ---

                spinner_hidden = False
                input_empty = False
                button_disabled = False

                try:
                    await expect_async(spinner_locator).to_be_hidden(timeout=SPINNER_CHECK_TIMEOUT_MS)
                    spinner_hidden = True
                except PlaywrightAsyncError: pass

                if spinner_hidden:
                    try:
                        await expect_async(input_field).to_have_value('', timeout=FINAL_STATE_CHECK_TIMEOUT_MS)
                        input_empty = True
                    except PlaywrightAsyncError: pass

                    if input_empty:
                        try:
                            await expect_async(submit_button).to_be_disabled(timeout=FINAL_STATE_CHECK_TIMEOUT_MS)
                            button_disabled = True
                        except PlaywrightAsyncError: pass

                if spinner_hidden and input_empty and button_disabled:
                    if not final_state_check_initiated:
                        final_state_check_initiated = True
                        print(f"[{req_id}]    检测到潜在最终状态。等待 {POST_COMPLETION_BUFFER} 毫秒以确认...") # 中文
                        await asyncio.sleep(POST_COMPLETION_BUFFER / 1000)
                        print(f"[{req_id}]    {POST_COMPLETION_BUFFER} 毫秒等待结束。严格重新检查状态...") # 中文
                        try:
                            await expect_async(spinner_locator).to_be_hidden(timeout=500)
                            await expect_async(input_field).to_have_value('', timeout=500)
                            await expect_async(submit_button).to_be_disabled(timeout=500)
                            print(f"[{req_id}]    状态已确认。检查文本稳定性 {SILENCE_TIMEOUT_MS} 毫秒...") # 中文

                            text_stable = False
                            silence_check_start_time = time.time()
                            last_check_text = await get_raw_text_content(response_element, '', req_id)

                            while time.time() - silence_check_start_time < SILENCE_TIMEOUT_MS / 1000:
                                await asyncio.sleep(POLLING_INTERVAL / 1000)
                                current_check_text = await get_raw_text_content(response_element, '', req_id)
                                if current_check_text == last_check_text:
                                    if time.time() - silence_check_start_time >= SILENCE_TIMEOUT_MS / 1000:
                                         print(f"[{req_id}]    文本稳定 {SILENCE_TIMEOUT_MS} 毫秒。处理完成。") # 中文
                                         text_stable = True
                                         break
                                else:
                                    print(f"[{req_id}]    (静默检查) 文本已更改。重置计时器。") # 中文
                                    silence_check_start_time = time.time()
                                    last_check_text = current_check_text

                            if text_stable:
                                final_state_reached = True
                                break
                            else:
                                print(f"[{req_id}]    ⚠️ 警告: 文本静默检查在 {SILENCE_TIMEOUT_MS} 毫秒后超时。无论如何继续。") # 中文
                                final_state_reached = True
                                break

                        except PlaywrightAsyncError as recheck_error:
                            print(f"[{req_id}]    状态在确认期间发生变化 ({recheck_error})。继续轮询。") # 中文
                            final_state_check_initiated = False
                        except Exception as stability_err:
                             print(f"[{req_id}]    文本稳定性检查期间出错: {stability_err}") # 中文
                             traceback.print_exc()
                             final_state_check_initiated = False

                else:
                    if final_state_check_initiated:
                         print(f"[{req_id}]    最终状态条件不再满足。重置确认标志。") # 中文
                         final_state_check_initiated = False
                    await asyncio.sleep(POLLING_INTERVAL * 2 / 1000)

            if client_disconnected:
                 raise HTTPException(status_code=499, detail=f"[{req_id}] 客户端关闭了请求") # 中文

            print(f"[{req_id}] 在最终解析前检查页面错误...") # 中文
            page_err_nonstream = await detect_and_extract_page_error(page, req_id)
            if page_err_nonstream:
                 print(f"[{req_id}] ❌ 在最终解析前检测到错误: {page_err_nonstream}") # 中文
                 await save_error_snapshot(f"page_error_nonstream_{req_id}")
                 raise HTTPException(status_code=502, detail=f"[{req_id}] AI Studio 错误: {page_err_nonstream}") # 中文

            if not final_state_reached:
                 print(f"[{req_id}] ⚠️ 非流式等待超时。尝试内容检索。") # 中文
                 await save_error_snapshot(f"nonstream_final_state_timeout_{req_id}")
            else:
                 print(f"[{req_id}] ✅ 最终状态已到达。获取并解析最终内容...") # 中文

            final_content_for_user = ""
            try:
                 final_raw_text = await get_raw_text_content(response_element, '', req_id)
                 print(f"[{req_id}] 最终原始文本 (长度={len(final_raw_text)}): '{final_raw_text[:100]}...'") # 中文

                 if not final_raw_text or not final_raw_text.strip():
                     print(f"[{req_id}] 警告: 从响应元素获取的原始文本为空。") # 中文
                     final_content_for_user = ""
                 else:
                    parsed_json = try_parse_json(final_raw_text, req_id)
                    ai_response_text_from_json = None

                    if parsed_json:
                         if isinstance(parsed_json.get("response"), str):
                              ai_response_text_from_json = parsed_json["response"]
                              print(f"[{req_id}]    从 JSON 中提取了 'response' 字段。") # 中文
                         else:
                             try:
                                 ai_response_text_from_json = json.dumps(parsed_json)
                                 print(f"[{req_id}]    警告: 在 JSON 中未找到/非字符串 'response' 字段。使用字符串化的 JSON。") # 中文
                             except Exception as stringify_err:
                                  print(f"[{req_id}]    字符串化解析的 JSON 时出错: {stringify_err}") # 中文
                                  ai_response_text_from_json = final_raw_text
                    else:
                        print(f"[{req_id}]    警告: 无法从原始文本解析 JSON。使用原始文本作为响应。") # 中文
                        ai_response_text_from_json = final_raw_text
                    
                    start_marker = '<<<START_RESPONSE>>>'
                    if ai_response_text_from_json and ai_response_text_from_json.startswith(start_marker):
                        final_content_for_user = ai_response_text_from_json[len(start_marker):]
                        print(f"[{req_id}]    移除了起始标记。") # 中文
                    elif ai_response_text_from_json:
                        final_content_for_user = ai_response_text_from_json
                        print(f"[{req_id}]    警告: 在最终文本中未找到起始标记。") # 中文
                    else:
                         final_content_for_user = ""

            except Exception as e:
                print(f"[{req_id}] ❌ 获取/解析最终非流式内容时出错: {e}") # 中文
                await save_error_snapshot(f"get_final_content_error_{req_id}")
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=f"[{req_id}] 处理最终响应时出错: {e}") # 中文

            response_payload = {
                "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": MODEL_NAME,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": final_content_for_user},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            return JSONResponse(content=response_payload)

    except PlaywrightAsyncError as e:
        print(f"[{req_id}] ❌ Playwright 处理期间出错: {e}") # 中文
        await save_error_snapshot(f"playwright_error_{req_id}") # Pass req_id here
        raise HTTPException(status_code=500, detail=f"[{req_id}] Playwright 错误: {e}") # 中文
    except HTTPException:
         raise
    except Exception as e:
        print(f"[{req_id}] ❌ 处理期间意外错误: {e}") # 中文
        await save_error_snapshot(f"unexpected_error_{req_id}") # Pass req_id here
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"[{req_id}] 意外服务器错误: {e}") # 中文
    finally:
         if disconnect_task and not disconnect_task.done():
              disconnect_task.cancel()
              try: await disconnect_task
              except asyncio.CancelledError: pass
         print(f"[{req_id}] --- 完成处理聊天请求 --- ") # 中文


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, http_request: Request):
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    print(f"[{req_id}] === 收到 /v1/chat/completions 请求 === 模式: {'流式' if request.stream else '非流式'}") # 中文

    if is_initializing:
        print(f"[{req_id}] ⏳ 服务仍在初始化。请求可能延迟或失败。") # 中文
        raise HTTPException(status_code=503, detail=f"[{req_id}] 服务初始化中，请稍后重试。") # 中文
    if not is_playwright_ready or not is_browser_connected or not is_page_ready:
         print(f"[{req_id}] ❌ 请求失败: 服务未完全就绪 (Playwright:{is_playwright_ready}, Browser:{is_browser_connected}, Page:{is_page_ready}).") # 中文
         raise HTTPException(status_code=503, detail=f"[{req_id}] 与 Camoufox 浏览器/页面的连接未激活。请确保 camoufox 服务器正在运行并重试。") # 中文

    try:
        return await asyncio.wait_for(
             process_chat_request(req_id, request, http_request),
             timeout=RESPONSE_COMPLETION_TIMEOUT / 1000
        )
    except asyncio.TimeoutError:
        print(f"[{req_id}] ❌ 整体请求在 {RESPONSE_COMPLETION_TIMEOUT / 1000} 秒后超时。") # 中文
        if request.stream:
            error_chunk = generate_sse_error_chunk("整体请求超时。", req_id, "timeout_error") # 中文
            done_chunk = "data: [DONE]\n\n"
            return StreamingResponse(iter([error_chunk, done_chunk]), media_type="text/event-stream", status_code=504)
        else:
            raise HTTPException(status_code=504, detail=f"[{req_id}] 整体请求处理超时。") # 中文
    except HTTPException as http_exc:
         raise http_exc
    except Exception as e:
         print(f"[{req_id}] ❌ 完成端点级别发生意外错误: {e}") # 中文
         raise HTTPException(status_code=500, detail=f"[{req_id}] 请求处理期间意外的服务器错误。") # 中文

# --- __main__ block --- (Translate print statements)
if __name__ == "__main__":
    check_dependencies()

    SERVER_PORT = 2048
    print(f"--- 步骤 2: 准备启动 FastAPI/Uvicorn (端口: {SERVER_PORT}) ---") # 中文
    import uvicorn

    try:
        uvicorn.run(
            "server:app",
            host="127.0.0.1",
            port=SERVER_PORT,
            log_level="info",
            workers=1,
            use_colors=False
        )
    except OSError as e:
        if e.errno == 48:
            print(f"\n❌ 错误：端口 {SERVER_PORT} 已被占用！") # 中文 (Keep f-string correction)
            print("   Uvicorn 无法绑定到该端口。") # 中文
            print("   请手动查找并结束占用该端口的进程:") # 中文
            print(f"     1. 查找进程 PID: lsof -t -i:{SERVER_PORT}")
            print(f"     2. 结束进程 (替换 <PID>): kill -9 <PID>")
            print("   然后重新运行此脚本。") # 中文
            sys.exit(1)
        else:
            print(f"❌ 发生未处理的 OS 错误: {e}") # 中文
            raise e
    except Exception as e:
         print(f"❌ 启动服务器时发生意外错误: {e}") # 中文
         traceback.print_exc()
         sys.exit(1) 