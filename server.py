# server.py
import asyncio
import random
import time
import json # Added for potential JSON operations
from typing import List, Optional, Dict, Any, Union
import os
import traceback # Keep traceback import

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
# Assuming camoufox is installed and provides sync/async APIs
# Adjust the import based on actual library structure if needed
from camoufox.sync_api import Camoufox as CamoufoxSync
# Import the async module directly
import camoufox.async_api
from playwright.sync_api import Page as SyncPage, Browser as SyncBrowser, Playwright as SyncPlaywright, Error as PlaywrightSyncError, expect as expect_sync # Added expect
from playwright.async_api import Page as AsyncPage, Browser as AsyncBrowser, Playwright as AsyncPlaywright, Error as PlaywrightAsyncError, expect as expect_async # Added expect
from playwright.async_api import async_playwright # Import standard async_playwright

# --- Configuration (Mirrored from server.cjs, adjust as needed) ---
# SERVER_PORT = 2048 # Port will be handled by uvicorn when running
AI_STUDIO_URL_PATTERN = 'aistudio.google.com/'
RESPONSE_COMPLETION_TIMEOUT = 300000 # 5 minutes total timeout (in ms)
POLLING_INTERVAL_STREAM = 200 # ms
SILENCE_TIMEOUT_MS = 1500 # ms
# v2.12: Timeout for secondary checks *after* spinner disappears
POST_SPINNER_CHECK_DELAY_MS = 500 # Spinner消失后稍作等待再检查其他状态
FINAL_STATE_CHECK_TIMEOUT_MS = 1500 # 检查按钮和输入框最终状态的超时
SPINNER_CHECK_TIMEOUT_MS = 1000 # 检查Spinner状态的超时
POST_COMPLETION_BUFFER = 1000 # JSON模式下可以缩短检查后等待时间
# !! 新增：清空验证相关常量 !! (Mirrored)
CLEAR_CHAT_VERIFY_TIMEOUT_MS = 5000 # 等待清空生效的总超时时间 (ms)
CLEAR_CHAT_VERIFY_INTERVAL_MS = 300 # 检查清空状态的轮询间隔 (ms)


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

# --- FastAPI App ---
app = FastAPI(
    title="AI Studio Proxy Server (Python/FastAPI/Camoufox)",
    description="A proxy server to interact with Google AI Studio using Playwright and Camoufox.",
    version="0.1.0-py"
)

# --- Global State (Consider alternatives for production, e.g., dependency injection) ---
# Using async versions for FastAPI's async nature
browser_instance: Optional[AsyncBrowser] = None
page_instance: Optional[AsyncPage] = None
is_camoufox_ready = False
is_initializing = False
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


