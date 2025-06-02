# routes.py - API 路由模块
# 定义所有 FastAPI 路由和端点处理函数

import asyncio
import json
import logging
import os
import uuid
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse

from config import (
    MODEL_NAME, DEFAULT_FALLBACK_MODEL_ID,
    DEBUG_LOGS_ENABLED, TRACE_LOGS_ENABLED
)
from utils import ChatCompletionRequest, generate_random_string
from model_manager import (
    parsed_model_list, wait_for_model_list, get_all_models,
    initialize_model_manager, handle_initial_model_state_and_storage
)
from queue_manager import add_request_to_queue, stream_response_generator
from logging_utils import log_ws_manager

logger = logging.getLogger("AIStudioProxyServer")


def setup_routes(app: FastAPI):
    """设置所有路由"""
    
    @app.get("/")
    async def root():
        """根路径"""
        return {"message": "AI Studio Proxy Server is running", "status": "ok"}
    
    
    @app.get("/v1/models")
    async def list_models():
        """列出可用模型（与重构前完全一致的实现）"""
        logger.info("[API] 收到 /v1/models 请求。")

        from browser_manager import page_instance
        from model_manager import model_list_fetch_event, excluded_model_ids, handle_model_list_response
        from playwright.async_api import Error as PlaywrightAsyncError
        import time

        if not model_list_fetch_event.is_set() and page_instance and not page_instance.is_closed():
            logger.info("/v1/models: 模型列表事件未设置或列表为空，尝试页面刷新以触发捕获...")
            try:
                listener_attached = False
                if hasattr(page_instance, '_events') and "response" in page_instance._events:
                    for handler_slot_or_func in page_instance._events["response"]:
                        actual_handler = getattr(handler_slot_or_func, 'handler', handler_slot_or_func)
                        if actual_handler == handle_model_list_response:
                            listener_attached = True
                            break
                if not listener_attached:
                    logger.info("/v1/models: 响应监听器似乎不存在或已被移除，尝试重新添加。")
                    page_instance.on("response", handle_model_list_response)
                await page_instance.reload(wait_until="domcontentloaded", timeout=20000)
                logger.info(f"页面已刷新。等待模型列表事件 (最多10秒)...")
                await asyncio.wait_for(model_list_fetch_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("/v1/models: 刷新后等待模型列表事件超时。")
            except PlaywrightAsyncError as reload_err:
                logger.error(f"/v1/models: 刷新页面失败: {reload_err}")
            except Exception as e:
                logger.error(f"/v1/models: 尝试触发模型列表捕获时发生错误: {e}")
            finally:
                if not model_list_fetch_event.is_set():
                    logger.info("/v1/models: 尝试捕获后，强制设置模型列表事件。")
                    model_list_fetch_event.set()

        if parsed_model_list:
            final_model_list = [m for m in parsed_model_list if m.get("id") not in excluded_model_ids]
            logger.info(f"返回过滤后的 {len(final_model_list)} 个模型 (原缓存 {len(parsed_model_list)} 个)。排除的有: {excluded_model_ids.intersection(set(m.get('id') for m in parsed_model_list))}")
            return {"object": "list", "data": final_model_list}
        else:
            logger.warning("模型列表为空或未成功获取。返回默认后备模型。")
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
    
    
    @app.post("/v1/chat/completions")
    async def chat_completions(request: ChatCompletionRequest, http_request: Request):
        """聊天完成端点"""
        req_id = generate_random_string(7)
        
        try:
            logger.info(f"[{req_id}] 收到聊天完成请求")
            
            if DEBUG_LOGS_ENABLED:
                logger.debug(f"[{req_id}] 请求详情: model={request.model}, stream={request.stream}, "
                           f"messages_count={len(request.messages)}")
            
            if TRACE_LOGS_ENABLED:
                logger.debug(f"[{req_id}] 完整请求: {request.dict()}")
            
            # 检查是否为流式请求
            if request.stream:
                logger.info(f"[{req_id}] 处理流式请求")
                return StreamingResponse(
                    stream_response_generator(request, req_id),
                    media_type="text/plain",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "Content-Type": "text/plain; charset=utf-8"
                    }
                )
            else:
                logger.info(f"[{req_id}] 处理非流式请求")
                response = await add_request_to_queue(request, req_id)
                
                if isinstance(response, dict) and "error" in response:
                    return JSONResponse(
                        status_code=500,
                        content=response
                    )
                
                return response
                
        except Exception as e:
            logger.error(f"[{req_id}] 处理聊天完成请求时出错: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": f"[{req_id}] 处理请求时发生错误: {str(e)}",
                        "type": "internal_server_error"
                    }
                }
            )
    
    
    @app.get("/health")
    async def health_check():
        """健康检查端点"""
        from browser_manager import is_page_ready, is_browser_connected
        
        status = {
            "status": "ok",
            "browser_connected": is_browser_connected,
            "page_ready": is_page_ready,
            "models_loaded": len(parsed_model_list) > 0,
            "model_count": len(parsed_model_list)
        }
        
        if not is_browser_connected or not is_page_ready:
            status["status"] = "degraded"
            return JSONResponse(status_code=503, content=status)
        
        return status
    
    
    @app.get("/debug/info")
    async def debug_info():
        """调试信息端点"""
        from browser_manager import is_browser_connected, is_page_ready, is_playwright_ready
        from model_manager import current_ai_studio_model_id

        info = {
            "playwright_ready": is_playwright_ready,
            "browser_connected": is_browser_connected,
            "page_ready": is_page_ready,
            "current_model": current_ai_studio_model_id,
            "models_count": len(parsed_model_list),
            "debug_logs": DEBUG_LOGS_ENABLED,
            "trace_logs": TRACE_LOGS_ENABLED
        }
        
        return info
    
    
    @app.websocket("/ws/logs")
    async def websocket_logs(websocket: WebSocket):
        """WebSocket 日志端点"""
        client_id = str(uuid.uuid4())
        
        try:
            await log_ws_manager.connect(client_id, websocket)
            
            # 保持连接活跃
            while True:
                try:
                    # 等待客户端消息（心跳或其他）
                    await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    # 发送心跳
                    await websocket.send_text(json.dumps({
                        "type": "heartbeat",
                        "timestamp": asyncio.get_event_loop().time()
                    }))
                except WebSocketDisconnect:
                    break
                    
        except WebSocketDisconnect:
            logger.info(f"WebSocket 客户端 {client_id} 断开连接")
        except Exception as e:
            logger.error(f"WebSocket 连接 {client_id} 出错: {e}")
        finally:
            log_ws_manager.disconnect(client_id)
    
    
    @app.get("/logs")
    async def get_log_file():
        """获取日志文件"""
        from config import APP_LOG_FILE_PATH
        
        if os.path.exists(APP_LOG_FILE_PATH):
            return FileResponse(
                APP_LOG_FILE_PATH,
                media_type="text/plain",
                filename="app.log"
            )
        else:
            raise HTTPException(status_code=404, detail="日志文件不存在")
    
    
    @app.post("/admin/reload-models")
    async def reload_models():
        """重新加载模型列表（管理员功能）"""
        try:
            # 重置模型列表事件
            from model_manager import model_list_fetch_event
            model_list_fetch_event.clear()
            
            # 等待新的模型列表
            success = await wait_for_model_list(timeout=30.0)
            
            if success:
                return {
                    "status": "success",
                    "message": f"模型列表已重新加载，共 {len(parsed_model_list)} 个模型"
                }
            else:
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "error",
                        "message": "重新加载模型列表失败"
                    }
                )
                
        except Exception as e:
            logger.error(f"重新加载模型列表时出错: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": f"重新加载模型列表失败: {str(e)}"
                }
            )
    
    
    @app.post("/admin/clear-cache")
    async def clear_cache():
        """清除参数缓存（管理员功能）"""
        try:
            from queue_manager import page_params_cache
            # 清除所有缓存参数（与重构前一致的实现）
            page_params_cache.clear()

            return {
                "status": "success",
                "message": "参数缓存已清除"
            }
            
        except Exception as e:
            logger.error(f"清除缓存时出错: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": f"清除缓存失败: {str(e)}"
                }
            )
    
    
    logger.info("所有路由已设置完成")
