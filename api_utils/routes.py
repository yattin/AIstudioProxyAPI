"""
FastAPI路由处理器模块
包含所有API端点的处理函数
"""

import asyncio
import json
import os
import random
import time
import uuid
import datetime
from typing import Dict, List, Any
from asyncio import Queue, Future

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse

# --- 配置模块导入 ---
from config import *

# --- models模块导入 ---
from models import ChatCompletionRequest

# --- browser_utils模块导入 ---
from browser_utils import _handle_model_list_response


def get_global_vars():
    """获取全局变量的引用"""
    from server import (
        logger, log_ws_manager, request_queue, processing_lock, worker_task,
        is_initializing, is_playwright_ready, is_browser_connected, is_page_ready,
        page_instance, model_list_fetch_event, parsed_model_list, excluded_model_ids,
        current_ai_studio_model_id
    )
    return {
        'logger': logger,
        'log_ws_manager': log_ws_manager,
        'request_queue': request_queue,
        'processing_lock': processing_lock,
        'worker_task': worker_task,
        'is_initializing': is_initializing,
        'is_playwright_ready': is_playwright_ready,
        'is_browser_connected': is_browser_connected,
        'is_page_ready': is_page_ready,
        'page_instance': page_instance,
        'model_list_fetch_event': model_list_fetch_event,
        'parsed_model_list': parsed_model_list,
        'excluded_model_ids': excluded_model_ids,
        'current_ai_studio_model_id': current_ai_studio_model_id
    }


# --- 静态文件端点 ---
async def read_index():
    """返回主页面"""
    index_html_path = os.path.join(os.path.dirname(__file__), "..", "index.html")
    if not os.path.exists(index_html_path):
        logger = get_global_vars()['logger']
        logger.error(f"index.html not found at {index_html_path}")
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_html_path)


async def get_css():
    """返回CSS文件"""
    css_path = os.path.join(os.path.dirname(__file__), "..", "webui.css")
    if not os.path.exists(css_path):
        logger = get_global_vars()['logger']
        logger.error(f"webui.css not found at {css_path}")
        raise HTTPException(status_code=404, detail="webui.css not found")
    return FileResponse(css_path, media_type="text/css")


async def get_js():
    """返回JavaScript文件"""
    js_path = os.path.join(os.path.dirname(__file__), "..", "webui.js")
    if not os.path.exists(js_path):
        logger = get_global_vars()['logger']
        logger.error(f"webui.js not found at {js_path}")
        raise HTTPException(status_code=404, detail="webui.js not found")
    return FileResponse(js_path, media_type="application/javascript")


# --- API信息端点 ---
async def get_api_info(request: Request):
    """返回API信息"""
    globals_dict = get_global_vars()
    current_ai_studio_model_id = globals_dict['current_ai_studio_model_id']
    
    server_port = request.url.port
    if not server_port and hasattr(request.app.state, 'server_port'):
        server_port = request.app.state.server_port
    if not server_port:
        server_port = os.environ.get('SERVER_PORT_INFO', '8000')
    
    host = request.headers.get('host') or f"127.0.0.1:{server_port}"
    scheme = request.headers.get('x-forwarded-proto', 'http')
    base_url = f"{scheme}://{host}"
    api_base = f"{base_url}/v1"
    effective_model_name = current_ai_studio_model_id if current_ai_studio_model_id else MODEL_NAME
    
    return JSONResponse(content={
        "model_name": effective_model_name,
        "api_base_url": api_base,
        "server_base_url": base_url,
        "api_key_required": False,
        "message": "API Key is not required."
    })


# --- 健康检查端点 ---
async def health_check():
    """健康检查"""
    globals_dict = get_global_vars()
    logger = globals_dict['logger']
    worker_task = globals_dict['worker_task']
    is_initializing = globals_dict['is_initializing']
    is_playwright_ready = globals_dict['is_playwright_ready']
    is_browser_connected = globals_dict['is_browser_connected']
    is_page_ready = globals_dict['is_page_ready']
    request_queue = globals_dict['request_queue']
    processing_lock = globals_dict['processing_lock']
    
    is_worker_running = bool(worker_task and not worker_task.done())
    launch_mode = os.environ.get('LAUNCH_MODE', 'unknown')
    browser_page_critical = launch_mode != "direct_debug_no_browser"
    
    core_ready_conditions = [not is_initializing, is_playwright_ready]
    if browser_page_critical:
        core_ready_conditions.extend([is_browser_connected, is_page_ready])
    
    is_core_ready = all(core_ready_conditions)
    status_val = "OK" if is_core_ready and is_worker_running else "Error"
    q_size = request_queue.qsize() if request_queue else -1
    
    status_message_parts = []
    if is_initializing: 
        status_message_parts.append("初始化进行中")
    if not is_playwright_ready: 
        status_message_parts.append("Playwright 未就绪")
    if browser_page_critical:
        if not is_browser_connected: 
            status_message_parts.append("浏览器未连接")
        if not is_page_ready: 
            status_message_parts.append("页面未就绪")
    if not is_worker_running: 
        status_message_parts.append("Worker 未运行")
    
    status = {
        "status": status_val,
        "message": "",
        "details": {
            "playwrightReady": is_playwright_ready,
            "browserConnected": is_browser_connected,
            "pageReady": is_page_ready,
            "initializing": is_initializing,
            "workerRunning": is_worker_running,
            "queueLength": q_size,
            "launchMode": launch_mode,
            "browserAndPageCritical": browser_page_critical
        }
    }
    
    if status_val == "OK":
        status["message"] = f"服务运行中;队列长度: {q_size}。"
        return JSONResponse(content=status, status_code=200)
    else:
        status["message"] = f"服务不可用;问题: {(', '.join(status_message_parts) if status_message_parts else '未知原因')}. 队列长度: {q_size}."
        return JSONResponse(content=status, status_code=503)