# --- Camoufox Initialization (Using async Camoufox) ---
async def initialize_camoufox():
    global browser_instance, page_instance, is_camoufox_ready, is_initializing, pw_instance # Need to store playwright instance too
    if is_camoufox_ready or is_initializing:
        return
    is_initializing = True
    pw_instance = None # Store the playwright instance
    print("--- Initializing Camoufox ---")
    try:
        # Start Playwright context first
        print("Starting Playwright context...")
        pw_instance = await async_playwright().start() # Start standard Playwright

        # Now try launching Camoufox *within* this context?
        # This assumes Camoufox integrates with an existing Playwright instance,
        # or uses its own launch parameters passed to the underlying firefox.launch
        print("Launching Camoufox browser instance via Playwright...")

        # Option 1: Camoufox is a launcher configuration passed to playwright's launch? (Less likely based on API)
        # browser_instance = await pw_instance.firefox.launch(
        #     # How to pass camoufox config here? Needs investigation.
        #     headless=False
        # )

        # Option 2: Camoufox class IS the launcher (Let's retry this, maybe class name was the only issue?)
        # **Reverting to previous attempt with correct class name, as async with isn't suitable**
        camoufox_launcher = camoufox.async_api.AsyncCamoufox(headless=False)
        # What METHOD should be called? Let's check dir() again inside the try block
        print(f"Methods available on AsyncCamoufox instance: {dir(camoufox_launcher)}")
        # Maybe it needs to be awaited directly? or has a different method?
        # Trying .launch() again just to confirm the AttributeError was real.
        browser_instance = await camoufox_launcher.launch() # This will likely fail again, but confirms the error.

        # --- If the above .launch() fails, we need to know the correct method ---
        # --- The dir() output will help ---

        print(f"Browser launched: {browser_instance.version}") # This line might not be reached

        context = browser_instance.contexts[0]
        print("Default context obtained.")

        # Restore page finding logic
        found_page = None
        pages = context.pages
        print(f"-> Found {len(pages)} existing pages. Searching for AI Studio ({AI_STUDIO_URL_PATTERN})...")
        target_url_base = f"https://{AI_STUDIO_URL_PATTERN}"
        target_full_url = f"{target_url_base}prompts/new_chat" # Example target

        for p in pages:
            try:
                current_url = await p.url()
                print(f"   Checking page: {current_url}")
                # Looser check for base domain first
                if not p.is_closed() and target_url_base in current_url:
                     print(f"-> Found potential AI Studio page: {current_url}")
                     found_page = p
                     # Try to navigate to the specific prompts page if not already there
                     if "/prompts/" not in current_url:
                          print(f"   Navigating to {target_full_url}...")
                          try:
                               await p.goto(target_full_url, wait_until="domcontentloaded", timeout=35000)
                               print(f"   Navigation successful: {await p.url()}")
                          except Exception as nav_err:
                               print(f"   Warning: Navigation failed: {nav_err}. Using current page.")
                     break # Found a suitable page
            except Exception as e:
                # Ignore pages that cause errors (e.g., about:blank during init)
                if not p.is_closed():
                     print(f"   Warning: Error checking page URL for '{getattr(p, 'url', 'N/A')}': {e}")

        if not found_page:
            print(f"-> AI Studio page not found. Opening and navigating new page to {target_full_url}...")
            found_page = await context.new_page()
            await found_page.goto(target_full_url, wait_until="domcontentloaded", timeout=60000)
            print(f"-> Navigated new page to AI Studio: {await found_page.url()}")

        page_instance = found_page
        await page_instance.bring_to_front()
        print("-> Attempted to bring page to front.")

        # Basic readiness check
        print("-> Checking for input area visibility...")
        await expect_async(page_instance.locator(INPUT_SELECTOR)).to_be_visible(timeout=15000)
        print("-> Core input area visible.")

        is_camoufox_ready = True
        print("✅ Camoufox initialization successful.")

    except AttributeError as ae:
         print(f"❌ Camoufox initialization failed: Method not found.")
         print(f"   Error details: {ae}")
         # Print dir() again on error if available
         if 'camoufox_launcher' in locals():
              print(f"   Methods available on AsyncCamoufox instance: {dir(camoufox_launcher)}")
         else:
              print("   Could not inspect AsyncCamoufox instance.")
         if browser_instance: await browser_instance.close()
         if pw_instance: await pw_instance.stop() # Stop playwright if started
         browser_instance = None
         page_instance = None
         pw_instance = None
         is_camoufox_ready = False
    except Exception as e:
        print(f"❌ Camoufox general initialization failed: {e}")
        traceback.print_exc()
        if browser_instance: await browser_instance.close()
        if pw_instance: await pw_instance.stop()
        browser_instance = None
        page_instance = None
        pw_instance = None
        is_camoufox_ready = False
    finally:
        is_initializing = False

# --- API Endpoints ---

@app.on_event("startup")
async def startup_event():
    # Initialize Camoufox when the FastAPI server starts
    print("FastAPI server starting up. Initializing Camoufox...")
    # Use asyncio.create_task for non-blocking initialization in background
    asyncio.create_task(initialize_camoufox())

@app.get("/health")
async def health_check():
    # More detailed health check
    page_valid = page_instance is not None and not page_instance.is_closed()
    browser_connected = browser_instance is not None and browser_instance.is_connected()
    status_val = "OK" if is_camoufox_ready and page_valid and browser_connected else "Error"

    status = {
        "status": status_val,
        "message": "",
        "camoufoxReady": is_camoufox_ready,
        "browserConnected": browser_connected,
        "pageValid": page_valid,
        "initializing": is_initializing,
        # "queueLength": 0 # Add queue status later
        # "processing": False
    }
    if status_val == "OK":
        status["message"] = "Service running, Camoufox connected, page valid."
        return JSONResponse(content=status, status_code=200)
    else:
        reasons = []
        if not is_camoufox_ready: reasons.append("Camoufox not initialized or ready")
        if not page_valid: reasons.append("Target page not found or closed")
        if not browser_connected: reasons.append("Browser disconnected")
        if is_initializing: reasons.append("Camoufox is currently initializing")
        status["message"] = f"Service Unavailable. Issues: {', '.join(reasons)}."
        return JSONResponse(content=status, status_code=503)


