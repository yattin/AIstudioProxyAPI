# server.py
import asyncio
import random
import time
import json
from typing import List, Optional, Dict, Any, Union, AsyncGenerator, Tuple # Add Tuple
import os
import traceback
from contextlib import asynccontextmanager
import sys
import platform
from asyncio import Queue, Lock, Future, Task, Event # Add Queue, Lock, Future, Task, Event

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from pydantic import BaseModel, Field
# Assuming camoufox is installed and provides sync/async APIs
# Adjust the import based on actual library structure if needed
# from camoufox.sync_api import Camoufox as CamoufoxSync
# Import the async module directly
# import camoufox.async_api
from playwright.async_api import Page as AsyncPage, Browser as AsyncBrowser, Playwright as AsyncPlaywright, Error as PlaywrightAsyncError, expect as expect_async, BrowserContext as AsyncBrowserContext
from playwright.async_api import async_playwright

# --- Configuration (Mirrored from server.cjs, adjust as needed) ---
# SERVER_PORT = 2048 # Port will be handled by uvicorn when running
AI_STUDIO_URL_PATTERN = 'aistudio.google.com/'
RESPONSE_COMPLETION_TIMEOUT = 300000 # 5 minutes total timeout (in ms)
POLLING_INTERVAL = 300 # ms - Standard polling interval
POLLING_INTERVAL_STREAM = 200 # ms - Stream-specific polling interval
SILENCE_TIMEOUT_MS = 3000 # ms (Increased from 1500ms)
# v2.12: Timeout for secondary checks *after* spinner disappears
POST_SPINNER_CHECK_DELAY_MS = 500 # Spinner消失后稍作等待再检查其他状态
FINAL_STATE_CHECK_TIMEOUT_MS = 1500 # 检查按钮和输入框最终状态的超时
SPINNER_CHECK_TIMEOUT_MS = 1000 # 检查Spinner状态的超时
POST_COMPLETION_BUFFER = 1000 # JSON模式下可以缩短检查后等待时间
# !! 新增：清空验证相关常量 !! (Mirrored)
CLEAR_CHAT_VERIFY_TIMEOUT_MS = 5000 # 等待清空生效的总超时时间 (ms)
CLEAR_CHAT_VERIFY_INTERVAL_MS = 300 # 检查清空状态的轮询间隔 (ms)

# --- Configuration ---
# STORAGE_STATE_PATH = os.path.join(os.path.dirname(__file__), "auth_state.json") # Old path, replaced by profile logic
AUTH_PROFILES_DIR = os.path.join(os.path.dirname(__file__), 'auth_profiles')
ACTIVE_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'active')
SAVED_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'saved')

# --- Constants (Mirrored from server.cjs, verify if still valid in Firefox/Camoufox) ---
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
playwright_manager: Optional[AsyncPlaywright] = None
browser_instance: Optional[AsyncBrowser] = None
# context_instance: Optional[AsyncBrowserContext] = None # Context is temporary within init
page_instance: Optional[AsyncPage] = None
is_playwright_ready = False
is_browser_connected = False
is_page_ready = False
is_initializing = False

# !! 新增：请求队列和处理锁 !!
request_queue: Queue = Queue()
processing_lock: Lock = Lock() # Lock to ensure sequential processing
worker_task: Optional[Task] = None # To hold the worker task

# --- Pydantic Models for API validation ---
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

# --- 自定义异常类 ---
class ClientDisconnectedError(Exception):
    """用于在检测到客户端断开时在Worker内部传递信号的自定义异常。"""
    pass

# --- Helper Functions (Ported/Adapted from server.cjs) ---

def prepare_ai_studio_prompt(user_prompt: str, system_prompt: Optional[str] = None) -> str:
    # ... (code unchanged) ...
    base_instruction = """
IMPORTANT: Your entire response MUST be a single JSON object. Do not include any text outside of this JSON object.
The JSON object must have a single key named "response".
Inside the value of the "response" key (which is a string), you MUST put the exact marker "<<<START_RESPONSE>>>"" at the very beginning of your actual answer. There should be NO text before this marker within the response string.
"""

    system_instruction = ""
    if system_prompt and system_prompt.strip():
        system_instruction = f"System Instruction: {system_prompt}\\n"

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
  "response": "<<<START_RESPONSE>>>```python\\ndef add(a, b):\\n  return a + b\\n```"
}

Now, answer the following user prompt, ensuring your output strictly adheres to the JSON format AND the start marker requirement described above:

User Prompt: "{user_prompt_placeholder}"

Your JSON Response:
'''
    full_prompt = base_instruction
    if system_instruction:
        full_prompt += "\\n" + system_instruction
    full_prompt += prompt_template.replace("{user_prompt_placeholder}", user_prompt)
    return full_prompt


def prepare_ai_studio_prompt_stream(user_prompt: str, system_prompt: Optional[str] = None) -> str:
    # ... (code unchanged) ...
    base_instruction = """
IMPORTANT: For this streaming request, your entire response MUST be enclosed in a single markdown code block (like ``` block ```).
Inside this code block, your actual answer text MUST start immediately after the exact marker "<<<START_RESPONSE>>>".
Start your response exactly with "```\\n<<<START_RESPONSE>>>" followed by your answer content.
Continue outputting your answer content. You SHOULD include the final closing "```" at the very end of your full response stream.
"""
    system_instruction = ""
    if system_prompt and system_prompt.strip():
        system_instruction = f"System Instruction: {system_prompt}\\n"
    prompt_template = '''
Example 1 (Streaming):
User asks: "What is the capital of France?"
Your streamed response MUST look like this over time:
Stream part 1: ```\\n<<<START_RESPONSE>>>The capital
Stream part 2:  of France is
Stream part 3:  Paris.\\n```

Example 2 (Streaming):
User asks: "Write a python function to add two numbers"
Your streamed response MUST look like this over time:
Stream part 1: ```\\n<<<START_RESPONSE>>>```python\\ndef add(a, b):
Stream part 2: \\n  return a + b\\n
Stream part 3: ```\\n```

Now, answer the following user prompt, ensuring your output strictly adheres to the markdown code block, start marker, and streaming requirements described above:

User Prompt: "{user_prompt_placeholder}"