# --- 模型列表端点 ---
async def list_models():
    """获取模型列表"""
    globals_dict = get_global_vars()
    logger = globals_dict['logger']
    model_list_fetch_event = globals_dict['model_list_fetch_event']
    page_instance = globals_dict['page_instance']
    parsed_model_list = globals_dict['parsed_model_list']
    excluded_model_ids = globals_dict['excluded_model_ids']
    
    logger.info("[API] 收到 /v1/models 请求。")
    
    if not model_list_fetch_event.is_set() and page_instance and not page_instance.is_closed():
        logger.info("/v1/models: 模型列表事件未设置或列表为空，尝试页面刷新以触发捕获...")
        try:
            listener_attached = False
            if hasattr(page_instance, '_events') and "response" in page_instance._events:
                for handler_slot_or_func in page_instance._events["response"]:
                    actual_handler = getattr(handler_slot_or_func, 'handler', handler_slot_or_func)
                    if actual_handler == _handle_model_list_response:
                        listener_attached = True
                        break
            
            if not listener_attached:
                logger.info("/v1/models: 响应监听器似乎不存在或已被移除，尝试重新添加。")
                page_instance.on("response", _handle_model_list_response)
            
            await page_instance.reload(wait_until="domcontentloaded", timeout=20000)
            logger.info(f"页面已刷新。等待模型列表事件 (最多10秒)...")
            await asyncio.wait_for(model_list_fetch_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("/v1/models: 刷新后等待模型列表事件超时。")
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


# --- 聊天完成端点 ---
async def chat_completions(request: ChatCompletionRequest, http_request: Request):
    """处理聊天完成请求"""
    globals_dict = get_global_vars()
    logger = globals_dict['logger']
    request_queue = globals_dict['request_queue']
    worker_task = globals_dict['worker_task']
    is_initializing = globals_dict['is_initializing']
    is_playwright_ready = globals_dict['is_playwright_ready']
    is_browser_connected = globals_dict['is_browser_connected']
    is_page_ready = globals_dict['is_page_ready']
    
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    logger.info(f"[{req_id}] 收到 /v1/chat/completions 请求 (Stream={request.stream})")
    logger.debug(f"[{req_id}] 完整请求参数: {request.model_dump_json(indent=2)}")
    
    launch_mode = os.environ.get('LAUNCH_MODE', 'unknown')
    browser_page_critical = launch_mode != "direct_debug_no_browser"
    
    service_unavailable = is_initializing or \
                          not is_playwright_ready or \
                          (browser_page_critical and (not is_page_ready or not is_browser_connected)) or \
                          not worker_task or worker_task.done()
    
    if service_unavailable:
        status_code = 503
        error_details = []
        if is_initializing: 
            error_details.append("初始化进行中")
        if not is_playwright_ready: 
            error_details.append("Playwright 未就绪")
        if browser_page_critical:
            if not is_browser_connected: 
                error_details.append("浏览器未连接")
            if not is_page_ready: 
                error_details.append("页面未就绪")
        if not worker_task or worker_task.done(): 
            error_details.append("Worker 未运行")
        
        detail = f"[{req_id}] 服务当前不可用 ({', '.join(error_details)}). 请稍后重试."
        logger.error(f"[{req_id}] 服务不可用详情: {detail}")
        raise HTTPException(status_code=status_code, detail=detail, headers={"Retry-After": "30"})
    
    result_future = Future()
    request_item = {
        "req_id": req_id, 
        "request_data": request, 
        "http_request": http_request,
        "result_future": result_future, 
        "enqueue_time": time.time(), 
        "cancelled": False
    }
    
    await request_queue.put(request_item)
    logger.info(f"[{req_id}] 请求已加入队列 (当前队列长度: {request_queue.qsize()})")
    
    try:
        timeout_seconds = RESPONSE_COMPLETION_TIMEOUT / 1000 + 120
        result = await asyncio.wait_for(result_future, timeout=timeout_seconds)
        logger.info(f"[{req_id}] Worker 处理完成，返回结果。")
        return result
    except asyncio.TimeoutError:
        logger.error(f"[{req_id}] ❌ 等待 Worker 响应超时 ({timeout_seconds}s)。")
        raise HTTPException(status_code=504, detail=f"[{req_id}] Request processing timed out waiting for worker response.")
    except asyncio.CancelledError:
        logger.info(f"[{req_id}] 请求 Future 被取消 (可能由客户端断开连接触发)。")
        if not result_future.done() or result_future.exception() is None:
            raise HTTPException(status_code=499, detail=f"[{req_id}] Request cancelled by client or server.")
        else:
            raise result_future.exception()
    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        logger.exception(f"[{req_id}] ❌ 等待 Worker 响应时发生意外错误")
        raise HTTPException(status_code=500, detail=f"[{req_id}] Unexpected error waiting for worker response: {e}")


# --- 取消请求相关 ---
async def cancel_queued_request(req_id: str) -> bool:
    """取消队列中的请求"""
    globals_dict = get_global_vars()
    logger = globals_dict['logger']
    request_queue = globals_dict['request_queue']
    
    cancelled = False
    items_to_requeue = []
    found = False
    
    try:
        while True:
            item = request_queue.get_nowait()
            if item.get("req_id") == req_id and not item.get("cancelled", False):
                logger.info(f"[{req_id}] 在队列中找到请求，标记为已取消。")
                item["cancelled"] = True
                item_future = item.get("result_future")
                if item_future and not item_future.done():
                    item_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] Request cancelled by API call."))
                items_to_requeue.append(item)
                cancelled = True
                found = True
            else:
                items_to_requeue.append(item)
    except asyncio.QueueEmpty:
        pass
    finally:
        for item in items_to_requeue:
            await request_queue.put(item)
    
    return cancelled


