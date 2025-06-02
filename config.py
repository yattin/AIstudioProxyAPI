# config.py - 配置管理模块
# 包含所有常量、环境变量和配置项

import os
import multiprocessing
from typing import Optional, Dict

# --- 流队列相关 ---
STREAM_QUEUE: Optional[multiprocessing.Queue] = None
STREAM_PROCESS = None

STREAM_TIMEOUT_LOG_STATE = {
    "consecutive_timeouts": 0,
    "last_error_log_time": 0.0,  # 使用 time.monotonic()
    "suppress_until_time": 0.0,  # 使用 time.monotonic()
    "max_initial_errors": 3,
    "warning_interval_after_suppress": 60.0,  # seconds
    "suppress_duration_after_initial_burst": 300.0,  # seconds
}

# --- 全局标记常量 ---
USER_INPUT_START_MARKER_SERVER = "__USER_INPUT_START__"
USER_INPUT_END_MARKER_SERVER = "__USER_INPUT_END__"

# --- 全局日志控制配置 ---
DEBUG_LOGS_ENABLED = os.environ.get('DEBUG_LOGS_ENABLED', 'false').lower() in ('true', '1', 'yes')
TRACE_LOGS_ENABLED = os.environ.get('TRACE_LOGS_ENABLED', 'false').lower() in ('true', '1', 'yes')

# --- AI Studio 配置 ---
AI_STUDIO_URL_PATTERN = 'aistudio.google.com/'
RESPONSE_COMPLETION_TIMEOUT = 300000  # 5 minutes total timeout (in ms)
INITIAL_WAIT_MS_BEFORE_POLLING = 500  # ms, initial wait before polling for response completion
POLLING_INTERVAL = 300  # ms
POLLING_INTERVAL_STREAM = 180  # ms
SILENCE_TIMEOUT_MS = 40000  # ms
POST_SPINNER_CHECK_DELAY_MS = 500
FINAL_STATE_CHECK_TIMEOUT_MS = 1500
POST_COMPLETION_BUFFER = 700
CLEAR_CHAT_VERIFY_TIMEOUT_MS = 5000
CLEAR_CHAT_VERIFY_INTERVAL_MS = 400
CLICK_TIMEOUT_MS = 5000
CLIPBOARD_READ_TIMEOUT_MS = 5000
PSEUDO_STREAM_DELAY = 0.01

# --- 目录路径配置 ---
AUTH_PROFILES_DIR = os.path.join(os.path.dirname(__file__), 'auth_profiles')
ACTIVE_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'active')
SAVED_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'saved')
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
APP_LOG_FILE_PATH = os.path.join(LOG_DIR, 'app.log')

# --- 代理设置 ---
# 与重构前完全一致的代理设置
PROXY_SERVER_ENV = "http://127.0.0.1:3120/"
STREAM_PROXY_SERVER_ENV = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
NO_PROXY_ENV = os.environ.get('NO_PROXY')
AUTO_SAVE_AUTH = os.environ.get('AUTO_SAVE_AUTH', '').lower() in ('1', 'true', 'yes')
AUTH_SAVE_TIMEOUT = int(os.environ.get('AUTH_SAVE_TIMEOUT', '30'))

# --- Helper 服务配置 ---
HELPER_ENDPOINT = os.environ.get('HELPER_ENDPOINT', '')
HELPER_SAPISID = os.environ.get('HELPER_SAPISID', '')

# --- 运行时环境变量 ---
CAMOUFOX_WS_ENDPOINT = os.environ.get('CAMOUFOX_WS_ENDPOINT', '')
LAUNCH_MODE = os.environ.get('LAUNCH_MODE', 'headless')
ACTIVE_AUTH_JSON_PATH = os.environ.get('ACTIVE_AUTH_JSON_PATH', '')
HOST_OS_FOR_SHORTCUT = os.environ.get('HOST_OS_FOR_SHORTCUT', '')

PLAYWRIGHT_PROXY_SETTINGS: Optional[Dict[str, str]] = None
if PROXY_SERVER_ENV:
    PLAYWRIGHT_PROXY_SETTINGS = {'server': PROXY_SERVER_ENV}
    if NO_PROXY_ENV:
        PLAYWRIGHT_PROXY_SETTINGS['bypass'] = NO_PROXY_ENV.replace(',', ';')

# --- 模型相关常量 ---
MODEL_NAME = 'AI-Studio_Camoufox-Proxy'
CHAT_COMPLETION_ID_PREFIX = 'chatcmpl-'
MODELS_ENDPOINT_URL_CONTAINS = "MakerSuiteService/ListModels"
DEFAULT_FALLBACK_MODEL_ID = "no model list"
EXCLUDED_MODELS_FILENAME = "excluded_models.txt"

# --- CSS 选择器 ---
PROMPT_TEXTAREA_SELECTOR = 'ms-prompt-input-wrapper ms-autosize-textarea textarea'
INPUT_SELECTOR = PROMPT_TEXTAREA_SELECTOR
INPUT_SELECTOR2 = PROMPT_TEXTAREA_SELECTOR
SUBMIT_BUTTON_SELECTOR = 'button[aria-label="Run"].run-button'
RESPONSE_CONTAINER_SELECTOR = 'ms-chat-turn .chat-turn-container.model'
RESPONSE_TEXT_SELECTOR = 'ms-cmark-node.cmark-node'
LOADING_SPINNER_SELECTOR = 'button[aria-label="Run"].run-button svg .stoppable-spinner'
OVERLAY_SELECTOR = 'div.cdk-overlay-backdrop'
WAIT_FOR_ELEMENT_TIMEOUT_MS = 10000  # Timeout for waiting for elements like overlays
ERROR_TOAST_SELECTOR = 'div.toast.warning, div.toast.error'
CLEAR_CHAT_BUTTON_SELECTOR = 'button[data-test-clear="outside"][aria-label="Clear chat"]'
CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR = 'button.mdc-button:has-text("Continue")'
MORE_OPTIONS_BUTTON_SELECTOR = 'div.actions-container div ms-chat-turn-options div > button'
COPY_MARKDOWN_BUTTON_SELECTOR = 'button.mat-mdc-menu-item:nth-child(4)'
COPY_MARKDOWN_BUTTON_SELECTOR_ALT = 'div[role="menu"] button:has-text("Copy Markdown")'
MAX_OUTPUT_TOKENS_SELECTOR = 'input[aria-label="Maximum output tokens"]'
STOP_SEQUENCE_INPUT_SELECTOR = 'input[aria-label="Add stop token"]'
MAT_CHIP_REMOVE_BUTTON_SELECTOR = 'mat-chip-set mat-chip-row button[aria-label*="Remove"]'
TOP_P_INPUT_SELECTOR = 'div.settings-item-column:has(h3:text-is("Top P")) input[type="number"].slider-input'
TEMPERATURE_INPUT_SELECTOR = 'div[data-test-id="temperatureSliderContainer"] input[type="number"].slider-input'

# --- 编辑相关选择器 ---
EDIT_MESSAGE_BUTTON_SELECTOR = 'ms-chat-turn:last-child .actions-container button.toggle-edit-button'
MESSAGE_TEXTAREA_SELECTOR = 'ms-chat-turn:last-child ms-text-chunk ms-autosize-textarea'
FINISH_EDIT_BUTTON_SELECTOR = 'ms-chat-turn:last-child .actions-container button.toggle-edit-button[aria-label="Stop editing"]'