@app.get("/v1/models")
async def list_models():
    # Mimic OpenAI models endpoint
    print("[API] Received /v1/models request.")
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

# --- Placeholder for the main chat completion logic ---
# We will migrate the complex logic from server.cjs processQueue here step-by-step
async def process_chat_request(req_id: str, request: ChatCompletionRequest, http_request: Request):
    print(f"[{req_id}] Processing chat request...")
    is_streaming = request.stream

    # Ensure page is still valid before proceeding
    if not page_instance or page_instance.is_closed():
         print(f"[{req_id}] Error: Page became invalid during processing.")
         raise HTTPException(status_code=503, detail=f"[{req_id}] AI Studio page lost during processing.")

    # 1. Validation
    try:
         validation_result = validate_chat_request(request.messages, req_id)
         user_prompt = validation_result["userPrompt"]
         system_prompt = validation_result["systemPrompt"]
         if user_prompt is None: # Should be string or empty string now
             raise ValueError("Processed user prompt is unexpectedly None.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"[{req_id}] Invalid request: {e}")

    # Correct the print statements to avoid nested quote issues
    print(f"[{req_id}] User Prompt (Validated, len={len(user_prompt)}): '{user_prompt[:80]}...'")
    if system_prompt:
        print(f"[{req_id}] System Prompt (Validated, len={len(system_prompt)}): '{system_prompt[:80]}...'")

    # 2. Prepare Prompt
    if is_streaming:
         prepared_prompt = prepare_ai_studio_prompt_stream(user_prompt, system_prompt)
         print(f"[{req_id}] Prepared Streaming Prompt (start): '{prepared_prompt[:150]}...'")
    else:
         prepared_prompt = prepare_ai_studio_prompt(user_prompt, system_prompt)
         print(f"[{req_id}] Prepared Non-Streaming Prompt (start): '{prepared_prompt[:150]}...'")

    # --- Client Disconnect Handling ---
    client_disconnected = False
    disconnect_event = asyncio.Event()

    async def check_disconnect():
        nonlocal client_disconnected
        try:
            # Poll the state; raises RequestDisconnect if client disconnects
            while True:
                await http_request.is_disconnected() # Check state
                # If is_disconnected() returns True (or doesn't raise), it means disconnected
                client_disconnected = True
                disconnect_event.set()
                print(f"[{req_id}] Client disconnected (detected by poll).")
                break # Stop polling once disconnected
        except Exception: # Catches RequestDisconnect or others
             client_disconnected = True
             disconnect_event.set()
             print(f"[{req_id}] Client disconnected (detected by exception).")
        # except asyncio.CancelledError:
             # print(f"[{req_id}] Disconnect checker cancelled.") # Task cancelled, likely during shutdown


    disconnect_task = asyncio.create_task(check_disconnect())
    # --- End Client Disconnect Handling ---

    # Add helper function import for saving snapshots
    async def save_error_snapshot(error_name: str = 'error', page: Optional[AsyncPage] = page_instance, prefix: str = req_id):
         # Simple snapshot helper, adapt path/naming as needed
         if not page or page.is_closed():
             print(f"[{prefix}] Cannot save snapshot ({error_name}), page unavailable.")
             return
         print(f"[{prefix}] Attempting to save error snapshot ({error_name})...")
         timestamp = int(time.time() * 1000)
         error_dir = os.path.join(os.path.dirname(__file__), 'errors_py') # Separate dir for python errors
         try:
             if not os.path.exists(error_dir):
                 os.makedirs(error_dir, exist_ok=True)
             filename_base = f"{error_name}_{prefix}_{timestamp}"
             screenshot_path = os.path.join(error_dir, f"{filename_base}.png")
             html_path = os.path.join(error_dir, f"{filename_base}.html")

             try:
                 await page.screenshot(path=screenshot_path, full_page=True, timeout=15000)
                 print(f"[{prefix}]   Snapshot saved to: {screenshot_path}")
             except Exception as ss_err:
                 print(f"[{prefix}]   Failed to save screenshot ({error_name}): {ss_err}")
             try:
                 content = await page.content(timeout=15000)
                 with open(html_path, 'w', encoding='utf-8') as f:
                     f.write(content)
                 print(f"[{prefix}]   HTML saved to: {html_path}")
             except Exception as html_err:
                 print(f"[{prefix}]   Failed to save HTML ({error_name}): {html_err}")
         except Exception as dir_err:
             print(f"[{prefix}]   Error creating error directory or saving snapshot: {dir_err}")

    try:
        # --- TODO: Optional: Implement Clear Chat Logic ---
        # is_likely_new_chat = ... (logic from server.cjs)
        # if is_likely_new_chat and CLEAR_CHAT_BUTTON_SELECTOR and CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR:
        #     try:
        #         print(f"[{req_id}] Attempting to clear chat...")
        #         await page_instance.locator(CLEAR_CHAT_BUTTON_SELECTOR).click(timeout=7000)
        #         await page_instance.locator(CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR).click(timeout=5000)
        #         # TODO: Add verification logic (check if response containers are gone)
        #         print(f"[{req_id}] Clear chat buttons clicked (verification pending).")
        #     except Exception as clear_err:
        #         print(f"[{req_id}] Warning: Failed to clear chat: {clear_err}")
        #         # Add snapshot saving here if desired

        # 3. Interact and Submit
        print(f"[{req_id}] Filling prompt and clicking submit...")
        input_field = page_instance.locator(INPUT_SELECTOR)
        submit_button = page_instance.locator(SUBMIT_BUTTON_SELECTOR)

        await expect_async(input_field).to_be_visible(timeout=10000)
        await input_field.fill(prepared_prompt, timeout=60000)
        await expect_async(submit_button).to_be_enabled(timeout=10000)
        await submit_button.click(timeout=10000)
        print(f"[{req_id}] Prompt submitted.")

        # 4. Locate Response Element (Might need retry logic like in server.cjs)
        print(f"[{req_id}] Locating response elements...")
        # Simplified location for now
        response_element = page_instance.locator(RESPONSE_CONTAINER_SELECTOR).last.locator(RESPONSE_TEXT_SELECTOR)
        await expect_async(response_element).to_be_attached(timeout=15000) # Wait for it to appear in DOM
        print(f"[{req_id}] Response element located.")

        # 5. Handle Response (Streaming or Non-streaming)
        if is_streaming:
            print(f"[{req_id}] Processing SSE stream...")

            async def stream_generator():
                last_raw_text = ""
                last_sent_response_content = ""
                response_started = False
                start_marker = '<<<START_RESPONSE>>>'
                spinner_disappeared = False
                last_text_change_timestamp = time.time() * 1000 # ms
                stream_finished_naturally = False
                start_time = time.time() * 1000 # ms
                spinner_locator = page_instance.locator(LOADING_SPINNER_SELECTOR)

                try:
                    while time.time() * 1000 - start_time < RESPONSE_COMPLETION_TIMEOUT:
                        if client_disconnected:
                             print(f"[{req_id}] Stopping stream generator due to client disconnect.")
                             break # Exit loop if client disconnected

                        loop_start_time = time.time() * 1000
                        current_raw_text = await get_raw_text_content(response_element, last_raw_text, req_id)

                        if current_raw_text != last_raw_text:
                            last_text_change_timestamp = time.time() * 1000
                            potential_new_delta = ""
                            current_content_after_marker = ""

                            marker_index = current_raw_text.find(start_marker)
                            if marker_index != -1:
                                if not response_started:
                                    print(f"[{req_id}]    (Stream) Found start marker '{start_marker}'.")
                                    response_started = True
                                current_content_after_marker = current_raw_text[marker_index + len(start_marker):]
                                potential_new_delta = current_content_after_marker[len(last_sent_response_content):]
                            elif response_started:
                                 potential_new_delta = "" # Marker disappeared?
                                 print(f"[{req_id}] Warning: Marker disappeared after being seen.")

                            if potential_new_delta:
                                yield generate_sse_chunk(potential_new_delta, req_id, MODEL_NAME)
                                last_sent_response_content += potential_new_delta

                            last_raw_text = current_raw_text

                        # Check spinner
                        if not spinner_disappeared:
                             try:
                                 await expect_async(spinner_locator).to_be_hidden(timeout=50)
                                 spinner_disappeared = True
                                 last_text_change_timestamp = time.time() * 1000 # Reset silence timer
                                 print(f"[{req_id}]    Spinner hidden. Checking for silence...")
                             except PlaywrightAsyncError:
                                 pass # Spinner still visible

                        # Silence Check
                        is_silent = spinner_disappeared and (time.time() * 1000 - last_text_change_timestamp > SILENCE_TIMEOUT_MS)
                        if is_silent:
                            print(f"[{req_id}] Silence detected. Finishing stream.")
                            stream_finished_naturally = True
                            break # Exit loop

                        # Control polling interval
                        loop_duration = time.time() * 1000 - loop_start_time
                        wait_time = max(0, POLLING_INTERVAL_STREAM - loop_duration) / 1000 # convert to seconds
                        await asyncio.sleep(wait_time)

                    # --- Loop End ---

                    if client_disconnected:
                         yield generate_sse_stop_chunk(req_id, MODEL_NAME, "client_disconnect")
                         return # Don't send final DONE

                    if stream_finished_naturally:
                        # Final sync check (simple version)
                        final_raw_text = await get_raw_text_content(response_element, last_raw_text, req_id)
                        final_content_after_marker = ""
                        final_marker_index = final_raw_text.find(start_marker)
                        if final_marker_index != -1:
                             final_content_after_marker = final_raw_text[final_marker_index + len(start_marker):]
                        final_delta = final_content_after_marker[len(last_sent_response_content):]
                        if final_delta:
                             print(f"[{req_id}] Sending final delta (len: {len(final_delta)})")
                             yield generate_sse_chunk(final_delta, req_id, MODEL_NAME)

                        yield generate_sse_stop_chunk(req_id, MODEL_NAME)
                        print(f"[{req_id}] ✅ Stream finished naturally.")
                    else: # Timeout
                        print(f"[{req_id}] ⚠️ Stream timed out after {RESPONSE_COMPLETION_TIMEOUT / 1000}s.")
                        await save_error_snapshot("streaming_timeout") # Added snapshot
                        yield generate_sse_error_chunk("Stream processing timed out on server.", req_id)
                        yield generate_sse_stop_chunk(req_id, MODEL_NAME, "timeout")

                    yield "data: [DONE]\n\n"

                except asyncio.CancelledError:
                     print(f"[{req_id}] Stream generator cancelled (likely client disconnect).")
                     yield "data: [DONE]\n\n" # Send DONE even on cancellation for SSE compliance
                except Exception as e:
                    print(f"[{req_id}] ❌ Error during streaming generation: {e}")
                    await save_error_snapshot("streaming_error") # Added snapshot
                    traceback.print_exc()
                    yield generate_sse_error_chunk(f"Server error during streaming: {e}", req_id)
                    yield "data: [DONE]\n\n" # Send DONE even on error


            return StreamingResponse(stream_generator(), media_type="text/event-stream")

        else: # Non-streaming
            print(f"[{req_id}] Processing non-streaming response...")
            start_time_ns = time.time()
            final_state_reached = False
            final_state_check_initiated = False
            spinner_locator = page_instance.locator(LOADING_SPINNER_SELECTOR)
            input_field = page_instance.locator(INPUT_SELECTOR) # Ensure locator is available
            submit_button = page_instance.locator(SUBMIT_BUTTON_SELECTOR) # Ensure locator is available

            # --- Refined Non-Streaming Wait Logic ---
            while time.time() - start_time_ns < RESPONSE_COMPLETION_TIMEOUT / 1000:
                if client_disconnected:
                    print(f"[{req_id}] Non-streaming cancelled due to client disconnect.")
                    raise HTTPException(status_code=499, detail=f"[{req_id}] Client closed request")

                spinner_hidden = False
                input_empty = False
                button_disabled = False

                # 1. Check Spinner Hidden
                try:
                    await expect_async(spinner_locator).to_be_hidden(timeout=SPINNER_CHECK_TIMEOUT_MS)
                    spinner_hidden = True
                except PlaywrightAsyncError: pass # Spinner still visible or check timed out

                # 2. If Spinner Hidden, check Input Empty and Button Disabled
                if spinner_hidden:
                    try:
                        await expect_async(input_field).to_have_value('', timeout=FINAL_STATE_CHECK_TIMEOUT_MS)
                        input_empty = True
                    except PlaywrightAsyncError: pass # Input not empty or check timed out

                    if input_empty: # Only check button if input is empty
                        try:
                            await expect_async(submit_button).to_be_disabled(timeout=FINAL_STATE_CHECK_TIMEOUT_MS)
                            button_disabled = True
                        except PlaywrightAsyncError: pass # Button not disabled or check timed out

                # 3. Potential Final State Detected
                if spinner_hidden and input_empty and button_disabled:
                    if not final_state_check_initiated:
                        final_state_check_initiated = True
                        print(f"[{req_id}]    Potential final state detected. Waiting {POST_COMPLETION_BUFFER}ms to confirm...")
                        await asyncio.sleep(POST_COMPLETION_BUFFER / 1000)
                        print(f"[{req_id}]    {POST_COMPLETION_BUFFER}ms wait finished. Re-checking state rigorously...")
                        try:
                            # Rigorous Re-check
                            await expect_async(spinner_locator).to_be_hidden(timeout=500)
                            await expect_async(input_field).to_have_value('', timeout=500)
                            await expect_async(submit_button).to_be_disabled(timeout=500)
                            print(f"[{req_id}]    State confirmed. Checking text stability for {SILENCE_TIMEOUT_MS}ms...")

                            # Text Silence Check
                            text_stable = False
                            silence_check_start_time = time.time()
                            last_check_text = await get_raw_text_content(response_element, '', req_id)

                            while time.time() - silence_check_start_time < SILENCE_TIMEOUT_MS / 1000:
                                await asyncio.sleep(POLLING_INTERVAL_STREAM / 1000) # Use stream interval for checking
                                current_check_text = await get_raw_text_content(response_element, '', req_id)
                                if current_check_text == last_check_text:
                                    # Text hasn't changed since last check interval
                                    if time.time() - silence_check_start_time >= SILENCE_TIMEOUT_MS / 1000:
                                         print(f"[{req_id}]    Text stable for {SILENCE_TIMEOUT_MS}ms. Processing complete.")
                                         text_stable = True
                                         break # Exit silence check loop
                                else:
                                    # Text changed, reset silence timer start *and* update text
                                    print(f"[{req_id}]    (Silence Check) Text changed. Resetting timer.")
                                    silence_check_start_time = time.time()
                                    last_check_text = current_check_text

                            if text_stable:
                                final_state_reached = True
                                break # Exit main non-streaming wait loop
                            else:
                                print(f"[{req_id}]    ⚠️ Warning: Text silence check timed out after {SILENCE_TIMEOUT_MS}ms. Proceeding anyway.")
                                final_state_reached = True # Proceed even if unstable after check duration
                                break # Exit main non-streaming wait loop

                        except PlaywrightAsyncError as recheck_error:
                            print(f"[{req_id}]    State changed during confirmation ({recheck_error}). Continuing poll.")
                            final_state_check_initiated = False # Reset flag
                        except Exception as stability_err:
                             print(f"[{req_id}]    Error during text stability check: {stability_err}")
                             traceback.print_exc()
                             final_state_check_initiated = False # Reset flag

                else: # Not in potential final state, or state changed during confirmation
                    if final_state_check_initiated:
                         print(f"[{req_id}]    Final state conditions no longer met. Resetting confirmation flag.")
                         final_state_check_initiated = False
                    # Wait longer if not actively checking final state
                    await asyncio.sleep(POLLING_INTERVAL_STREAM * 2 / 1000)

            # --- End of Non-Streaming Wait Logic Loop ---

            if client_disconnected: # Re-check after loop
                 raise HTTPException(status_code=499, detail=f"[{req_id}] Client closed request")

            # Check for Page Errors (like toasts) BEFORE parsing
            # TODO: Implement detectAndExtractPageError if needed

            if not final_state_reached:
                 print(f"[{req_id}] ⚠️ Non-streaming wait timed out after {RESPONSE_COMPLETION_TIMEOUT / 1000}s. Attempting to get content anyway.")
                 await save_error_snapshot("nonstream_final_state_timeout")
            else:
                 print(f"[{req_id}] ✅ Final state reached. Getting and parsing final content...")

            # --- Get and Parse Final Content ---
            final_content_for_user = "" # Default empty
            try:
                # Add retry logic here if needed, like in server.cjs
                 final_raw_text = await get_raw_text_content(response_element, '', req_id)
                 print(f"[{req_id}] Final Raw Text (len={len(final_raw_text)}): '{final_raw_text[:100]}...'")

                 if not final_raw_text or not final_raw_text.strip():
                     print(f"[{req_id}] Warning: Got empty raw text from response element.")
                     # Maybe check for page errors again here
                     # final_check_error = await detectAndExtractPageError(...)
                     # if final_check_error: raise UpstreamError(...)
                     final_content_for_user = "" # Keep it empty

                 else:
                    # --- JSON Parsing Logic (from server.cjs) ---
                    parsed_json = None
                    ai_response_text_from_json = None
                    try:
                        # Attempt to find and parse JSON within the raw text
                        text_to_parse = final_raw_text.strip()
                        start_index = -1
                        end_index = -1
                        first_brace = text_to_parse.find('{')
                        first_bracket = text_to_parse.find('[')

                        if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
                            start_index = first_brace
                            end_index = text_to_parse.rfind('}')
                        elif first_bracket != -1:
                            start_index = first_bracket
                            end_index = text_to_parse.rfind(']')

                        if start_index != -1 and end_index != -1 and end_index >= start_index:
                             json_text = text_to_parse[start_index : end_index + 1]
                             try:
                                 parsed_json = json.loads(json_text)
                                 print(f"[{req_id}]    Successfully parsed JSON block.")
                             except json.JSONDecodeError as json_err:
                                 print(f"[{req_id}]    Warning: Failed to parse extracted JSON text: {json_err}")
                                 await save_error_snapshot("json_parse_fail")
                                 # Fallback: Use raw text if JSON parsing fails but text exists
                                 ai_response_text_from_json = final_raw_text
                        else:
                             print(f"[{req_id}]    Warning: Could not find valid JSON start/end markers in raw text.")
                             # Fallback: Use raw text if no JSON structure found
                             ai_response_text_from_json = final_raw_text

                    except Exception as parse_find_err:
                         print(f"[{req_id}]    Error during JSON finding/parsing: {parse_find_err}")
                         await save_error_snapshot("json_find_parse_error")
                         # Fallback: Use raw text on error
                         ai_response_text_from_json = final_raw_text

                    # Extract 'response' field if JSON was parsed
                    if parsed_json:
                         if isinstance(parsed_json.get("response"), str):
                              ai_response_text_from_json = parsed_json["response"]
                              print(f"[{req_id}]    Extracted 'response' field from JSON.")
                         else:
                             # JSON valid but no 'response' string - use stringified JSON
                             try:
                                 ai_response_text_from_json = json.dumps(parsed_json)
                                 print(f"[{req_id}]    Warning: 'response' field not found/not string in JSON. Using stringified JSON.")
                             except Exception as stringify_err:
                                  print(f"[{req_id}]    Error stringifying parsed JSON: {stringify_err}")
                                  ai_response_text_from_json = final_raw_text # Fallback

                    # --- End JSON Parsing Logic ---

                    # Remove marker if present
                    start_marker = '<<<START_RESPONSE>>>'
                    if ai_response_text_from_json and ai_response_text_from_json.startswith(start_marker):
                        final_content_for_user = ai_response_text_from_json[len(start_marker):]
                        print(f"[{req_id}]    Removed start marker.")
                    elif ai_response_text_from_json: # Use the text (even if no marker)
                        final_content_for_user = ai_response_text_from_json
                        print(f"[{req_id}]    Warning: Start marker not found in final text.")
                    else: # Should not happen if raw_text was not empty, but safeguard
                         final_content_for_user = ""


            except Exception as e:
                print(f"[{req_id}] ❌ Error getting final non-streaming content: {e}")
                await save_error_snapshot("get_final_content_error")
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=f"[{req_id}] Error processing final response: {e}")

            # Construct and return final JSON payload
            response_payload = {
                "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}", # Add timestamp to ID
                "object": "chat.completion",
                "created": int(time.time()),
                "model": MODEL_NAME,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": final_content_for_user},
                    "finish_reason": "stop", # Assume stop unless timeout occurred before final state
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, # Placeholder
            }
            return JSONResponse(content=response_payload)

    except PlaywrightAsyncError as e:
        print(f"[{req_id}] ❌ Playwright Error during processing: {e}")
        await save_error_snapshot("playwright_error") # Added snapshot
        raise HTTPException(status_code=500, detail=f"[{req_id}] Playwright Error: {e}")
    except HTTPException:
         raise # Re-raise HTTPExceptions (like validation errors or client disconnect)
    except Exception as e:
        print(f"[{req_id}] ❌ Unexpected Error during processing: {e}")
        await save_error_snapshot("unexpected_error") # Added snapshot
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"[{req_id}] Unexpected Server Error: {e}")
    finally:
         # Cancel the disconnect checker task if it's still running
         if disconnect_task and not disconnect_task.done():
              disconnect_task.cancel()
              try:
                  await disconnect_task # Allow cancellation to propagate
              except asyncio.CancelledError:
                  pass # Expected cancellation
         print(f"[{req_id}] --- Finished processing chat request ---")


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, http_request: Request):
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    print(f"[{req_id}] === Received /v1/chat/completions request === Mode: {'Streaming' if request.stream else 'Non-streaming'}")

    # --- Basic Readiness Check ---
    if is_initializing:
        print(f"[{req_id}] ⏳ Camoufox is still initializing. Request may be delayed or fail.")
        # Optionally wait or raise 503 immediately
        # await asyncio.sleep(1) # Simple wait
        # Or: raise HTTPException(status_code=503, detail="Service initializing, please retry shortly.")
    if not is_camoufox_ready or not page_instance or page_instance.is_closed():
         print(f"[{req_id}] ❌ Request failed: Camoufox not ready or page closed.")
         # Attempt re-initialization maybe? Or just fail.
         # asyncio.create_task(initialize_camoufox()) # Trigger re-init in background?
         raise HTTPException(status_code=503, detail=f"[{req_id}] Camoufox connection not active. Please ensure browser is running and retry.")

    # --- TODO: Implement Request Queueing Logic ---
    # For now, process sequentially (FastAPI handles concurrent requests up to worker limits)
    # If strict ordering or resource limiting is needed, implement an asyncio.Queue here
    # like in server.cjs (requestQueue, isProcessing, processQueue).

    # --- Process the request ---
    # Use asyncio.wait_for for overall timeout, mirroring RESPONSE_COMPLETION_TIMEOUT
    try:
        return await asyncio.wait_for(
             process_chat_request(req_id, request, http_request),
             timeout=RESPONSE_COMPLETION_TIMEOUT / 1000 # Convert ms to seconds
        )
    except asyncio.TimeoutError:
        print(f"[{req_id}] ❌ Overall request timed out after {RESPONSE_COMPLETION_TIMEOUT / 1000}s.")
        # If streaming, the generator might have already sent DONE.
        # If non-streaming, send a 504.
        if request.stream:
            # Use helper functions to generate SSE error and DONE messages
            error_chunk = generate_sse_error_chunk("Overall request timeout.", req_id, "timeout_error")
            done_chunk = "data: [DONE]\n\n" # Standard DONE message
            # Return a streaming response containing the error and DONE
            return StreamingResponse(iter([error_chunk, done_chunk]), media_type="text/event-stream", status_code=504)
        else:
            raise HTTPException(status_code=504, detail=f"[{req_id}] Overall request processing timed out.")
    except HTTPException as http_exc:
         # Re-raise specific HTTP errors from processing function
         raise http_exc
    except Exception as e:
         # Catch any other unexpected errors during processing
         print(f"[{req_id}] ❌ Unexpected error at completion endpoint level: {e}")
         raise HTTPException(status_code=500, detail=f"[{req_id}] Unexpected server error during request handling.")