async def cancel_request(req_id: str):
    """取消请求端点"""
    globals_dict = get_global_vars()
    logger = globals_dict['logger']
    
    logger.info(f"[{req_id}] 收到取消请求。")
    cancelled = await cancel_queued_request(req_id)
    
    if cancelled:
        return JSONResponse(content={"success": True, "message": f"Request {req_id} marked as cancelled in queue."})
    else:
        return JSONResponse(
            content={"success": False, "message": f"Request {req_id} not found in queue (it might be processing or already finished)."},
            status_code=404
        )


# --- 队列状态端点 ---
async def get_queue_status():
    """获取队列状态"""
    globals_dict = get_global_vars()
    request_queue = globals_dict['request_queue']
    processing_lock = globals_dict['processing_lock']
    
    queue_items = []
    items_to_requeue = []
    
    try:
        while True:
            item = request_queue.get_nowait()
            items_to_requeue.append(item)
            req_id = item.get("req_id", "unknown")
            timestamp = item.get("enqueue_time", 0)
            is_streaming = item.get("request_data").stream if hasattr(item.get("request_data", {}), "stream") else False
            cancelled = item.get("cancelled", False)
            
            queue_items.append({
                "req_id": req_id, 
                "enqueue_time": timestamp,
                "wait_time_seconds": round(time.time() - timestamp, 2) if timestamp else None,
                "is_streaming": is_streaming, 
                "cancelled": cancelled
            })
    except asyncio.QueueEmpty:
        pass
    finally:
        for item in items_to_requeue:
            await request_queue.put(item)
    
    return JSONResponse(content={
        "queue_length": len(queue_items),
        "is_processing_locked": processing_lock.locked(),
        "items": sorted(queue_items, key=lambda x: x.get("enqueue_time", 0))
    })


# --- WebSocket日志端点 ---
async def websocket_log_endpoint(websocket: WebSocket):
    """WebSocket日志端点"""
    globals_dict = get_global_vars()
    logger = globals_dict['logger']
    log_ws_manager = globals_dict['log_ws_manager']
    
    if not log_ws_manager:
        try:
            await websocket.accept()
            await websocket.send_text(json.dumps({
                "type": "error", 
                "status": "disconnected",
                "message": "日志服务内部错误 (管理器未初始化)。",
                "timestamp": datetime.datetime.now().isoformat()
            }))
            await websocket.close(code=1011)
        except Exception: 
            pass
        return
    
    client_id = str(uuid.uuid4())
    try:
        await log_ws_manager.connect(client_id, websocket)
        while True:
            data = await websocket.receive_text()
            if data.lower() == "ping":
                await websocket.send_text(json.dumps({
                    "type": "pong", 
                    "timestamp": datetime.datetime.now().isoformat()
                }))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"日志 WebSocket (客户端 {client_id}) 发生异常: {e}", exc_info=True)
    finally:
        if log_ws_manager:
            log_ws_manager.disconnect(client_id) 