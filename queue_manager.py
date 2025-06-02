# queue_manager.py - 队列管理模块
# 负责请求队列处理、Worker 任务管理和流式响应处理

import asyncio
import logging
import time
from typing import Optional, Dict, Any, AsyncGenerator

from config import (
    STREAM_QUEUE, STREAM_PROCESS, STREAM_TIMEOUT_LOG_STATE,
    PSEUDO_STREAM_DELAY
)
from utils import (
    ChatCompletionRequest, generate_sse_chunk, generate_sse_stop_chunk,
    generate_sse_error_chunk, ClientDisconnectedError
)


# --- 全局状态变量 ---
request_queue: Optional[asyncio.Queue] = None
processing_lock: Optional[asyncio.Lock] = None
worker_task: Optional[asyncio.Task] = None
page_params_cache: Dict[str, Any] = {}
params_cache_lock: Optional[asyncio.Lock] = None

logger = logging.getLogger("AIStudioProxyServer")


async def queue_worker():
    """队列工作器，处理请求队列中的任务"""
    logger.info("队列工作器已启动")
    
    while True:
        try:
            # 从队列获取请求
            request_data = await request_queue.get()
            if request_data is None:  # 停止信号
                logger.info("队列工作器收到停止信号")
                break
            
            req_id, request, response_future = request_data
            logger.info(f"[{req_id}] 队列工作器开始处理请求")
            
            try:
                # 处理请求（延迟导入避免循环导入）
                import request_processor
                result = await request_processor.process_chat_request(request, req_id)
                response_future.set_result(result)
                logger.info(f"[{req_id}] 队列工作器完成请求处理")
                
            except Exception as e:
                logger.error(f"[{req_id}] 队列工作器处理请求时出错: {e}", exc_info=True)
                response_future.set_exception(e)
            finally:
                request_queue.task_done()
                
        except asyncio.CancelledError:
            logger.info("队列工作器被取消")
            break
        except Exception as e:
            logger.error(f"队列工作器发生未预期错误: {e}", exc_info=True)
            await asyncio.sleep(1)  # 避免快速循环


async def add_request_to_queue(request: ChatCompletionRequest, req_id: str) -> Any:
    """
    将请求添加到队列并等待处理结果
    
    Args:
        request: 聊天完成请求
        req_id: 请求ID
        
    Returns:
        处理结果
    """
    if not request_queue:
        raise RuntimeError("请求队列未初始化")
    
    response_future = asyncio.Future()
    await request_queue.put((req_id, request, response_future))
    logger.info(f"[{req_id}] 请求已添加到队列，当前队列大小: {request_queue.qsize()}")
    
    return await response_future


async def stream_response_generator(request: ChatCompletionRequest, req_id: str) -> AsyncGenerator[str, None]:
    """
    流式响应生成器

    Args:
        request: 聊天完成请求
        req_id: 请求ID

    Yields:
        SSE 格式的响应块
    """
    try:
        logger.info(f"[{req_id}] 开始流式响应生成")

        # 检查是否使用stream服务
        import os
        stream_port = os.environ.get('STREAM_PORT', '3120')
        use_stream = stream_port != '0'

        if use_stream:
            logger.info(f"[{req_id}] 使用流式代理服务进行流式响应")

            # 首先触发 Playwright 操作（非流式模式），然后从流式队列获取数据
            logger.info(f"[{req_id}] 先触发 Playwright 操作以启动响应生成")

            # 创建一个后台任务来处理 Playwright 操作
            import asyncio

            # 创建一个修改过的请求（非流式）来触发 Playwright 操作
            non_stream_request = ChatCompletionRequest(
                model=request.model,
                messages=request.messages,
                temperature=request.temperature,
                max_output_tokens=request.max_output_tokens,
                top_p=request.top_p,
                stop=request.stop,
                stream=False  # 关键：设置为非流式
            )

            # 启动后台任务来触发 Playwright 操作
            playwright_task = asyncio.create_task(add_request_to_queue(non_stream_request, req_id))

            # 等待一小段时间让 Playwright 操作开始
            await asyncio.sleep(0.5)

            # 使用流式代理服务获取数据
            async for chunk in _generate_stream_from_helper(request, req_id):
                yield chunk

            # 确保 Playwright 任务完成
            try:
                await playwright_task
            except Exception as e:
                logger.warning(f"[{req_id}] Playwright 后台任务完成时出错: {e}")
        else:
            logger.info(f"[{req_id}] 使用 Playwright 页面交互进行流式响应")
            # 获取完整响应
            full_response = await add_request_to_queue(request, req_id)

            if isinstance(full_response, dict) and "error" in full_response:
                error_msg = full_response["error"].get("message", "未知错误")
                yield generate_sse_error_chunk(error_msg, req_id)
                return

            # 提取响应内容
            content = ""
            if isinstance(full_response, dict):
                choices = full_response.get("choices", [])
                if choices and len(choices) > 0:
                    message = choices[0].get("message", {})
                    content = message.get("content", "")

            if not content:
                yield generate_sse_error_chunk("响应内容为空", req_id)
                return

            # 模拟流式输出
            model_id = request.model or "unknown"
            words = content.split()
            current_chunk = ""

            for i, word in enumerate(words):
                current_chunk += word
                if i < len(words) - 1:
                    current_chunk += " "

                # 每几个词发送一个块
                if (i + 1) % 3 == 0 or i == len(words) - 1:
                    yield generate_sse_chunk(current_chunk, req_id, model_id)
                    current_chunk = ""
                    await asyncio.sleep(PSEUDO_STREAM_DELAY)

            # 发送结束块
            yield generate_sse_stop_chunk(req_id, model_id)

        logger.info(f"[{req_id}] 流式响应生成完成")

    except ClientDisconnectedError:
        logger.info(f"[{req_id}] 客户端断开连接，停止流式响应")
        return
    except Exception as e:
        logger.error(f"[{req_id}] 流式响应生成出错: {e}", exc_info=True)
        yield generate_sse_error_chunk(f"流式响应生成出错: {str(e)}", req_id)


