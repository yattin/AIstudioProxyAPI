# logging_utils.py - 日志管理模块
# 包含日志设置、WebSocket 日志管理和流重定向功能

import asyncio
import datetime
import json
import logging
import logging.handlers
import os
import sys
from typing import Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from config import LOG_DIR, APP_LOG_FILE_PATH


class StreamToLogger:
    """将标准输出/错误流重定向到日志系统"""
    
    def __init__(self, logger_instance, log_level=logging.INFO):
        self.logger = logger_instance
        self.log_level = log_level
        self.linebuf = ''

    def write(self, buf):
        try:
            temp_linebuf = self.linebuf + buf
            self.linebuf = ''
            for line in temp_linebuf.splitlines(True):
                if line.endswith(('\n', '\r')):
                    self.logger.log(self.log_level, line.rstrip())
                else:
                    self.linebuf += line
        except Exception as e:
            print(f"StreamToLogger 错误: {e}", file=sys.__stderr__)

    def flush(self):
        try:
            if self.linebuf != '':
                self.logger.log(self.log_level, self.linebuf.rstrip())
            self.linebuf = ''
        except Exception as e:
            print(f"StreamToLogger Flush 错误: {e}", file=sys.__stderr__)

    def isatty(self):
        return False


class WebSocketConnectionManager:
    """WebSocket 连接管理器，用于实时日志推送"""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, client_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger = logging.getLogger("AIStudioProxyServer")
        logger.info(f"WebSocket 日志客户端已连接: {client_id}")
        try:
            await websocket.send_text(json.dumps({
                "type": "connection_status",
                "status": "connected",
                "message": "已连接到实时日志流。",
                "timestamp": datetime.datetime.now().isoformat()
            }))
        except Exception as e:
            logger.warning(f"向 WebSocket 客户端 {client_id} 发送欢迎消息失败: {e}")

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger = logging.getLogger("AIStudioProxyServer")
            logger.info(f"WebSocket 日志客户端已断开: {client_id}")

    async def broadcast(self, message: str):
        if not self.active_connections:
            return
        disconnected_clients = []
        active_conns_copy = list(self.active_connections.items())
        logger = logging.getLogger("AIStudioProxyServer")
        
        for client_id, connection in active_conns_copy:
            try:
                await connection.send_text(message)
            except WebSocketDisconnect:
                logger.info(f"[WS Broadcast] 客户端 {client_id} 在广播期间断开连接。")
                disconnected_clients.append(client_id)
            except RuntimeError as e:
                if "Connection is closed" in str(e):
                    logger.info(f"[WS Broadcast] 客户端 {client_id} 的连接已关闭。")
                    disconnected_clients.append(client_id)
                else:
                    logger.error(f"广播到 WebSocket {client_id} 时发生运行时错误: {e}")
                    disconnected_clients.append(client_id)
            except Exception as e:
                logger.error(f"广播到 WebSocket {client_id} 时发生未知错误: {e}")
                disconnected_clients.append(client_id)
        
        if disconnected_clients:
            for client_id_to_remove in disconnected_clients:
                self.disconnect(client_id_to_remove)


class WebSocketLogHandler(logging.Handler):
    """WebSocket 日志处理器，将日志消息推送到 WebSocket 客户端"""
    
    def __init__(self, manager: WebSocketConnectionManager):
        super().__init__()
        self.manager = manager
        self.formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    def emit(self, record: logging.LogRecord):
        if self.manager and self.manager.active_connections:
            try:
                log_entry_str = self.format(record)
                try:
                    current_loop = asyncio.get_running_loop()
                    current_loop.create_task(self.manager.broadcast(log_entry_str))
                except RuntimeError:
                    pass
            except Exception as e:
                print(f"WebSocketLogHandler 错误: 广播日志失败 - {e}", file=sys.__stderr__)


# 全局 WebSocket 管理器实例
log_ws_manager: Optional[WebSocketConnectionManager] = None


def setup_server_logging(log_level_name: str = "INFO", redirect_print_str: str = "false"):
    """设置服务器日志系统"""
    global log_ws_manager
    
    log_level = getattr(logging, log_level_name.upper(), logging.INFO)
    redirect_print = redirect_print_str.lower() in ('true', '1', 'yes')
    
    # 创建必要的目录
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # 获取日志器
    logger = logging.getLogger("AIStudioProxyServer")
    
    # 设置日志格式
    file_log_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s'
    )
    
    # 清除现有处理器
    if logger.hasHandlers():
        logger.handlers.clear()
    
    logger.setLevel(log_level)
    logger.propagate = False
    
    # 删除旧的日志文件
    if os.path.exists(APP_LOG_FILE_PATH):
        try:
            os.remove(APP_LOG_FILE_PATH)
        except OSError as e:
            print(f"警告 (setup_server_logging): 尝试移除旧的 app.log 文件 '{APP_LOG_FILE_PATH}' 失败: {e}。将依赖 mode='w' 进行截断。", file=sys.__stderr__)
    
    # 文件处理器
    file_handler = logging.handlers.RotatingFileHandler(
        APP_LOG_FILE_PATH, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8', mode='w'
    )
    file_handler.setFormatter(file_log_formatter)
    logger.addHandler(file_handler)
    
    # WebSocket 处理器
    if log_ws_manager is None:
        print("严重警告 (setup_server_logging): log_ws_manager 未初始化！WebSocket 日志功能将不可用。", file=sys.__stderr__)
    else:
        ws_handler = WebSocketLogHandler(log_ws_manager)
        ws_handler.setLevel(logging.INFO)
        logger.addHandler(ws_handler)
    
    # 控制台处理器
    console_server_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s [SERVER] - %(message)s')
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(console_server_log_formatter)
    console_handler.setLevel(log_level)
    logger.addHandler(console_handler)
    
    # 保存原始流
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    # 重定向 print 输出（如果启用）
    if redirect_print:
        print("--- 注意：server.py 正在将其 print 输出重定向到日志系统 (文件、WebSocket 和控制台记录器) ---", file=original_stderr)
        stdout_redirect_logger = logging.getLogger("AIStudioProxyServer.stdout")
        stdout_redirect_logger.setLevel(logging.INFO)
        stdout_redirect_logger.propagate = True
        sys.stdout = StreamToLogger(stdout_redirect_logger, logging.INFO)
        
        stderr_redirect_logger = logging.getLogger("AIStudioProxyServer.stderr")
        stderr_redirect_logger.setLevel(logging.ERROR)
        stderr_redirect_logger.propagate = True
        sys.stderr = StreamToLogger(stderr_redirect_logger, logging.ERROR)
    else:
        print("--- server.py 的 print 输出未被重定向到日志系统 (将使用原始 stdout/stderr) ---", file=original_stderr)
    
    # 设置其他库的日志级别
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.ERROR)
    
    # 记录初始化信息
    logger.info("=" * 5 + " AIStudioProxyServer 日志系统已在 lifespan 中初始化 " + "=" * 5)
    logger.info(f"日志级别设置为: {logging.getLevelName(log_level)}")
    logger.info(f"日志文件路径: {APP_LOG_FILE_PATH}")
    logger.info(f"控制台日志处理器已添加。")
    logger.info(f"Print 重定向 (由 SERVER_REDIRECT_PRINT 环境变量控制): {'启用' if redirect_print else '禁用'}")
    
    return original_stdout, original_stderr


def restore_original_streams(original_stdout, original_stderr):
    """恢复原始的标准输出和错误流"""
    sys.stdout = original_stdout
    sys.stderr = original_stderr
    print("已恢复 server.py 的原始 stdout 和 stderr 流。", file=sys.__stderr__)