# --- Graceful shutdown ---
@app.on_event("shutdown")
async def shutdown_event():
    global browser_instance, page_instance, is_camoufox_ready, pw_instance
    print("--- FastAPI server shutting down ---")
    if browser_instance and browser_instance.is_connected():
        print("Closing Camoufox browser...")
        try:
            await browser_instance.close()
            print("Browser closed.")
        except Exception as e:
            print(f"Error closing browser: {e}")
    if pw_instance:
         print("Stopping Playwright...")
         try:
              await pw_instance.stop()
              print("Playwright stopped.")
         except Exception as e:
              print(f"Error stopping Playwright: {e}")

    browser_instance = None
    page_instance = None
    pw_instance = None
    is_camoufox_ready = False
    print("Shutdown complete.")

# --- Add __main__ block to run with uvicorn ---
if __name__ == "__main__":
    import uvicorn
    print("Starting server with Uvicorn...")
    # Make sure to install uvicorn: pip install "uvicorn[standard]" fastapi playwright camoufox browserforge
    # Run with: python server.py
    # Or for development with auto-reload: uvicorn server:app --reload --port 2048
    # Note: --reload might interfere with global state and browser instances if not handled carefully.
    # For production, run without --reload: uvicorn server:app --host 0.0.0.0 --port 2048 --workers 1
    # Using workers > 1 with Playwright/Camoufox global instance is problematic, stick to 1 worker.
    uvicorn.run("server:app", host="127.0.0.1", port=2048, log_level="info", workers=1) 