async def _generate_stream_from_helper(request: ChatCompletionRequest, req_id: str) -> AsyncGenerator[str, None]:
    """
    从流式代理服务生成流式响应

    注意：此函数假设 Playwright 操作已经通过其他方式触发，直接从流式队列获取数据

    Args:
        request: 聊天完成请求
        req_id: 请求ID

    Yields:
        SSE 格式的响应块
    """
    try:
        from utils import generate_random_string, generate_sse_error_chunk
        from config import MODEL_NAME, CHAT_COMPLETION_ID_PREFIX
        import random
        import json

        logger.info(f"[{req_id}] 开始从流式代理服务获取数据")

        # 生成响应ID和时间戳
        model_name_for_stream = request.model or MODEL_NAME
        chat_completion_id = f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}-{random.randint(100, 999)}"
        created_timestamp = int(time.time())

        last_reason_pos = 0
        last_body_pos = 0

        # 从流式代理服务获取数据
        from request_processor import use_stream_response
        async for data in use_stream_response(req_id):
            # 处理思考内容
            if len(data.get("reason", "")) > last_reason_pos:
                reasoning_content = data["reason"][last_reason_pos:]
                output = {
                    "id": chat_completion_id,
                    "object": "chat.completion.chunk",
                    "model": model_name_for_stream,
                    "created": created_timestamp,
                    "choices": [{
                        "delta": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": reasoning_content,
                        },
                        "finish_reason": None,
                        "native_finish_reason": None,
                    }]
                }
                last_reason_pos = len(data["reason"])
                yield f"data: {json.dumps(output, ensure_ascii=False, separators=(',', ':'))}\n\n"

            # 处理主要内容
            elif len(data.get("body", "")) > last_body_pos:
                finish_reason_val = None
                if data.get("done"):
                    finish_reason_val = "stop"

                delta_content = {"role": "assistant", "content": data["body"][last_body_pos:]}
                choice_item = {
                    "delta": delta_content,
                    "finish_reason": finish_reason_val,
                    "native_finish_reason": finish_reason_val,
                }

                # 处理函数调用
                if data.get("done") and data.get("function") and len(data["function"]) > 0:
                    tool_calls_list = []
                    for func_idx, function_call_data in enumerate(data["function"]):
                        tool_calls_list.append({
                            "id": f"call_{generate_random_string(24)}",
                            "index": func_idx,
                            "type": "function",
                            "function": {
                                "name": function_call_data["name"],
                                "arguments": json.dumps(function_call_data["params"]),
                            },
                        })
                    delta_content["tool_calls"] = tool_calls_list
                    choice_item["finish_reason"] = "tool_calls"
                    choice_item["native_finish_reason"] = "tool_calls"
                    delta_content["content"] = None

                output = {
                    "id": chat_completion_id,
                    "object": "chat.completion.chunk",
                    "model": model_name_for_stream,
                    "created": created_timestamp,
                    "choices": [choice_item]
                }
                last_body_pos = len(data["body"])
                yield f"data: {json.dumps(output, ensure_ascii=False, separators=(',', ':'))}\n\n"

            # 处理仅done为true的情况
            elif data.get("done"):
                delta_content = {"role": "assistant"}
                choice_item = {
                    "delta": delta_content,
                    "finish_reason": "stop",
                    "native_finish_reason": "stop",
                }

                if data.get("function") and len(data["function"]) > 0:
                    tool_calls_list = []
                    for func_idx, function_call_data in enumerate(data["function"]):
                        tool_calls_list.append({
                            "id": f"call_{generate_random_string(24)}",
                            "index": func_idx,
                            "type": "function",
                            "function": {
                                "name": function_call_data["name"],
                                "arguments": json.dumps(function_call_data["params"]),
                            },
                        })
                    delta_content["tool_calls"] = tool_calls_list
                    choice_item["finish_reason"] = "tool_calls"
                    choice_item["native_finish_reason"] = "tool_calls"
                    delta_content["content"] = None

                output = {
                    "id": chat_completion_id,
                    "object": "chat.completion.chunk",
                    "model": model_name_for_stream,
                    "created": created_timestamp,
                    "choices": [choice_item]
                }
                yield f"data: {json.dumps(output, ensure_ascii=False, separators=(',', ':'))}\n\n"

        # 发送结束标记
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error(f"[{req_id}] 从流式代理服务生成流式响应时出错: {e}", exc_info=True)
        yield generate_sse_error_chunk(f"流式代理服务错误: {str(e)}", req_id)
        yield "data: [DONE]\n\n"


