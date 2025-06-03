"""
主要设置配置模块
包含环境变量配置、路径配置、代理配置等运行时设置
"""

import os
from typing import Optional, Dict

# --- 全局日志控制配置 ---
DEBUG_LOGS_ENABLED = os.environ.get('DEBUG_LOGS_ENABLED', 'false').lower() in ('true', '1', 'yes')
TRACE_LOGS_ENABLED = os.environ.get('TRACE_LOGS_ENABLED', 'false').lower() in ('true', '1', 'yes')

# --- 认证相关配置 ---
AUTO_SAVE_AUTH = os.environ.get('AUTO_SAVE_AUTH', '').lower() in ('1', 'true', 'yes')
AUTH_SAVE_TIMEOUT = int(os.environ.get('AUTH_SAVE_TIMEOUT', '30'))

# --- 路径配置 ---
AUTH_PROFILES_DIR = os.path.join(os.path.dirname(__file__), '..', 'auth_profiles')
ACTIVE_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'active')
SAVED_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'saved')
LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')
APP_LOG_FILE_PATH = os.path.join(LOG_DIR, 'app.log')

# --- 代理配置 ---
PROXY_SERVER_ENV = "http://127.0.0.1:3120/"
STREAM_PROXY_SERVER_ENV = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
NO_PROXY_ENV = os.environ.get('NO_PROXY')

# --- Playwright代理设置 ---
PLAYWRIGHT_PROXY_SETTINGS: Optional[Dict[str, str]] = None
if PROXY_SERVER_ENV:
    PLAYWRIGHT_PROXY_SETTINGS = {'server': PROXY_SERVER_ENV}
    if NO_PROXY_ENV:
        PLAYWRIGHT_PROXY_SETTINGS['bypass'] = NO_PROXY_ENV.replace(',', ';')

def get_environment_variable(key: str, default: str = '') -> str:
    """获取环境变量值"""
    return os.environ.get(key, default)

def get_boolean_env(key: str, default: bool = False) -> bool:
    """获取布尔型环境变量"""
    value = os.environ.get(key, '').lower()
    if default:
        return value not in ('false', '0', 'no', 'off')
    else:
        return value in ('true', '1', 'yes', 'on')

def get_int_env(key: str, default: int = 0) -> int:
    """获取整型环境变量"""
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default 