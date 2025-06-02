# 重构后的 server.py - 主服务器文件
# 负责应用启动、生命周期管理和模块协调

import asyncio
import os
import sys
import multiprocessing
from contextlib import asynccontextmanager

from fastapi import FastAPI

# 导入重构后的模块
from config import *
from logging_utils import (
    setup_server_logging, restore_original_streams,
    WebSocketConnectionManager, log_ws_manager
)
from browser_manager import (
    initialize_browser_and_page, cleanup_browser_and_page
)
from model_manager import (
    parsed_model_list, wait_for_model_list, initialize_model_manager,
    handle_initial_model_state_and_storage
)
from queue_manager import start_queue_worker, cleanup_queue_worker
from routes import setup_routes
import stream

# 初始化全局日志管理器
if log_ws_manager is None:
    import logging_utils
    logging_utils.log_ws_manager = WebSocketConnectionManager()

import logging
logger = logging.getLogger("AIStudioProxyServer")

# 全局变量用于管理流式代理服务器
stream_proxy_process = None


def start_stream_proxy_server():
    """启动流式代理服务器"""
    global stream_proxy_process

    # 从环境变量获取流式代理端口
    stream_port = int(os.environ.get('STREAM_PORT', '3120'))

    # 如果端口为0，则禁用流式代理
    if stream_port == 0:
        logger.info("流式代理服务器已禁用 (STREAM_PORT=0)")
        return False

    try:
        logger.info(f"启动流式代理服务器 (端口: {stream_port})...")

        # 获取上游代理配置
        upstream_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')

        # 创建流队列（关键修复：与重构前保持一致）
        import config
        config.STREAM_QUEUE = multiprocessing.Queue()
        logger.info("✅ 流队列已创建")

        # 启动流式代理服务器进程（传递队列）
        stream_proxy_process = multiprocessing.Process(
            target=stream.start,
            kwargs={
                'queue': config.STREAM_QUEUE,  # 修复：传递实际的队列而不是None
                'port': stream_port,
                'proxy': upstream_proxy
            }
        )
        stream_proxy_process.start()

        # 设置流进程到配置中
        config.STREAM_PROCESS = stream_proxy_process

        logger.info(f"✅ 流式代理服务器已启动 (PID: {stream_proxy_process.pid}, 端口: {stream_port})")

        # 更新配置以使用启动的代理服务器
        config.PROXY_SERVER_ENV = f"http://127.0.0.1:{stream_port}/"
        config.PLAYWRIGHT_PROXY_SETTINGS = {'server': config.PROXY_SERVER_ENV}

        logger.info(f"✅ 已更新 Playwright 代理配置: {config.PLAYWRIGHT_PROXY_SETTINGS}")
        logger.info(f"✅ 流队列和流进程已正确初始化")

        return True

    except Exception as e:
        logger.error(f"❌ 启动流式代理服务器失败: {e}", exc_info=True)
        return False


def stop_stream_proxy_server():
    """停止流式代理服务器"""
    global stream_proxy_process

    if stream_proxy_process and stream_proxy_process.is_alive():
        try:
            logger.info("正在停止流式代理服务器...")
            stream_proxy_process.terminate()
            stream_proxy_process.join(timeout=5)

            if stream_proxy_process.is_alive():
                logger.warning("流式代理服务器未在超时时间内停止，强制终止...")
                stream_proxy_process.kill()
                stream_proxy_process.join()

            logger.info("✅ 流式代理服务器已停止")

        except Exception as e:
            logger.error(f"❌ 停止流式代理服务器时出错: {e}", exc_info=True)
        finally:
            stream_proxy_process = None

    # 清理流队列和流进程（关键修复：与重构前保持一致）
    import config
    if config.STREAM_QUEUE:
        try:
            # 清空队列
            while not config.STREAM_QUEUE.empty():
                try:
                    config.STREAM_QUEUE.get_nowait()
                except:
                    break
            logger.info("✅ 流队列已清空")
        except Exception as e:
            logger.warning(f"清空流队列时出错: {e}")
        finally:
            config.STREAM_QUEUE = None

    # 清理流进程引用
    config.STREAM_PROCESS = None
    logger.info("✅ 流队列和流进程已清理")


# --- 应用生命周期管理 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理器"""
    logger.info("=" * 50)
    logger.info("🚀 AI Studio Proxy Server 正在启动...")
    logger.info("=" * 50)
    
    # 启动时的初始化
    original_stdout, original_stderr = None, None
    
    try:
        # 设置日志系统
        log_level = os.environ.get('SERVER_LOG_LEVEL', 'INFO')
        redirect_print = os.environ.get('SERVER_REDIRECT_PRINT', 'false')
        original_stdout, original_stderr = setup_server_logging(log_level, redirect_print)

        # 启动流式代理服务器
        stream_success = start_stream_proxy_server()
        if stream_success:
            logger.info("✅ 流式代理服务器启动成功")
            # 等待一下让代理服务器完全启动
            await asyncio.sleep(2)
        else:
            logger.warning("⚠️ 流式代理服务器启动失败，将使用直接连接模式")

        # 初始化模型管理器
        initialize_model_manager()

        # 启动队列工作器
        start_queue_worker()

        # 初始化浏览器和页面
        browser_success = await initialize_browser_and_page()
        
        if browser_success:
            # 等待模型列表加载
            logger.info("等待模型列表加载...")
            model_success = await wait_for_model_list(timeout=30.0)
            
            if model_success:
                logger.info(f"✅ 模型列表加载成功，共 {len(parsed_model_list)} 个模型")
                
                # 处理初始模型状态
                await handle_initial_model_state_and_storage()
            else:
                logger.warning("⚠️ 模型列表加载失败或超时")
        else:
            logger.warning("⚠️ 浏览器初始化失败")
        
        logger.info("=" * 50)
        logger.info("✅ AI Studio Proxy Server 启动完成")
        logger.info("=" * 50)
        
        yield  # 应用运行期间
        
    except Exception as e:
        logger.error(f"❌ 启动过程中发生错误: {e}", exc_info=True)
        raise
    finally:
        # 关闭时的清理
        logger.info("=" * 50)
        logger.info("🛑 AI Studio Proxy Server 正在关闭...")
        logger.info("=" * 50)
        
        try:
            # 清理队列工作器
            await cleanup_queue_worker()

            # 清理浏览器和页面
            await cleanup_browser_and_page()

            # 停止流式代理服务器
            stop_stream_proxy_server()

            # 恢复原始流
            if original_stdout and original_stderr:
                restore_original_streams(original_stdout, original_stderr)

            logger.info("✅ AI Studio Proxy Server 已安全关闭")
            
        except Exception as e:
            logger.error(f"❌ 关闭过程中发生错误: {e}", exc_info=True)


# --- FastAPI 应用创建 ---
def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    app = FastAPI(
        title="AI Studio Proxy Server",
        description="AI Studio 代理服务器，提供 OpenAI 兼容的 API",
        version="1.0.0",
        lifespan=lifespan
    )
    
    # 设置路由
    setup_routes(app)
    
    return app


# --- 应用实例 ---
app = create_app()


if __name__ == "__main__":
    import uvicorn
    
    # 从环境变量获取配置
    host = os.environ.get('SERVER_HOST', '127.0.0.1')
    port = int(os.environ.get('SERVER_PORT', '8000'))
    
    print(f"启动服务器: {host}:{port}")
    
    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=False,
        log_level="warning"  # 使用我们自己的日志系统
    )