def clear_stream_queue():
    """清理流队列"""
    global STREAM_QUEUE, STREAM_PROCESS
    
    if STREAM_QUEUE:
        try:
            # 清空队列
            while not STREAM_QUEUE.empty():
                try:
                    STREAM_QUEUE.get_nowait()
                except:
                    break
            logger.info("流队列已清空")
        except Exception as e:
            logger.warning(f"清空流队列时出错: {e}")
    
    if STREAM_PROCESS and STREAM_PROCESS.is_alive():
        try:
            STREAM_PROCESS.terminate()
            STREAM_PROCESS.join(timeout=5)
            if STREAM_PROCESS.is_alive():
                STREAM_PROCESS.kill()
            logger.info("流进程已终止")
        except Exception as e:
            logger.warning(f"终止流进程时出错: {e}")
        finally:
            STREAM_PROCESS = None
    
    STREAM_QUEUE = None


def get_cached_params() -> Dict[str, Any]:
    """
    获取缓存的页面参数（与重构前完全一致的实现）

    Returns:
        缓存的参数字典
    """
    return page_params_cache.copy()


def update_cached_params(params: Dict[str, Any]):
    """
    更新缓存的页面参数（与重构前完全一致的实现）

    Args:
        params: 要更新的参数
    """
    page_params_cache.update(params)


def clear_cached_params(param_keys: list):
    """
    清除指定的缓存参数（与重构前完全一致的实现）

    Args:
        param_keys: 要清除的参数键列表
    """
    for key in param_keys:
        if key in page_params_cache:
            del page_params_cache[key]


def log_stream_timeout_with_suppression(error_message: str):
    """
    带抑制功能的流超时日志记录
    
    Args:
        error_message: 错误消息
    """
    current_time = time.monotonic()
    state = STREAM_TIMEOUT_LOG_STATE
    
    # 检查是否在抑制期间
    if current_time < state["suppress_until_time"]:
        return
    
    # 检查是否需要开始抑制
    if state["consecutive_timeouts"] < state["max_initial_errors"]:
        logger.warning(error_message)
        state["consecutive_timeouts"] += 1
        state["last_error_log_time"] = current_time
    elif state["consecutive_timeouts"] == state["max_initial_errors"]:
        # 开始抑制
        logger.warning(f"{error_message} (后续类似错误将被抑制 {state['suppress_duration_after_initial_burst']} 秒)")
        state["suppress_until_time"] = current_time + state["suppress_duration_after_initial_burst"]
        state["consecutive_timeouts"] += 1
    else:
        # 在抑制期后的警告
        if current_time - state["last_error_log_time"] >= state["warning_interval_after_suppress"]:
            logger.warning(f"流超时错误仍在继续发生 (最近一次: {error_message})")
            state["last_error_log_time"] = current_time


def start_queue_worker():
    """启动队列工作器"""
    global request_queue, processing_lock, worker_task, params_cache_lock
    
    # 初始化队列和锁
    request_queue = asyncio.Queue()
    processing_lock = asyncio.Lock()
    params_cache_lock = asyncio.Lock()
    
    # 启动工作器任务
    worker_task = asyncio.create_task(queue_worker())
    logger.info("队列管理器已启动")


async def cleanup_queue_worker():
    """清理队列工作器"""
    global request_queue, worker_task, processing_lock, params_cache_lock
    
    if worker_task and not worker_task.done():
        # 发送停止信号
        if request_queue:
            await request_queue.put(None)
        
        try:
            await asyncio.wait_for(worker_task, timeout=5.0)
            logger.info("队列工作器已正常停止")
        except asyncio.TimeoutError:
            logger.warning("队列工作器停止超时，强制取消")
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
    
    # 清理资源
    request_queue = None
    worker_task = None
    processing_lock = None
    params_cache_lock = None
    
    # 清理流队列
    clear_stream_queue()
    
    logger.info("队列管理器已清理")
