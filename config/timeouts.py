"""
超时和时间配置模块
包含所有超时时间、轮询间隔等时间相关配置
"""

# --- 响应等待配置 ---
RESPONSE_COMPLETION_TIMEOUT = 300000  # 5 minutes total timeout (in ms)
INITIAL_WAIT_MS_BEFORE_POLLING = 500  # ms, initial wait before polling for response completion

# --- 轮询间隔配置 ---
POLLING_INTERVAL = 300  # ms
POLLING_INTERVAL_STREAM = 180  # ms

# --- 静默超时配置 ---
SILENCE_TIMEOUT_MS = 60000  # ms

# --- 页面操作超时配置 ---
POST_SPINNER_CHECK_DELAY_MS = 500
FINAL_STATE_CHECK_TIMEOUT_MS = 1500
POST_COMPLETION_BUFFER = 700

# --- 清理聊天相关超时 ---
CLEAR_CHAT_VERIFY_TIMEOUT_MS = 5000
CLEAR_CHAT_VERIFY_INTERVAL_MS = 2000

# --- 点击和剪贴板操作超时 ---
CLICK_TIMEOUT_MS = 3000
CLIPBOARD_READ_TIMEOUT_MS = 3000

# --- 元素等待超时 ---
WAIT_FOR_ELEMENT_TIMEOUT_MS = 10000  # Timeout for waiting for elements like overlays

# --- 流相关配置 ---
PSEUDO_STREAM_DELAY = 0.01 