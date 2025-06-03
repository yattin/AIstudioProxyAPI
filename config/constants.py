"""
常量配置模块
包含所有固定的常量定义，如模型名称、标记符、文件名等
"""

# --- 模型相关常量 ---
MODEL_NAME = 'AI-Studio_Proxy_API'
CHAT_COMPLETION_ID_PREFIX = 'chatcmpl-'
DEFAULT_FALLBACK_MODEL_ID = "no model list"

# --- 默认参数值 ---
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_OUTPUT_TOKENS = 65536
DEFAULT_TOP_P = 0.95
DEFAULT_STOP_SEQUENCES = []  # 空列表表示无停止序列

# --- URL模式 ---
AI_STUDIO_URL_PATTERN = 'aistudio.google.com/'
MODELS_ENDPOINT_URL_CONTAINS = "MakerSuiteService/ListModels"

# --- 输入标记符 ---
USER_INPUT_START_MARKER_SERVER = "__USER_INPUT_START__"
USER_INPUT_END_MARKER_SERVER = "__USER_INPUT_END__"

# --- 文件名常量 ---
EXCLUDED_MODELS_FILENAME = "excluded_models.txt"

# --- 流状态配置 ---
STREAM_TIMEOUT_LOG_STATE = {
    "consecutive_timeouts": 0,
    "last_error_log_time": 0.0,  # 使用 time.monotonic()
    "suppress_until_time": 0.0,  # 使用 time.monotonic()
    "max_initial_errors": 3,
    "warning_interval_after_suppress": 60.0,  # seconds
    "suppress_duration_after_initial_burst": 400.0,  # seconds
} 