Your Response (Streaming, within a markdown code block):
'''
    full_prompt = base_instruction
    if system_instruction:
        full_prompt += "\\n" + system_instruction
    full_prompt += prompt_template.replace("{user_prompt_placeholder}", user_prompt)
    return full_prompt

def validate_chat_request(messages: List[Message], req_id: str) -> Dict[str, Optional[str]]:
    # ... (code unchanged) ...
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
        processed_user_prompt = "\\n".join(text_parts)
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
    # ... (code unchanged) ...
    """获取AI响应的原始文本内容，优先使用 <pre> 标签，并清理已知UI文本。"""
    raw_text = previous_text # 默认返回上一次的文本以防万一
    try:
        # Reduce default wait slightly, rely on caller's timeout
        await response_element.wait_for(state='attached', timeout=1000)
        pre_element = response_element.locator('pre').last
        
        pre_found_and_visible = False
        try:
            # Make pre check faster
            await pre_element.wait_for(state='visible', timeout=250)
            pre_found_and_visible = True
        except PlaywrightAsyncError:
            pass # pre 元素不存在或不可见是正常情况

        if pre_found_and_visible:
            try:
                # Reduce timeout for getting text
                raw_text = await pre_element.inner_text(timeout=500)
            except PlaywrightAsyncError as pre_err:
                print(f"[{req_id}] (Warn) Failed to get innerText from visible <pre>: {pre_err.message.split('\\n')[0]}", flush=True)
                try:
                     raw_text = await response_element.inner_text(timeout=1000) # Slightly longer fallback
                except PlaywrightAsyncError as e_parent:
                     print(f"[{req_id}] (Warn) getRawTextContent (inner_text) failed on parent after <pre> fail: {e_parent}. Returning previous.", flush=True)
                     raw_text = previous_text
        else:
            try:
                 raw_text = await response_element.inner_text(timeout=1500) # Slightly longer if no pre
            except PlaywrightAsyncError as e_parent:
                 print(f"[{req_id}] (Warn) getRawTextContent (inner_text) failed on parent (no pre): {e_parent}. Returning previous.", flush=True)
                 raw_text = previous_text

        # --- Text Cleaning Logic --- (Unchanged)
        if raw_text and isinstance(raw_text, str): # 确保是字符串
            replacements = {
                "IGNORE_WHEN_COPYING_START": "",
                "content_copy": "",
                "download": "",
                "Use code with caution.": "",
                "IGNORE_WHEN_COPYING_END": ""
            }
            cleaned_text = raw_text
            found_junk = False
            for junk, replacement in replacements.items():
                if junk in cleaned_text:
                    cleaned_text = cleaned_text.replace(junk, replacement)
                    found_junk = True
            if found_junk:
                cleaned_text = "\\n".join([line.strip() for line in cleaned_text.splitlines() if line.strip()])
                print(f"[{req_id}] (清理) 已移除响应文本中的已知UI元素。", flush=True) # 中文
                raw_text = cleaned_text
        # --- End Cleaning ---

        return raw_text
        
    except PlaywrightAsyncError as e_attach:
        # Be less verbose on attach errors, might happen during streaming
        # print(f"[{req_id}] (Warn) getRawTextContent failed waiting for response element attach: {e_attach}. Returning previous.", flush=True)
        return previous_text
    except Exception as e_general:
         print(f"[{req_id}] (Warn) getRawTextContent unexpected error: {e_general}. Returning previous.", flush=True)
         return previous_text


def generate_sse_chunk(delta: str, req_id: str, model: str) -> str:
    # ... (code unchanged) ...
    chunk = {
        "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}-{random.randint(100, 999)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk)}\\n\\n"

def generate_sse_stop_chunk(req_id: str, model: str, reason: str = "stop") -> str:
    # ... (code unchanged) ...
     chunk = {
        "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}-{random.randint(100, 999)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]
    }
     return f"data: {json.dumps(chunk)}\\n\\n"

def generate_sse_error_chunk(message: str, req_id: str, error_type: str = "server_error") -> str:
    # ... (code unchanged) ...
    error_payload = {"error": {"message": f"[{req_id}] {message}", "type": error_type}}
    return f"data: {json.dumps(error_payload)}\\n\\n"

# --- Helper Functions (Pre-checks) ---
def check_dependencies():
    # ... (code unchanged) ...
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
        print("\\n❌ 错误: 缺少必要的 Python 库!")
        print("   请运行以下命令安装:")
        install_cmd = f"pip install {' '.join(missing)}"
        print(f"   {install_cmd}")
        sys.exit(1)
    else:
        print("✅ 服务器依赖检查通过.")
    print("---\\n")

# --- Page Initialization Logic --- (Translate print statements)
async def _initialize_page_logic(browser: AsyncBrowser):
    global page_instance, is_page_ready
    print("--- 初始化页面逻辑 (连接到现有浏览器) ---") # 中文
    temp_context = None
    # loaded_state = None # 将不再从此变量加载，但保留用于逻辑判断
    storage_state_path_to_use = None # 用于决定使用哪个状态文件
    
    # 步骤 16: 读取环境变量
    launch_mode = os.environ.get('LAUNCH_MODE', 'debug') # 默认为 debug 以防万一
    active_auth_json_path = os.environ.get('ACTIVE_AUTH_JSON_PATH')
    print(f"   检测到启动模式: {launch_mode}")
    
    storage_state_path_to_use = None # 默认不加载
    loop = asyncio.get_running_loop() # 获取事件循环用于 input

    if launch_mode == 'headless':
        if active_auth_json_path and os.path.exists(active_auth_json_path):
            print(f"   无头模式将使用的认证文件: {active_auth_json_path}")
            storage_state_path_to_use = active_auth_json_path
        else:
            print(f"   ❌ 错误: 无头模式启动，但提供的 ACTIVE_AUTH_JSON_PATH 无效或文件不存在: '{active_auth_json_path}'。将不加载 storage_state。")
            # 在无头模式下，没有有效的 active profile 是致命错误
            raise RuntimeError("无头模式需要一个有效的 ACTIVE_AUTH_JSON_PATH。")

    elif launch_mode == 'debug':
         print(f"   调试模式: 检查可用的认证文件...")
         available_profiles = []
         # 查找 active 和 saved 目录中的 JSON 文件
         for profile_dir in [ACTIVE_AUTH_DIR, SAVED_AUTH_DIR]:
             if os.path.exists(profile_dir):
                 try:
                     for filename in os.listdir(profile_dir):
                         if filename.endswith(".json"):
                             full_path = os.path.join(profile_dir, filename)
                             relative_dir = os.path.basename(profile_dir) # 'active' or 'saved'
                             available_profiles.append({"name": f"{relative_dir}/{filename}", "path": full_path})
                 except OSError as e:
                     print(f"   ⚠️ 警告: 无法读取目录 '{profile_dir}': {e}")

         if not available_profiles:
             print("   未在 active 或 saved 目录中找到 .json 认证文件。将使用浏览器当前状态。")
             storage_state_path_to_use = None
         else:
             print('-'*60)
             print("   找到以下可用的认证文件:")
             for i, profile in enumerate(available_profiles):
                 print(f"     {i+1}: {profile['name']}")
             print("     N: 不加载任何文件 (使用浏览器当前状态)")
             print('-'*60)
             
             prompt = "   请选择要加载的认证文件编号 (输入 N 或直接回车则不加载): "
             choice = await loop.run_in_executor(None, input, prompt)
             
             if choice.lower() == 'n' or not choice:
                 print("   好的，不加载认证文件，将使用浏览器当前状态。")
                 storage_state_path_to_use = None
             else:
                 try:
                     choice_index = int(choice) - 1
                     if 0 <= choice_index < len(available_profiles):
                         selected_profile = available_profiles[choice_index]
                         storage_state_path_to_use = selected_profile["path"]
                         print(f"   已选择加载: {selected_profile['name']}")
                     else:
                         print("   无效的选择编号。将不加载认证文件，使用浏览器当前状态。")
                         storage_state_path_to_use = None
                 except ValueError:
                     print("   无效的输入。将不加载认证文件，使用浏览器当前状态。")
                     storage_state_path_to_use = None
             print('-'*60)

    else: # 未知模式
         print(f"   ⚠️ 警告: 未知的启动模式 '{launch_mode}'。将尝试使用浏览器当前状态。不加载 storage_state 文件。")
         storage_state_path_to_use = None
        
    # --- 创建 Context 的逻辑保持不变，使用最终确定的 storage_state_path_to_use ---
    try:
        print(f"使用已连接的浏览器实例。版本: {browser.version}") # 中文
        # 步骤 17: 根据模式创建上下文
        print("创建新的浏览器上下文...")
        try:
            viewport_size = {'width': 460, 'height': 800}
            print(f"   尝试设置视口大小: {viewport_size}") # 中文
            
            # 根据 storage_state_path_to_use 的值决定是否加载 storage_state
            if storage_state_path_to_use:
                print(f"   (使用 storage_state='{os.path.basename(storage_state_path_to_use)}')")
                temp_context = await browser.new_context(
                    storage_state=storage_state_path_to_use, # 使用找到的路径
                    viewport=viewport_size
                )
            else:
                 print("   (不使用 storage_state)")
                 temp_context = await browser.new_context(
                     viewport=viewport_size
                     # storage_state=None # 默认即是 None
                 )
        except Exception as context_err:
            print(f"❌ 创建浏览器上下文时出错: {context_err}")
            # 如果是因为加载状态文件失败，给出更具体的提示
            if storage_state_path_to_use and 'storageState: Failed to read storage state from file' in str(context_err):
                 print(f"   错误详情：无法从 '{storage_state_path_to_use}' 加载认证状态。文件可能已损坏或格式不正确。")
            raise # 直接重新抛出错误
            
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
                if not p.is_closed() and target_url_base in page_url_check and "/prompts/" in page_url_check:
                    print(f"-> 找到现有的 AI Studio 对话页面: {page_url_check}") # 中文
                    found_page = p
                    current_url = page_url_check
                    break # 直接使用找到的页面
                elif not p.is_closed() and target_url_base in page_url_check:
                    print(f"   找到潜在的 AI Studio 页面 (非对话页): {page_url_check}，尝试导航到 {target_full_url}...") # 中文
                    try:
                       await p.goto(target_full_url, wait_until="domcontentloaded", timeout=35000)
                       current_url = p.url
                       print(f"   导航成功，当前 URL: {current_url}") # 中文
                       # 检查导航后是否到了登录页
                       if login_url_pattern in current_url:
                             print("   警告: 导航后重定向到登录页。关闭此页。") # 更新提示
                             await p.close()
                             found_page = None
                             current_url = ""
                             if launch_mode == 'headless':
                                 raise RuntimeError(f"无头模式导航后重定向到登录页面。认证文件 '{os.path.basename(storage_state_path_to_use) if storage_state_path_to_use else '未知'}' 可能无效。")
                             break # 退出页面循环，将尝试新建页面
                       elif target_url_base in current_url and "/prompts/" in current_url:
                           print(f"-> 导航到 AI Studio 对话页面成功: {current_url}")
                           found_page = p # 使用导航成功的页面
                           break
                       else:
                           print(f"   警告: 导航后 URL 不符合预期: {current_url}")
                           await p.close() # 关闭不符合预期的页面
                           found_page = None
                           current_url = ""
                    except Exception as nav_err:
                       print(f"   警告: 在现有页面上导航失败: {nav_err}。关闭此页。") # 中文
                       try:
                           if not p.is_closed(): await p.close()
                       except: pass
                       found_page = None
                       current_url = ""
                    break # 不论导航结果如何，都处理完这个页面了
            except Exception as e:
                if not p.is_closed():
                    print(f"   警告: 检查页面 URL 时出错: {e}。尝试关闭此页。") # 中文
                    try: await p.close() # 关闭出错的页面
                    except: pass
                    
        if not found_page:
            print(f"-> 未找到合适的现有页面，正在打开新页面并导航到 {target_full_url}...") # 中文
            found_page = await temp_context.new_page()
            try:
                await found_page.goto(target_full_url, wait_until="domcontentloaded", timeout=90000)
                current_url = found_page.url
                print(f"-> 新页面导航尝试完成。当前 URL: {current_url}") # 中文
            except Exception as new_page_nav_err:
                print(f"❌ 错误: 导航新页面到 {target_full_url} 时失败: {new_page_nav_err}")
                await save_error_snapshot(f"init_new_page_nav_fail")
                raise RuntimeError(f"导航新页面失败: {new_page_nav_err}") from new_page_nav_err

        # --- 修改后的登录处理逻辑 ---
        if login_url_pattern in current_url:
            if launch_mode == 'headless':
                # 无头模式下，到达登录页面是致命错误
                print(f"❌ 错误: 无头模式启动后重定向到 Google 登录页面 ({current_url})。")
                auth_file_msg = f"使用的认证文件 '{os.path.basename(storage_state_path_to_use) if storage_state_path_to_use else '未知'}' 可能已过期或无效。"
                print(f"   {auth_file_msg}")
                print(f"   请使用 '--debug' 模式启动，保存新的认证文件到 '{SAVED_AUTH_DIR}'，然后将其移动到 '{ACTIVE_AUTH_DIR}'。")
                raise RuntimeError("无头模式认证失败，需要更新认证文件。")
            else: # 调试模式
                print(f"\n{'='*20} 需要操作 {'='*20}") # 中文
                print(f"   脚本检测到页面已重定向到 Google 登录页面:")
                print(f"   {current_url}")
                print(f"   请在 Camoufox 启动的浏览器窗口中完成 Google 登录。")
                print(f"   登录成功并进入 AI Studio (看到聊天界面) 后，回到此终端。")
                print('-'*60)
                
                # 使用 asyncio 在 executor 中运行 input，避免阻塞
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, input, "   完成登录后，请按 Enter 键继续...")
                
                print("   感谢操作！正在检查登录状态...")
                
                # 尝试等待页面导航到 AI Studio URL，增加超时时间
                check_login_success_url = f"**/{AI_STUDIO_URL_PATTERN}**"
                try:
                    print(f"   等待 URL 包含 '{AI_STUDIO_URL_PATTERN}' (最长等待 180 秒)...")
                    await found_page.wait_for_url(check_login_success_url, timeout=180000)
                    current_url = found_page.url
                    print(f"   登录后确认 URL: {current_url}") # 中文
                    if login_url_pattern in current_url:
                        raise RuntimeError("手动登录尝试后仍在登录页面。脚本无法继续。") # 中文
                    
                    print("   ✅ 登录成功！") # 中文
                    
                    # --- 询问是否保存状态 --- 
                    print('-'*60)
                    save_prompt = "   是否要将当前的浏览器认证状态保存到文件？ (y/N): "
                    should_save = await loop.run_in_executor(None, input, save_prompt)
                    
                    if should_save.lower() == 'y':
                        # 确保保存目录存在
                        if not os.path.exists(SAVED_AUTH_DIR):
                             print(f"   创建保存目录: {SAVED_AUTH_DIR}")
                             os.makedirs(SAVED_AUTH_DIR, exist_ok=True)
                        
                        default_filename = f"auth_state_{int(time.time())}.json"
                        filename_prompt = f"   请输入保存的文件名 (默认为: {default_filename}): "
                        save_filename = await loop.run_in_executor(None, input, filename_prompt)
                        if not save_filename:
                            save_filename = default_filename
                        if not save_filename.endswith(".json"):
                             save_filename += ".json"
                        
                        save_path = os.path.join(SAVED_AUTH_DIR, save_filename)
                        
                        try:
                            await temp_context.storage_state(path=save_path)
                            print(f"   ✅ 认证状态已成功保存到: {save_path}") # 中文
                            print(f"   提示: 您可以将此文件移动到 '{ACTIVE_AUTH_DIR}' 目录中，以便在 '--headless' 模式下自动使用。")
                        except Exception as save_err:
                            print(f"   ❌ 保存认证状态失败: {save_err}") # 中文
                    else:
                        print("   好的，不保存认证状态。")
                    print('-'*60)
                    # --- 结束询问 --- 
                    
                except Exception as wait_err:
                    last_known_url = found_page.url
                    print(f"   ❌ 等待 AI Studio URL 时出错或超时: {wait_err}") # 中文
                    print(f"   最后已知 URL: {last_known_url}")
                    await save_error_snapshot(f"init_login_wait_fail")
                    raise RuntimeError(f"登录提示后未能检测到 AI Studio URL。请确保您在浏览器中完成了登录并看到了 AI Studio 聊天界面。错误: {wait_err}") # 中文
        
        # 检查非登录重定向后的 URL 是否预期
        elif target_url_base not in current_url or "/prompts/" not in current_url:
            print(f"\n⚠️ 警告: 初始页面或导航后到达意外页面: {current_url}") # 中文
            if launch_mode == 'headless' and storage_state_path_to_use:
                 print(f"   无头模式使用的认证文件 '{os.path.basename(storage_state_path_to_use)}' 可能指向了错误的状态或已过期。")
            elif launch_mode == 'debug' and not storage_state_path_to_use:
                 print(f"   请检查浏览器是否已正确打开 AI Studio 对话页面 (例如 /prompts/new_chat)。")
            await save_error_snapshot(f"init_unexpected_page")
            raise RuntimeError(f"初始导航后出现意外页面: {current_url}。无法找到目标输入区域。") # 中文
            
        # --- 只有在确认 URL 是 AI Studio 对话页面后才继续 ---
        print(f"-> 确认当前位于 AI Studio 对话页面: {current_url}") # 调整日志
        await found_page.bring_to_front()
        print("-> 已尝试将页面置于前台。检查核心输入区...") # 中文
        
        # 等待核心输入区可见 (保留此检查)
        try:
             # 等待输入框的父容器可见可能更稳定
             input_wrapper_locator = found_page.locator('ms-prompt-input-wrapper')
             await expect_async(input_wrapper_locator).to_be_visible(timeout=35000) # 增加超时
             # 再确认一下 textarea 本身
             await expect_async(found_page.locator(INPUT_SELECTOR)).to_be_visible(timeout=10000)
             print("-> ✅ 核心输入区域可见。") # 中文
             page_instance = found_page
             is_page_ready = True
             print(f"✅ 页面逻辑初始化成功。") # 中文
        except Exception as input_visible_err:
             print(f"❌ 错误: 等待核心输入区域 ('{INPUT_SELECTOR}' 或其父容器) 可见时超时或失败。")
             print(f"   最后确认的 URL: {found_page.url}")
             print(f"   错误详情: {input_visible_err}")
             await save_error_snapshot(f"init_fail_input_timeout")
             raise RuntimeError(f"页面初始化失败：核心输入区域未在预期时间内变为可见。最后的 URL 是 {found_page.url}") from input_visible_err
             
    except RuntimeError as e:
        print(f"❌ 页面逻辑初始化失败 (RuntimeError): {e}") # 中文
        # 清理可能创建的 context
        if temp_context:
             try: await temp_context.close()
             except: pass
        raise # 重新抛出，以便 lifespan 捕获
    except Exception as e:
        print(f"❌ 页面逻辑初始化期间发生意外错误: {e}") # 中文
        if temp_context:
             try: await temp_context.close()
             except: pass
        await save_error_snapshot(f"init_unexpected_error")
        raise RuntimeError(f"页面初始化意外错误: {e}") from e

# --- Page Shutdown Logic --- (Translate print statements)
async def _close_page_logic():
    # ... (code unchanged) ...
    global page_instance, is_page_ready
    print("--- 运行页面逻辑关闭 --- ") # 中文
    page_instance = None
    is_page_ready = False
    print("页面逻辑状态已重置。") # 中文

# --- Lifespan context manager ---
@asynccontextmanager
async def lifespan(app_param: FastAPI):
    global playwright_manager, browser_instance, page_instance, worker_task # Add worker_task
    global is_playwright_ready, is_browser_connected, is_page_ready, is_initializing

    is_initializing = True
    print("\\n" + "="*60)
    # Update server name in startup message
    print(f"          🚀 AI Studio Proxy Server (Python/FastAPI - Queue Enabled) 🚀")
    print("="*60)
    print(f"FastAPI 生命周期: 启动中...") # 中文
    try:
        # Ensure auth directories exist
        os.makedirs(ACTIVE_AUTH_DIR, exist_ok=True)
        os.makedirs(SAVED_AUTH_DIR, exist_ok=True)
        print(f"   确保认证目录存在:")
        print(f"   - Active: {ACTIVE_AUTH_DIR}")
        print(f"   - Saved:  {SAVED_AUTH_DIR}")
        
        print(f"   启动 Playwright...") # 中文
        playwright_manager = await async_playwright().start()
        is_playwright_ready = True
        print(f"   ✅ Playwright 已启动。") # 中文

        ws_endpoint = os.environ.get('CAMOUFOX_WS_ENDPOINT')
        if not ws_endpoint:
             raise ValueError("未找到或环境变量 CAMOUFOX_WS_ENDPOINT 为空。请确保 launch_camoufox.py 脚本已设置此变量。") # 中文

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

        # !! 新增：启动队列 Worker !!
        if is_page_ready and is_browser_connected:
             print(f"   启动请求队列 Worker...") # 中文
             worker_task = asyncio.create_task(queue_worker()) # Create and store the worker task
             print(f"   ✅ 请求队列 Worker 已启动。") # 中文
        else:
             print(f"   ⚠️ 页面或浏览器未就绪，未启动请求队列 Worker。") # 中文
             # Ensure browser connection is closed if page init failed
             if browser_instance and browser_instance.is_connected():
                 try: await browser_instance.close()
                 except: pass
             raise RuntimeError("页面或浏览器初始化失败，无法启动 Worker。") # 中文

        print(f"✅ FastAPI 生命周期: 启动完成。") # 中文
        is_initializing = False
        yield # Application runs here

    except Exception as startup_err:
        print(f"❌ FastAPI 生命周期: 启动期间出错: {startup_err}") # 中文
        is_initializing = False
        # Add worker task cancellation to error handling
        if worker_task and not worker_task.done():
            worker_task.cancel()
        # Ensure browser connection is closed if startup fails at any point after connection
        if browser_instance and browser_instance.is_connected():
            try: await browser_instance.close()
            except: pass
        if playwright_manager:
            try: await playwright_manager.stop()
            except: pass
        # traceback.print_exc() # Optionally print full traceback
        # Reraise with a clearer message
        raise RuntimeError(f"应用程序启动失败: {startup_err}") from startup_err # 中文
    finally:
        is_initializing = False # Ensure this is false on normal exit too

        print(f"\\nFastAPI 生命周期: 关闭中...") # 中文

        # !! 新增：关闭队列 Worker !!
        if worker_task and not worker_task.done():
             print(f"   正在取消请求队列 Worker...") # 中文
             worker_task.cancel()
             try:
                  await worker_task # Wait for the worker to finish cancellation
                  print(f"   ✅ 请求队列 Worker 已停止。") # 中文
             except asyncio.CancelledError:
                  print(f"   ✅ 请求队列 Worker 已确认取消。") # 中文
             except Exception as wt_err:
                  print(f"   ❌ 等待 Worker 停止时出错: {wt_err}") # 中文
        else:
             print(f"   ⚠️ Worker 任务未运行或已完成。") # 中文

        await _close_page_logic() # Existing page close logic

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
    title="AI Studio Proxy Server (Python/FastAPI/Camoufox - Queue Enabled)",
    description="A proxy server to interact with Google AI Studio using Playwright and Camoufox, with request queueing.",
    version="0.3.0-py-queue-debugfix", # Updated version
    lifespan=lifespan # Use the updated lifespan context manager
)

# --- Serve Static HTML for Web UI --- (New Route)
@app.get("/", response_class=FileResponse)
async def read_index():
    # ... (code unchanged) ...
    index_html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if not os.path.exists(index_html_path):
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_html_path)

# --- API Endpoints --- (Translate print statements)
@app.get("/health")
async def health_check():
    # Check worker status safely
    is_worker_running = bool(worker_task and not worker_task.done())
    # Check core readiness
    is_core_ready = is_playwright_ready and is_browser_connected and is_page_ready
    status_val = "OK" if is_core_ready and is_worker_running else "Error"

    # Get queue size safely
    q_size = -1
    try:
         q_size = request_queue.qsize()
    except Exception:
         pass # Ignore error if queue not ready

    status = {
        "status": status_val,
        "message": "",
        "playwrightReady": is_playwright_ready,
        "browserConnected": is_browser_connected,
        "pageReady": is_page_ready,
        "initializing": is_initializing,
        "workerRunning": is_worker_running, # Add worker status
        "queueLength": q_size # Add queue length
    }
    if status_val == "OK":
        status["message"] = f"服务运行中，Playwright 活动，浏览器已连接，页面已初始化，Worker 运行中。队列长度: {q_size}。" # 中文
        return JSONResponse(content=status, status_code=200)
    else:
        reasons = []
        if not is_playwright_ready: reasons.append("Playwright 未初始化") # 中文
        if not is_browser_connected: reasons.append("浏览器断开或不可用") # 中文
        if not is_page_ready: reasons.append("目标页面未初始化或未就绪") # 中文
        if not is_worker_running: reasons.append("队列 Worker 未运行") # 中文
        if is_initializing: reasons.append("初始化当前正在进行中") # 中文
        status["message"] = f"服务不可用。问题: {(', '.join(reasons) if reasons else '未知')}. 队列长度: {q_size}." # 中文，添加空列表检查
        return JSONResponse(content=status, status_code=503)

@app.get("/v1/models")
async def list_models():
    # ... (code unchanged) ...
    print("[API] 收到 /v1/models 请求。") # 中文
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "camoufox-proxy",
                "permission": [],
                "root": MODEL_NAME,
                "parent": None,
            }
        ]
    }

# --- Helper: Detect Error ---
async def detect_and_extract_page_error(page: AsyncPage, req_id: str):
    # ... (code unchanged) ...
    """检查可见的错误/警告提示框并提取消息。"""
    error_toast_locator = page.locator(ERROR_TOAST_SELECTOR).last
    try:
        # Use a shorter timeout for quick checks
        await error_toast_locator.wait_for(state='visible', timeout=500)
        print(f"[{req_id}]    检测到错误/警告提示框元素。") # 中文
        message_locator = error_toast_locator.locator('span.content-text')
        error_message = await message_locator.text_content(timeout=500)
        if error_message:
             print(f"[{req_id}]    提取的错误消息: {error_message}") # 中文
             return error_message.strip()
        else:
             print(f"[{req_id}]    警告: 检测到提示框，但无法提取特定消息。") # 中文
             return "检测到错误提示框，但无法提取特定消息。" # 中文
    except PlaywrightAsyncError:
        return None # Not visible is the common case
    except Exception as e:
        print(f"[{req_id}]    警告: 检查页面错误时出错: {e}") # 中文
        return None

# --- Helper: Try Parse JSON ---
def try_parse_json(text: str, req_id: str):
    # ... (code unchanged) ...
    """Attempts to find and parse the outermost JSON object/array in text."""
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    start_index = -1
    end_index = -1
    first_brace = text.find('{')
    first_bracket = text.find('[')
    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        start_index = first_brace
        end_index = text.rfind('}')
    elif first_bracket != -1:
        start_index = first_bracket
        end_index = text.rfind(']')
    if start_index == -1 or end_index == -1 or end_index < start_index:
        return None
    json_text = text[start_index : end_index + 1]
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        return None

# --- Snapshot Helper --- (Translate logs)
async def save_error_snapshot(error_name: str = 'error'):
    # ... (code unchanged) ...
    """发生错误时保存屏幕截图和 HTML 快照。"""
    name_parts = error_name.split('_')
    req_id = name_parts[-1] if len(name_parts) > 1 and len(name_parts[-1]) == 7 else None
    base_error_name = error_name if not req_id else '_'.join(name_parts[:-1])
    log_prefix = f"[{req_id}]" if req_id else "[无请求ID]" # 中文
    
    # 使用 page_instance 全局变量
    page_to_snapshot = page_instance
    if not browser_instance or not browser_instance.is_connected() or not page_to_snapshot or page_to_snapshot.is_closed():
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
            await page_to_snapshot.screenshot(path=screenshot_path, full_page=True, timeout=15000)
            print(f"{log_prefix}   快照已保存到: {screenshot_path}") # 中文
        except Exception as ss_err:
            print(f"{log_prefix}   保存屏幕截图失败 ({base_error_name}): {ss_err}") # 中文
        try:
            content = await page_to_snapshot.content()
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"{log_prefix}   HTML 已保存到: {html_path}") # 中文
        except Exception as html_err:
            print(f"{log_prefix}   保存 HTML 失败 ({base_error_name}): {html_err}") # 中文
    except Exception as dir_err:
        print(f"{log_prefix}   创建错误目录或保存快照时出错: {dir_err}") # 中文


# --- 新增：队列 Worker 函数 ---
async def queue_worker():
    """后台任务，持续处理请求队列中的项目"""
    print("--- 队列 Worker 已启动 ---") # 中文
    while True:
        request_item = None
        result_future = None # Initialize future here
        req_id = "UNKNOWN" # Default req_id
        try:
            # 从队列中获取下一个请求项
            request_item = await request_queue.get()
            req_id = request_item["req_id"]
            request_data = request_item["request_data"]
            http_request = request_item["http_request"]
            result_future = request_item["result_future"] # Assign future

            print(f"[{req_id}] (Worker) 从队列中取出请求。", flush=True) # 中文

            # 检查客户端是否在进入处理锁之前断开连接
            if await http_request.is_disconnected():
                 print(f"[{req_id}] (Worker) 客户端在等待锁时断开连接。取消。", flush=True) # 中文
                 if result_future and not result_future.done():
                      result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端关闭了请求")) # 中文
                 request_queue.task_done()
                 continue # 获取下一个请求

            # 获取处理锁，确保只有一个请求在操作 Playwright
            print(f"[{req_id}] (Worker) 等待获取处理锁...", flush=True) # 中文
            async with processing_lock:
                print(f"[{req_id}] (Worker) 已获取处理锁。开始核心处理...", flush=True) # 中文
                # 再次检查连接状态，以防在获取锁期间断开
                if await http_request.is_disconnected():
                     print(f"[{req_id}] (Worker) 客户端在获取锁后、处理前断开连接。取消。", flush=True) # 中文
                     if result_future and not result_future.done():
                          result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端关闭了请求")) # 中文
                elif result_future and result_future.done(): # Check future before processing
                     print(f"[{req_id}] (Worker) 请求 Future 在处理开始前已完成/取消。跳过。", flush=True) # 中文
                elif result_future: # Ensure future exists
                    # 调用核心处理逻辑
                    await _process_request_from_queue(
                        req_id, request_data, http_request, result_future
                    )
                else:
                    print(f"[{req_id}] (Worker) 错误：Future 对象丢失。无法处理请求。", flush=True)
                    # Mark task as done even if future is lost
                    request_queue.task_done()
                    continue

            print(f"[{req_id}] (Worker) 处理完成，已释放锁。", flush=True) # 中文

        except asyncio.CancelledError:
             print("--- 队列 Worker 收到取消信号，正在退出 ---", flush=True) # 中文
             # 如果 worker 被取消，尝试取消当前正在处理的请求的 future
             if result_future and not result_future.done():
                  print(f"[{req_id}] (Worker) 取消当前处理请求的 Future...", flush=True)
                  result_future.set_exception(HTTPException(status_code=503, detail=f"[{req_id}] 服务器关闭中，请求被取消"))
             break # 退出循环
        except Exception as e:
             # Worker 自身的未捕获错误
             print(f"[Worker Error] Worker 循环中发生意外错误 (Req ID: {req_id}): {e}", flush=True) # 中文
             traceback.print_exc()
             # 尝试通知客户端（如果可能）
             if result_future and not result_future.done():
                  result_future.set_exception(HTTPException(status_code=500, detail=f"[{req_id}] Worker 内部错误: {e}")) # 中文
             # 避免 worker 因单个请求处理错误而崩溃，继续处理下一个
        finally:
             # Ensure task_done is called even if future was missing or error occurred before processing
             if request_item:
                  request_queue.task_done()

    print("--- 队列 Worker 已停止 ---", flush=True) # 中文


# --- 重构的核心聊天请求处理逻辑 (由 Worker 调用) ---
async def _process_request_from_queue(
    req_id: str,
    request: ChatCompletionRequest,
    http_request: Request, # Still needed for disconnect check
    result_future: Future
):
    """处理单个请求的核心逻辑，由队列 Worker 调用"""
    print(f"[{req_id}] (Worker) 开始处理来自队列的请求...", flush=True) # 中文
    is_streaming = request.stream
    page: Optional[AsyncPage] = None # Initialize page variable

    # 在开始重度操作前快速检查客户端是否已断开连接
    # This check is redundant if worker already checked, but keep as safeguard
    if await http_request.is_disconnected():
         print(f"[{req_id}] (Worker) 客户端在核心处理开始前已断开连接。设置 Future 异常。", flush=True) # 中文
         if not result_future.done():
              result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端关闭了请求")) # 中文
         return

    if not page_instance or page_instance.is_closed() or not is_page_ready:
        print(f"[{req_id}] (Worker) 错误: 页面无效 (is_closed={page_instance.is_closed() if page_instance else 'N/A'}, is_page_ready={is_page_ready}).", flush=True) # 中文
        if not result_future.done():
            result_future.set_exception(HTTPException(status_code=503, detail=f"[{req_id}] AI Studio 页面丢失或未就绪。请检查服务器状态。", headers={"Retry-After": "30"})) # 中文
        return

    page = page_instance # Assign global page instance

    # --- Client Disconnect Handling within processing ---
    client_disconnected_event = Event() # Use asyncio.Event
    disconnect_check_task = None
    # Locators needed for stop button click
    input_field_locator = page.locator(INPUT_SELECTOR)
    submit_button_locator = page.locator(SUBMIT_BUTTON_SELECTOR)


    async def check_disconnect_periodically():
         """Periodically check if the client has disconnected."""
         while not client_disconnected_event.is_set():
              try:
                   # Check disconnect first
                   is_disconnected = await http_request.is_disconnected()
                   if is_disconnected:
                        print(f"[{req_id}] (Worker Disco Check Task) 客户端断开连接。设置事件。", flush=True) # 中文
                        client_disconnected_event.set()
                        # --- Add Stop Button Click Logic ---
                        print(f"[{req_id}] (Worker Disco Check Task) 尝试点击停止按钮...")
                        try:
                            # Check if button is enabled (indicating generation might be in progress)
                            # Use a shorter timeout for this check
                            if await submit_button_locator.is_enabled(timeout=1500):
                                # Check if input field is empty (heuristic for stopping generation vs starting new)
                                input_value = await input_field_locator.input_value(timeout=1500)
                                if input_value == '':
                                    print(f"[{req_id}] (Worker Disco Check Task)   按钮启用且输入为空，点击停止...")
                                    await submit_button_locator.click(timeout=3000, force=True) # Force click might be needed
                                    print(f"[{req_id}] (Worker Disco Check Task)   停止按钮点击已尝试。")
                                else:
                                    print(f"[{req_id}] (Worker Disco Check Task)   按钮启用但输入非空，不点击停止。")
                            else:
                                print(f"[{req_id}] (Worker Disco Check Task)   按钮已禁用，无需点击停止。")
                        except Exception as click_err:
                            print(f"[{req_id}] (Worker Disco Check Task) 尝试点击停止按钮时出错: {click_err}", flush=True)
                            # Don't stop the disconnect process for this error
                        # --- End Stop Button Click Logic ---
                        # Set exception on future *after* attempting stop click
                        if not result_future.done():
                             print(f"[{req_id}] (Worker Disco Check Task) 设置 Future 异常 (499)。")
                             result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端在处理期间关闭了请求"))
                        break # Exit loop once disconnected

                   await asyncio.sleep(1.0) # Check every second
              except asyncio.CancelledError:
                   print(f"[{req_id}] Disconnect checker task cancelled.") # Debug
                   break # Task was cancelled
              except Exception as e:
                   print(f"[{req_id}] (Worker) 内部断开检查任务出错: {e}", flush=True) # 中文
                   client_disconnected_event.set() # Signal disconnect on error too
                   # Also set exception on future if checker task fails unexpectedly
                   if not result_future.done():
                       result_future.set_exception(HTTPException(status_code=500, detail=f"[{req_id}] Internal disconnect checker error: {e}"))
                   break

    disconnect_check_task = asyncio.create_task(check_disconnect_periodically())

    # Helper to check disconnect event easily
    def check_client_disconnected(msg_prefix=""):
        if client_disconnected_event.is_set():
            print(f"[{req_id}] {msg_prefix}检测到客户端断开连接事件。", flush=True)
            # Exception should have been set by the checker task, raise internal exception to stop processing
            raise ClientDisconnectedError(f"[{req_id}] Client disconnected event set.")
        return False

    # Helper for interruptible sleep
    async def interruptible_sleep(duration):
        try:
            # Wait for sleep or disconnect event, whichever happens first
            sleep_task = asyncio.create_task(asyncio.sleep(duration))
            disconnect_wait_task = asyncio.create_task(client_disconnected_event.wait())
            done, pending = await asyncio.wait(
                [sleep_task, disconnect_wait_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            # Cancel whichever task is still pending
            for task in pending:
                task.cancel()
                try: await task # Suppress CancelledError
                except asyncio.CancelledError: pass
            # Check if disconnect happened
            check_client_disconnected(f"Sleep interrupted by disconnect ({duration}s): ")
        except asyncio.CancelledError:
            # If sleep itself was cancelled (e.g., by main task cancellation)
            check_client_disconnected("Sleep cancelled: ")
            raise # Re-raise CancelledError

    # Helper for interruptible Playwright actions with timeout
    async def interruptible_wait_for(awaitable, timeout):
        try:
            # Wait for awaitable or disconnect event
            awaitable_task = asyncio.create_task(awaitable)
            disconnect_wait_task = asyncio.create_task(client_disconnected_event.wait())
            done, pending = await asyncio.wait(
                [awaitable_task, disconnect_wait_task],
                timeout=timeout # Use provided timeout for the wait
            )

            for task in pending:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass

            # Check results
            if awaitable_task in done:
                 # Check for exceptions from the awaitable task
                 return await awaitable_task # Return result or raise exception
            elif disconnect_wait_task in done:
                 check_client_disconnected(f"Wait cancelled by disconnect (timeout={timeout}s): ")
            else:
                 # Timeout occurred
                 print(f"[{req_id}] (Worker) 操作超时 ({timeout}s)。", flush=True)
                 raise asyncio.TimeoutError(f"Operation timed out after {timeout}s")

        except asyncio.CancelledError:
             check_client_disconnected("Wait cancelled: ")
             raise

    try:
        # 1. Validation
        try:
            validation_result = validate_chat_request(request.messages, req_id)
            user_prompt = validation_result["userPrompt"]
            system_prompt = validation_result["systemPrompt"]
            if user_prompt is None:
                raise ValueError("处理后的用户提示意外为 None。") # 中文
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"[{req_id}] 无效请求: {e}")

        print(f"[{req_id}] (Worker) 用户提示 (已验证, 长度={len(user_prompt)}): '{user_prompt[:80]}...'", flush=True) # 中文
        if system_prompt:
            print(f"[{req_id}] (Worker) 系统提示 (已验证, 长度={len(system_prompt)}): '{system_prompt[:80]}...'", flush=True) # 中文

        # 2. Prepare Prompt
        if is_streaming:
            prepared_prompt = prepare_ai_studio_prompt_stream(user_prompt, system_prompt)
            print(f"[{req_id}] (Worker) 准备好的流式提示 (开始): '{prepared_prompt[:150]}...'", flush=True) # 中文
        else:
            prepared_prompt = prepare_ai_studio_prompt(user_prompt, system_prompt)
            print(f"[{req_id}] (Worker) 准备好的非流式提示 (开始): '{prepared_prompt[:150]}...'", flush=True) # 中文

        check_client_disconnected("Before Clear Chat: ")

        # --- START: Clear Chat Logic --- (Use interruptible helpers)
        current_url = page.url
        is_new_chat_url = current_url.endswith("/new_chat")

        if is_new_chat_url:
            print(f"[{req_id}] (Worker) 当前在 /new_chat 页面，跳过清空聊天记录操作。", flush=True) # 中文
        else:
            is_likely_new_conversation_turn = len(request.messages) == 1 or \
                                            (len(request.messages) == 2 and any(m.role == 'system' for m in request.messages))

            if is_likely_new_conversation_turn and CLEAR_CHAT_BUTTON_SELECTOR and CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR:
                print(f"[{req_id}] (Worker) 尝试清空聊天记录... (新对话轮次)", flush=True) # 中文
                try:
                    clear_button = page.locator(CLEAR_CHAT_BUTTON_SELECTOR)
                    confirm_button = page.locator(CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR)

                    # --- 改进的检查逻辑 ---
                    print(f"[{req_id}] (Worker)   - 检查'清除聊天'按钮状态...", flush=True) # 中文
                    await interruptible_wait_for(expect_async(clear_button).to_be_visible(timeout=8000), timeout=8.5)
                    
                    is_disabled = await clear_button.is_disabled()
                    
                    if is_disabled:
                        print(f"[{req_id}] (Worker)   - '清除聊天'按钮当前被禁用，可能无需清除。跳过清空操作。", flush=True) # 中文
                    else:
                        # 按钮可见且未被禁用，继续尝试点击
                        print(f"[{req_id}] (Worker)   - '清除聊天'按钮可见且可用，尝试点击...", flush=True) # 中文
                        # await interruptible_wait_for(expect_async(clear_button).to_be_enabled(timeout=5000), timeout=5.5) # 不再需要重复检查 enabled
                        await interruptible_wait_for(clear_button.click(timeout=5000), timeout=5.5)
                        print(f"[{req_id}] (Worker)   - '清除聊天'按钮已点击。", flush=True) # 中文

                        print(f"[{req_id}] (Worker)   - 点击'继续'按钮...", flush=True) # 中文
                        await interruptible_wait_for(expect_async(confirm_button).to_be_visible(timeout=5000), timeout=5.5)
                        await interruptible_wait_for(confirm_button.click(timeout=5000), timeout=5.5)
                        print(f"[{req_id}] (Worker)   - '继续'按钮已点击。验证清空效果...", flush=True) # 中文
                        check_client_disconnected("After Clear Confirm Click: ")

                        # --- Verify clear (保持不变) ---
                        verify_start_time = time.time() * 1000
                        cleared = False
                        while time.time() * 1000 - verify_start_time < CLEAR_CHAT_VERIFY_TIMEOUT_MS:
                            check_client_disconnected("Verify Clear Loop: ")
                            model_turns = page.locator(RESPONSE_CONTAINER_SELECTOR)
                            # Use interruptible_wait_for for count, shorter timeout
                            count = await interruptible_wait_for(model_turns.count(), timeout=1.0)
                            if count == 0:
                                print(f"[{req_id}] (Worker)   ✅ 验证成功: 清空完成 (耗时 {int(time.time() * 1000 - verify_start_time)}ms)。", flush=True) # 中文
                                cleared = True
                                break
                            await interruptible_sleep(CLEAR_CHAT_VERIFY_INTERVAL_MS / 1000)

                        if not cleared and not client_disconnected_event.is_set():
                            print(f"[{req_id}] (Worker)   ⚠️ 验证超时: 上下文可能未完全清空。", flush=True) # 中文
                            await save_error_snapshot(f"clear_chat_verify_fail_{req_id}")
                    # --- 结束改进的检查逻辑 ---

                except PlaywrightAsyncError as clear_err:
                    # 保持现有的 Playwright 错误处理（例如超时）
                    if "timeout" in clear_err.message.lower():
                        print(f"[{req_id}] (Worker) ⚠️ 清空聊天Playwright操作超时。继续执行。", flush=True)
                        await save_error_snapshot(f"clear_chat_timeout_pw_{req_id}")
                    else:
                        print(f"[{req_id}] (Worker) ⚠️ 清空聊天Playwright错误: {clear_err.message.split('\n')[0]}. 继续执行。", flush=True) # 中文
                        await save_error_snapshot(f"clear_chat_fail_or_verify_{req_id}")
                except asyncio.TimeoutError:
                    # 保持现有的 asyncio 超时处理
                    print(f"[{req_id}] (Worker) ⚠️ 清空聊天asyncio操作超时。继续执行。", flush=True)
                    await save_error_snapshot(f"clear_chat_timeout_asyncio_{req_id}")
                except Exception as general_clear_err:
                    # 捕获其他意外错误，但不再因为按钮禁用而进入这里
                    if not isinstance(general_clear_err, HTTPException) and not isinstance(general_clear_err, ClientDisconnectedError):
                        print(f"[{req_id}] (Worker) ⚠️ 清空聊天未知错误: {general_clear_err}. 继续执行。", flush=True) # 中文
                        traceback.print_exc()
                        await save_error_snapshot(f"clear_chat_unknown_err_{req_id}")

            elif is_likely_new_conversation_turn:
                print(f"[{req_id}] (Worker) 可能是新对话轮次，但未配置清空选择器，无法自动重置。", flush=True) # 中文
        # --- END: Clear Chat Logic ---

        check_client_disconnected("Before Submit: ")

        # 3. Interact and Submit (Use interruptible helpers)
        print(f"[{req_id}] (Worker) 填充提示并提交...", flush=True) # 中文
        input_field = page.locator(INPUT_SELECTOR)
        submit_button = page.locator(SUBMIT_BUTTON_SELECTOR)

        await interruptible_wait_for(expect_async(input_field).to_be_visible(timeout=10000), timeout=10.5)
        await interruptible_wait_for(input_field.fill(prepared_prompt, timeout=60000), timeout=60.5)
        await interruptible_wait_for(expect_async(submit_button).to_be_enabled(timeout=10000), timeout=10.5)

        print(f"[{req_id}] (Worker) 短暂等待 UI 稳定...", flush=True) # 中文
        await interruptible_sleep(0.2)

        # --- Try submitting with shortcut ---
        submitted_successfully = False
        shortcut_key = "Meta" if platform.system() == "Darwin" else "Control"
        shortcut_name = "Command" if shortcut_key == "Meta" else "Control"
        try:
            print(f"[{req_id}] (Worker) 尝试 {shortcut_name}+Enter 提交...", flush=True) # 中文
            await interruptible_wait_for(input_field.focus(timeout=5000), timeout=5.5)
            # Keyboard press is usually fast, less need for interruptible_wait_for unless issues arise
            await page.keyboard.press(f'{shortcut_key}+Enter')
            print(f"[{req_id}] (Worker) {shortcut_name}+Enter 已发送。", flush=True) # 中文
            submitted_successfully = True
        except PlaywrightAsyncError as key_press_error:
            print(f"[{req_id}] (Worker) 警告: {shortcut_name}+Enter 提交出错: {key_press_error.message.split('\\n')[0]}", flush=True) # 中文
        except asyncio.TimeoutError:
            print(f"[{req_id}] (Worker) 警告: {shortcut_name}+Enter 提交超时。", flush=True)
        check_client_disconnected("After Shortcut Attempt: ")

        # --- Fallback to clicking ---
        if not submitted_successfully:
            print(f"[{req_id}] (Worker) 回退到模拟点击提交按钮...", flush=True) # 中文
            try:
                await interruptible_wait_for(submit_button.scroll_into_view_if_needed(timeout=5000), timeout=5.5)
            except Exception as scroll_err:
                print(f"[{req_id}] (Worker) 警告: 滚动提交按钮失败: {scroll_err}", flush=True) # 中文
            check_client_disconnected("After Scroll Fallback: ")
            try:
                await interruptible_wait_for(submit_button.click(timeout=10000, force=True), timeout=10.5)
                await interruptible_wait_for(expect_async(input_field).to_have_value('', timeout=3000), timeout=3.5)
                print(f"[{req_id}] (Worker) 模拟点击提交成功。", flush=True) # 中文
                submitted_successfully = True
            except PlaywrightAsyncError as click_error:
                print(f"[{req_id}] (Worker) ❌ 错误: 模拟点击提交按钮失败: {click_error.message.split('\\n')[0]}", flush=True) # 中文
                await save_error_snapshot(f"submit_fallback_click_fail_{req_id}")
                if not submitted_successfully: raise click_error
            except asyncio.TimeoutError:
                print(f"[{req_id}] (Worker) ❌ 错误: 模拟点击提交或检查输入框超时。", flush=True)
                if not submitted_successfully: raise PlaywrightAsyncError("Submit fallback click or input clear check timed out")
        check_client_disconnected("After Submit Logic: ")

        # --- Add Delay Post-Submission ---
        print(f"[{req_id}] (Worker) 提交后等待 1 秒...", flush=True) # 中文
        await interruptible_sleep(1.0)

        # 4. Locate Response Element (Use interruptible helpers)
        print(f"[{req_id}] (Worker) 定位响应容器...", flush=True) # 中文
        response_container = page.locator(RESPONSE_CONTAINER_SELECTOR).last
        try:
            await interruptible_wait_for(expect_async(response_container).to_be_attached(timeout=20000), timeout=20.5)
            print(f"[{req_id}] (Worker) 响应容器已定位。定位内部文本节点...", flush=True) # 中文
            response_element = response_container.locator(RESPONSE_TEXT_SELECTOR)
            await interruptible_wait_for(expect_async(response_element).to_be_attached(timeout=5000), timeout=5.5)
            print(f"[{req_id}] (Worker) 响应文本节点已定位。", flush=True) # 中文
        except PlaywrightAsyncError as locate_err:
            print(f"[{req_id}] (Worker) ❌ 定位响应元素 Playwright 错误: {locate_err}", flush=True) # 中文
            await save_error_snapshot(f"response_locate_error_{req_id}")
            raise locate_err
        except asyncio.TimeoutError:
            print(f"[{req_id}] (Worker) ❌ 定位响应元素超时。", flush=True)
            await save_error_snapshot(f"response_locate_timeout_{req_id}")
            raise PlaywrightAsyncError("Locating response element timed out")
        check_client_disconnected("After Locate Response: ")

        # 5. Handle Response (Streaming or Non-streaming)
        if is_streaming:
            print(f"[{req_id}] (Worker) 处理 SSE 流...", flush=True) # 中文

            async def stream_generator() -> AsyncGenerator[str, None]:
                # This generator runs independently once started by the endpoint handler.
                # It needs its *own* check for the disconnect event passed from the parent.
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
                last_scroll_time = 0
                scroll_interval_ms = 3000

                try:
                    while time.time() * 1000 - start_time < RESPONSE_COMPLETION_TIMEOUT:
                        if client_disconnected_event.is_set(): # Check event directly inside generator
                            print(f"[{req_id}] (Worker Stream Gen) 检测到断开连接，停止。", flush=True)
                            break

                        current_loop_time_ms = time.time() * 1000
                        loop_start_time = time.time() * 1000
                        loop_counter += 1

                        # --- Periodic Scroll --- 
                        if current_loop_time_ms - last_scroll_time > scroll_interval_ms:
                            try:
                                # Use regular await here, timeout is less critical
                                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                                last_scroll_time = current_loop_time_ms
                            except Exception as scroll_e:
                                print(f"[{req_id}] (Worker Stream Gen) 滚动失败: {scroll_e}", flush=True)
                        if client_disconnected_event.is_set(): break

                        # --- Periodic Error Check ---
                        if loop_counter % 10 == 0:
                            page_err_stream_periodic = await detect_and_extract_page_error(page, req_id)
                            if page_err_stream_periodic:
                                print(f"[{req_id}] (Worker Stream Gen) ❌ 错误: {page_err_stream_periodic}", flush=True) # 中文
                                await save_error_snapshot(f"page_error_stream_periodic_{req_id}")
                                yield generate_sse_error_chunk(f"AI Studio 错误: {page_err_stream_periodic}", req_id, "upstream_error") # 中文
                                yield "data: [DONE]\\n\\n"
                                return
                        if client_disconnected_event.is_set(): break

                        # --- Get Text Content --- (Short timeout, handle failure)
                        try:
                            # Make this faster, rely on loop for retries
                            current_raw_text = await asyncio.wait_for(get_raw_text_content(response_element, last_raw_text, req_id), timeout=1.5)
                        except asyncio.TimeoutError:
                            print(f"[{req_id}] (Worker Stream Gen) 获取文本超时，使用上次文本。", flush=True)
                            current_raw_text = last_raw_text
                        except Exception as getTextErr:
                            print(f"[{req_id}] (Worker Stream Gen) 获取文本失败: {getTextErr}", flush=True)
                            current_raw_text = last_raw_text # Use previous on error
                        if client_disconnected_event.is_set(): break

                        # --- Process Text Change and Send Delta ---
                        text_changed = current_raw_text != last_raw_text
                        if text_changed:
                            last_text_change_timestamp = time.time() * 1000
                            potential_new_delta = ""
                            current_content_after_marker = ""
                            marker_index = current_raw_text.find(start_marker)
                            if marker_index != -1:
                                if not response_started:
                                    print(f"[{req_id}] (Worker Stream Gen) 找到起始标记。", flush=True) # 中文
                                    response_started = True
                                current_content_after_marker = current_raw_text[marker_index + len(start_marker):]
                                potential_new_delta = current_content_after_marker[len(last_sent_response_content):]
                            elif response_started:
                                 potential_new_delta = ""
                                 print(f"[{req_id}] (Worker Stream Gen) 警告: 起始标记消失。", flush=True) # 中文

                            if potential_new_delta:
                                yield generate_sse_chunk(potential_new_delta, req_id, MODEL_NAME)
                                last_sent_response_content += potential_new_delta
                            last_raw_text = current_raw_text
                        if client_disconnected_event.is_set(): break

                        # --- Check Spinner Status ---
                        if not spinner_disappeared:
                             try:
                                 await expect_async(spinner_locator).to_be_hidden(timeout=0.1)
                                 spinner_disappeared = True
                                 print(f"[{req_id}] (Worker Stream Gen) Spinner 已隐藏。", flush=True) # 中文
                             except (AssertionError, PlaywrightAsyncError): pass
                        if client_disconnected_event.is_set(): break

                        # --- Silence Check ---
                        is_silent = spinner_disappeared and (time.time() * 1000 - last_text_change_timestamp > SILENCE_TIMEOUT_MS)
                        if is_silent:
                            print(f"[{req_id}] (Worker Stream Gen) 检测到静默，完成流。", flush=True) # 中文
                            stream_finished_naturally = True
                            break

                        # --- Interruptible Sleep ---
                        loop_duration = time.time() * 1000 - loop_start_time
                        wait_time = max(0, POLLING_INTERVAL_STREAM - loop_duration) / 1000
                        # Use simple sleep inside generator loop, rely on event check
                        await asyncio.sleep(wait_time)
                        if client_disconnected_event.is_set(): break

                    # --- End of while loop ---

                    if client_disconnected_event.is_set():
                         print(f"[{req_id}] (Worker Stream Gen) 流处理因客户端断开而中止。", flush=True) # 中文
                         yield "data: [DONE]\\n\\n"
                         return

                    page_err_stream_final = await detect_and_extract_page_error(page, req_id)
                    if page_err_stream_final:
                        print(f"[{req_id}] (Worker Stream Gen) ❌ 完成前错误: {page_err_stream_final}", flush=True) # 中文
                        await save_error_snapshot(f"page_error_stream_final_{req_id}")
                        yield generate_sse_error_chunk(f"AI Studio 错误: {page_err_stream_final}", req_id, "upstream_error") # 中文
                    elif stream_finished_naturally:
                        print(f"[{req_id}] (Worker Stream Gen) 流自然结束，最终内容检查...", flush=True) # 中文
                        final_raw_text = await get_raw_text_content(response_element, last_raw_text, req_id)
                        final_content_after_marker = ""
                        final_delta = ""
                        final_marker_index = final_raw_text.find(start_marker)
                        if final_marker_index != -1:
                             final_content_after_marker = final_raw_text[final_marker_index + len(start_marker):]
                             final_delta = final_content_after_marker[len(last_sent_response_content):]
                        elif response_started:
                             print(f"[{req_id}] (Worker Stream Gen) 警告: 最终检查时起始标记消失。", flush=True) # 中文
                        if final_delta:
                             print(f"[{req_id}] (Worker Stream Gen) 发送最终增量 (长度: {len(final_delta)})", flush=True) # 中文
                             yield generate_sse_chunk(final_delta, req_id, MODEL_NAME)
                        else:
                             print(f"[{req_id}] (Worker Stream Gen) 最终检查无新内容。", flush=True) # 中文
                        yield generate_sse_stop_chunk(req_id, MODEL_NAME)
                        print(f"[{req_id}] (Worker Stream Gen) ✅ 流自然完成。", flush=True) # 中文
                    else: # Timeout case
                        print(f"[{req_id}] (Worker Stream Gen) ⚠️ 流超时。", flush=True) # 中文
                        await save_error_snapshot(f"streaming_timeout_{req_id}")
                        yield generate_sse_error_chunk("流处理在服务器上超时。", req_id) # 中文
                        yield generate_sse_stop_chunk(req_id, MODEL_NAME, "timeout")

                    yield "data: [DONE]\\n\\n"

                except asyncio.CancelledError:
                    print(f"[{req_id}] (Worker Stream Gen) 流生成器被取消。", flush=True) # 中文
                    # Don't yield anything if cancelled? Or send special error?
                    # Yielding DONE might be confusing. Let the endpoint handle 499.
                except Exception as e:
                    print(f"[{req_id}] (Worker Stream Gen) ❌ 流式生成意外错误: {e}", flush=True) # 中文
                    await save_error_snapshot(f"streaming_error_{req_id}")
                    traceback.print_exc()
                    # Don't set future exception here, yield error chunk
                    try:
                        yield generate_sse_error_chunk(f"流式处理期间服务器错误: {e}", req_id) # 中文
                        yield "data: [DONE]\\n\\n"
                    except Exception as yield_err:
                         print(f"[{req_id}] (Worker Stream Gen) 尝试 yield 错误块时出错: {yield_err}", flush=True) # 中文
                         # If yielding fails, NOW set exception on the future
                         if not result_future.done():
                              result_future.set_exception(HTTPException(status_code=500, detail=f"[{req_id}] Stream generation error and yield failed: {e}"))

            # Set the generator function itself as the result
            if not result_future.done():
                result_future.set_result(stream_generator)
            else:
                 print(f"[{req_id}] (Worker) Future 已完成/取消，无法设置流生成器结果。", flush=True)


        else: # Non-streaming
            print(f"[{req_id}] (Worker) 处理非流式响应...", flush=True) # 中文
            start_time_ns = time.time()
            final_state_reached = False
            final_state_check_initiated = False
            spinner_locator = page.locator(LOADING_SPINNER_SELECTOR)
            input_field = page.locator(INPUT_SELECTOR)
            submit_button = page.locator(SUBMIT_BUTTON_SELECTOR)
            last_scroll_time_ns = 0
            scroll_interval_ms_ns = 3000

            while time.time() - start_time_ns < RESPONSE_COMPLETION_TIMEOUT / 1000:
                check_client_disconnected("NonStream Loop Start: ")

                # --- Periodic Scroll --- 
                current_loop_time_ms_ns = time.time() * 1000
                if current_loop_time_ms_ns - last_scroll_time_ns > scroll_interval_ms_ns:
                    try:
                        await interruptible_wait_for(page.evaluate('window.scrollTo(0, document.body.scrollHeight)'), timeout=1.0)
                        last_scroll_time_ns = current_loop_time_ms_ns
                    except Exception as scroll_e:
                         print(f"[{req_id}] (Worker NonStream) 滚动失败: {scroll_e}", flush=True)
                check_client_disconnected("NonStream After Scroll: ")

                # --- Check Final State Conditions --- (Use faster checks)
                spinner_hidden = False
                input_empty = False
                button_disabled = False
                try:
                    await expect_async(spinner_locator).to_be_hidden(timeout=0.1)
                    spinner_hidden = True
                except (AssertionError, PlaywrightAsyncError): pass
                check_client_disconnected("NonStream After Spinner Check: ")

                if spinner_hidden:
                    try:
                        await expect_async(input_field).to_have_value('', timeout=0.1)
                        input_empty = True
                    except (AssertionError, PlaywrightAsyncError): pass
                    check_client_disconnected("NonStream After Input Check: ")
                    if input_empty:
                        try:
                            await expect_async(submit_button).to_be_disabled(timeout=0.1)
                            button_disabled = True
                        except (AssertionError, PlaywrightAsyncError): pass
                check_client_disconnected("NonStream After State Checks: ")

                # --- Confirm Final State and Text Stability ---
                if spinner_hidden and input_empty and button_disabled:
                    if not final_state_check_initiated:
                        final_state_check_initiated = True
                        print(f"[{req_id}] (Worker NonStream) 检测到潜在最终状态。等待 {POST_COMPLETION_BUFFER}ms...", flush=True) # 中文
                        await interruptible_sleep(POST_COMPLETION_BUFFER / 1000)

                        print(f"[{req_id}] (Worker NonStream) 确认等待结束。严格重检...", flush=True) # 中文
                        try:
                            await interruptible_wait_for(expect_async(spinner_locator).to_be_hidden(timeout=0.5), timeout=0.6)
                            await interruptible_wait_for(expect_async(input_field).to_have_value('', timeout=0.5), timeout=0.6)
                            await interruptible_wait_for(expect_async(submit_button).to_be_disabled(timeout=0.5), timeout=0.6)
                            print(f"[{req_id}] (Worker NonStream) 状态确认。检查文本稳定性...", flush=True) # 中文

                            text_stable = False
                            silence_check_start_time = time.time()
                            last_check_text = await interruptible_wait_for(get_raw_text_content(response_element, '', req_id), timeout=3.0)

                            while time.time() - silence_check_start_time < SILENCE_TIMEOUT_MS / 1000:
                                await interruptible_sleep(POLLING_INTERVAL / 1000)
                                current_check_text = await interruptible_wait_for(get_raw_text_content(response_element, last_check_text, req_id), timeout=3.0)

                                if current_check_text == last_check_text:
                                    if time.time() - silence_check_start_time >= SILENCE_TIMEOUT_MS / 1000:
                                         print(f"[{req_id}] (Worker NonStream) 文本稳定。", flush=True) # 中文
                                         text_stable = True
                                         break
                                else:
                                    print(f"[{req_id}] (Worker NonStream) (静默检查) 文本更改。", flush=True) # 中文
                                    silence_check_start_time = time.time()
                                    last_check_text = current_check_text

                            check_client_disconnected("NonStream After Silence Check: ")
                            if text_stable:
                                final_state_reached = True
                                break # Exit outer loop
                            else:
                                print(f"[{req_id}] (Worker NonStream) ⚠️ 文本静默检查超时。", flush=True) # 中文
                                final_state_reached = True # Assume complete on timeout
                                break # Exit outer loop

                        except PlaywrightAsyncError as recheck_error:
                            print(f"[{req_id}] (Worker NonStream) 状态确认期间变化 ({recheck_error})。", flush=True) # 中文
                            final_state_check_initiated = False
                        except asyncio.TimeoutError:
                            print(f"[{req_id}] (Worker NonStream) 状态确认或文本稳定性检查超时。", flush=True)
                            final_state_check_initiated = False
                        # No need to catch general Exception here, let main handler do it
                    # else: already initiated
                else: # Conditions not met
                    if final_state_check_initiated:
                         print(f"[{req_id}] (Worker NonStream) 最终状态条件不再满足。", flush=True) # 中文
                         final_state_check_initiated = False
                    await interruptible_sleep(POLLING_INTERVAL * 2 / 1000) # Longer sleep if not confirming
                check_client_disconnected("NonStream Loop End: ")

            # --- End of while loop ---
            check_client_disconnected("NonStream After Loop: ")

            # --- Final Error Check and Content Retrieval ---
            print(f"[{req_id}] (Worker NonStream) 最终解析前检查页面错误...", flush=True) # 中文
            page_err_nonstream = await detect_and_extract_page_error(page, req_id)
            if page_err_nonstream:
                 print(f"[{req_id}] (Worker NonStream) ❌ 错误: {page_err_nonstream}", flush=True) # 中文
                 await save_error_snapshot(f"page_error_nonstream_{req_id}")
                 raise HTTPException(status_code=502, detail=f"[{req_id}] AI Studio 错误: {page_err_nonstream}") # 中文

            if not final_state_reached:
                 print(f"[{req_id}] (Worker NonStream) ⚠️ 等待最终状态超时。", flush=True) # 中文
                 await save_error_snapshot(f"nonstream_final_state_timeout_{req_id}")
            else:
                 print(f"[{req_id}] (Worker NonStream) ✅ 最终状态到达。", flush=True) # 中文

            # --- Get Final Content ---
            print(f"[{req_id}] (Worker NonStream) 获取并解析最终内容...", flush=True)
            final_content_for_user = ""
            try:
                 final_raw_text = await interruptible_wait_for(get_raw_text_content(response_element, '', req_id), timeout=5.0)
                 print(f"[{req_id}] (Worker NonStream) 最终原始文本 (长度={len(final_raw_text)}): '{final_raw_text[:100]}...'", flush=True) # 中文

                 if not final_raw_text or not final_raw_text.strip():
                     print(f"[{req_id}] (Worker NonStream) 警告: 原始文本为空。", flush=True) # 中文
                     final_content_for_user = ""
                 else:
                    parsed_json = try_parse_json(final_raw_text, req_id)
                    ai_response_text_from_json = None
                    if parsed_json:
                         if isinstance(parsed_json.get("response"), str):
                              ai_response_text_from_json = parsed_json["response"]
                         else:
                             try: ai_response_text_from_json = json.dumps(parsed_json)
                             except: ai_response_text_from_json = final_raw_text
                    else:
                        print(f"[{req_id}] (Worker NonStream) 警告: 无法解析 JSON。", flush=True) # 中文
                        ai_response_text_from_json = final_raw_text
                    
                    start_marker = '<<<START_RESPONSE>>>'
                    if ai_response_text_from_json and ai_response_text_from_json.startswith(start_marker):
                        final_content_for_user = ai_response_text_from_json[len(start_marker):]
                    elif ai_response_text_from_json:
                        final_content_for_user = ai_response_text_from_json
                        print(f"[{req_id}] (Worker NonStream) 警告: 未找到起始标记。", flush=True) # 中文
                    else: final_content_for_user = ""

            except asyncio.TimeoutError:
                 print(f"[{req_id}] (Worker NonStream) ❌ 获取最终内容超时。", flush=True)
                 await save_error_snapshot(f"get_final_content_timeout_{req_id}")
                 raise HTTPException(status_code=504, detail=f"[{req_id}] 获取最终响应超时")
            except Exception as e:
                # Avoid raising another HTTPException if already disconnected
                check_client_disconnected("NonStream Get Final Content Error Check: ")
                print(f"[{req_id}] (Worker NonStream) ❌ 获取/解析最终内容出错: {e}", flush=True) # 中文
                await save_error_snapshot(f"get_final_content_error_{req_id}")
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=f"[{req_id}] 处理最终响应时出错: {e}") # 中文

            # --- Build and Set Result ---
            response_payload = {
                "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": MODEL_NAME,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": final_content_for_user}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            if not result_future.done():
                result_future.set_result(response_payload)
            else:
                print(f"[{req_id}] (Worker) Future 已完成/取消，无法设置非流式结果。", flush=True)
            print(f"[{req_id}] (Worker NonStream) ✅ 非流式处理完成。", flush=True) # 中文

    # --- Exception Handling for _process_request_from_queue ---
    except HTTPException as e:
         # Log the exception detail captured by the handlers above or raised directly
         print(f"[{req_id}] (Worker) 捕获到 HTTP 异常: Status={e.status_code}, Detail={e.detail}", flush=True) # 中文
         if not result_future.done():
              result_future.set_exception(e)
    # Add specific handling for our custom disconnect error
    except ClientDisconnectedError as e:
         print(f"[{req_id}] (Worker) 捕获到内部客户端断开信号: {e}", flush=True)
         # The exception should already be set on the future by the checker task.
         # If somehow it's not, set it now.
         if not result_future.done():
              print(f"[{req_id}] (Worker) 警告：内部断开信号捕获，但 Future 未设置异常。现在设置 499。")
              result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端关闭了请求 (捕获于 Worker)"))
    except PlaywrightAsyncError as e:
         print(f"[{req_id}] (Worker) ❌ Playwright 处理期间出错: {e}", flush=True) # 中文
         # Check if client disconnected *before* saving snapshot or setting 503
         if client_disconnected_event.is_set():
              print(f"[{req_id}] (Worker) Playwright 错误期间检测到客户端已断开，优先处理断开。")
              if not result_future.done():
                   result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端在 Playwright 错误期间关闭请求"))
              return # Exit to finally block
         await save_error_snapshot(f"playwright_error_{req_id}")
         if not result_future.done():
              # Return 503 for likely page/browser issues that might be recoverable
              result_future.set_exception(HTTPException(status_code=503, detail=f"[{req_id}] Playwright 错误，请稍后重试: {e}", headers={"Retry-After": "30"})) # 中文
    except asyncio.TimeoutError as e:
         # Catch timeouts from interruptible_wait_for
         print(f"[{req_id}] (Worker) ❌ 操作超时: {e}", flush=True)
         if client_disconnected_event.is_set(): # Check disconnect on timeout too
              print(f"[{req_id}] (Worker) 操作超时期间检测到客户端已断开，优先处理断开。")
              if not result_future.done():
                   result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端在操作超时期间关闭请求"))
              return # Exit to finally block
         await save_error_snapshot(f"operation_timeout_{req_id}")
         if not result_future.done():
            result_future.set_exception(HTTPException(status_code=504, detail=f"[{req_id}] 服务器操作超时"))
    except asyncio.CancelledError:
        print(f"[{req_id}] (Worker) 处理任务被取消 (可能来自 Worker 自身取消)。", flush=True) # 中文
        if not result_future.done():
            # Don't assume 499, could be server shutdown
            result_future.set_exception(HTTPException(status_code=503, detail=f"[{req_id}] 请求处理被服务器取消"))
    except Exception as e:
         print(f"[{req_id}] (Worker) ❌ 处理期间意外错误: {e}", flush=True) # 中文
         if client_disconnected_event.is_set(): # Check disconnect on general error
              print(f"[{req_id}] (Worker) 意外错误期间检测到客户端已断开，优先处理断开。")
              if not result_future.done():
                   result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] 客户端在意外错误期间关闭请求"))
              return # Exit to finally block
         await save_error_snapshot(f"unexpected_error_{req_id}")
         traceback.print_exc()
         if not result_future.done():
              result_future.set_exception(HTTPException(status_code=500, detail=f"[{req_id}] 意外服务器错误: {e}")) # 中文
    finally:
         # Clean up the disconnect checker task for this request
         if disconnect_check_task and not disconnect_check_task.done():
              disconnect_check_task.cancel()
              try: await disconnect_check_task
              except asyncio.CancelledError: pass
              # print(f"[{req_id}] (Worker) Disconnect check task cleanup attempted.") # Debug log
         print(f"[{req_id}] (Worker) --- 完成处理请求 (退出 _process_request_from_queue) --- ", flush=True) # 中文


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, http_request: Request):
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    print(f"[{req_id}] === 收到 /v1/chat/completions 请求 === 模式: {'流式' if request.stream else '非流式'}。队列长度: {request_queue.qsize()}", flush=True) # 中文

    if is_initializing or not worker_task or worker_task.done():
         print(f"[{req_id}] ⏳ 服务仍在初始化或 Worker 未运行。", flush=True) # 中文
         # Return 503 Service Unavailable
         raise HTTPException(status_code=503, detail=f"[{req_id}] 服务初始化中或 Worker 未运行，请稍后重试。", headers={"Retry-After": "10"}) # 中文

    if not is_playwright_ready or not is_browser_connected or not is_page_ready:
         print(f"[{req_id}] ❌ 请求失败: 服务未完全就绪 (Playwright:{is_playwright_ready}, Browser:{is_browser_connected}, Page:{is_page_ready}).", flush=True) # 中文
         raise HTTPException(status_code=503, detail=f"[{req_id}] 与浏览器/页面的连接未激活。", headers={"Retry-After": "30"}) # 中文

    # --- 加入队列前先检查客户端是否已断开连接 ---
    if await http_request.is_disconnected():
        print(f"[{req_id}] 客户端在加入队列前已断开连接。返回 499。", flush=True)
        raise HTTPException(status_code=499, detail=f"[{req_id}] 客户端在请求排队前关闭了请求")
    # --- 结束初始检查 ---

    result_future = asyncio.Future()
    queue_item = {
         "req_id": req_id,
         "request_data": request,
         "http_request": http_request, # Pass the original request object
         "result_future": result_future
    }

    await request_queue.put(queue_item)
    print(f"[{req_id}] 请求已加入队列 (新队列长度: {request_queue.qsize()})。等待 Worker 处理...", flush=True) # 中文

    try:
        # 只等待 Future 结果。断开连接检测完全由 Worker 处理。
        print(f"[{req_id}] API Handler: 等待 Future 结果...", flush=True)
        result = await result_future
        print(f"[{req_id}] API Handler: Future 完成，收到结果。", flush=True) # 中文

        # 处理成功结果
        if request.stream:
            if callable(result): # 检查是否为生成器函数
                print(f"[{req_id}] 返回流式响应。", flush=True) # 中文
                headers = {
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'Content-Type': 'text/event-stream'
                }
                return StreamingResponse(result(), media_type="text/event-stream", headers=headers)
            else:
                print(f"[{req_id}] 错误: 流式请求 Worker 未返回可调用对象。", flush=True) # 中文
                # 如果 Worker 未返回生成器，抛出 500
                raise HTTPException(status_code=500, detail=f"[{req_id}] 服务器内部错误：流式处理未能生成有效响应") # 中文
        else:
            if isinstance(result, dict):
                print(f"[{req_id}] 返回 JSON 响应。", flush=True) # 中文
                return JSONResponse(content=result)
            else:
                print(f"[{req_id}] 错误: 非流式请求 Worker 未返回字典。", flush=True) # 中文
                # 如果 Worker 未返回字典，抛出 500
                raise HTTPException(status_code=500, detail=f"[{req_id}] 服务器内部错误：非流式处理未能生成有效响应") # 中文

    except HTTPException as http_exc:
        # 重新抛出由 Worker 显式设置的 HTTPException (包括因断开连接设置的 499)
        print(f"[{req_id}] API Handler: Future 返回 HTTPException: {http_exc.status_code}, Detail: {http_exc.detail}", flush=True)
        raise http_exc
    except asyncio.CancelledError:
        # 如果 Worker 任务本身或此处理器的 await 被取消 (例如，服务器关闭)
        print(f"[{req_id}] API 端点等待任务被取消 (可能由服务器关闭引起)。", flush=True) # 中文
        # 不要假设是 499，设置为 503
        raise HTTPException(status_code=503, detail=f"[{req_id}] 请求在服务器端被取消") # Service Unavailable
    except Exception as e:
        # 捕获其他由 Worker 在 Future 上设置的意外异常
        print(f"[{req_id}] API Handler: Future 返回意外错误: {type(e).__name__}: {e}", flush=True) # 中文
        traceback.print_exc()
        # 确保如果发生意外情况，Future 被取消 (尽管它应该已经保存了异常)
        if not result_future.done():
             result_future.set_exception(e) # 如果 await 已完成，不应触发
        raise HTTPException(status_code=500, detail=f"[{req_id}] 处理请求时发生意外服务器错误: {e}") # 中文
    # 不需要 finally 块，因为断开连接任务由 Worker 处理

# --- __main__ block --- (Translate print statements)
if __name__ == "__main__":
    # ... (code unchanged) ...
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
            workers=1, # MUST be 1 due to shared Playwright state and queue
            use_colors=False
        )
    except OSError as e:
        if e.errno == 48: # Address already in use
            print(f"\\n❌ 错误：端口 {SERVER_PORT} 已被占用！") # 中文
            print("   请检查并结束占用该端口的进程，或修改 server.py 中的 SERVER_PORT。") # 中文
            print(f"   查找命令示例 (macOS/Linux): lsof -t -i:{SERVER_PORT} | xargs kill -9")
            sys.exit(1)
        else:
            print(f"❌ 发生未处理的 OS 错误: {e}") # 中文
            raise e
    except Exception as e:
         print(f"❌ 启动服务器时发生意外错误: {e}") # 中文
         traceback.print_exc()
         sys.exit(1) 
