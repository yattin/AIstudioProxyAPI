# request_processor.py - 请求处理模块
# 负责核心请求处理逻辑、参数设置和响应内容获取

import asyncio
import json
import logging
import os
import random
import time
from typing import Dict, Any, Optional, List, Union, AsyncGenerator

from playwright.async_api import Page as AsyncPage, Error as PlaywrightAsyncError, TimeoutError, expect as expect_async

from config import (
    INPUT_SELECTOR, SUBMIT_BUTTON_SELECTOR, RESPONSE_CONTAINER_SELECTOR,
    RESPONSE_TEXT_SELECTOR, LOADING_SPINNER_SELECTOR, ERROR_TOAST_SELECTOR,
    RESPONSE_COMPLETION_TIMEOUT, INITIAL_WAIT_MS_BEFORE_POLLING, POLLING_INTERVAL,
    SILENCE_TIMEOUT_MS, POST_SPINNER_CHECK_DELAY_MS, FINAL_STATE_CHECK_TIMEOUT_MS,
    POST_COMPLETION_BUFFER, TEMPERATURE_INPUT_SELECTOR, MAX_OUTPUT_TOKENS_SELECTOR,
    TOP_P_INPUT_SELECTOR, STOP_SEQUENCE_INPUT_SELECTOR, MAT_CHIP_REMOVE_BUTTON_SELECTOR,
    CHAT_COMPLETION_ID_PREFIX, MODEL_NAME, DEFAULT_FALLBACK_MODEL_ID
)
from utils import (
    ChatCompletionRequest, Message, validate_chat_request, prepare_combined_prompt,
    generate_random_string
)
from model_manager import (
    current_ai_studio_model_id, model_switching_lock, switch_ai_studio_model,
    get_model_by_id, is_model_available
)
from queue_manager import get_cached_params, update_cached_params, clear_cached_params

logger = logging.getLogger("AIStudioProxyServer")


async def process_chat_request(request: ChatCompletionRequest, req_id: str) -> Dict[str, Any]:
    """
    处理聊天完成请求
    
    Args:
        request: 聊天完成请求
        req_id: 请求ID
        
    Returns:
        响应字典
    """
    from browser_manager import page_instance, is_page_ready
    
    if not is_page_ready or not page_instance or page_instance.is_closed():
        error_msg = "浏览器页面未就绪或已关闭"
        logger.error(f"[{req_id}] {error_msg}")
        return _create_error_response(error_msg, req_id)
    
    try:
        # 验证请求
        validate_chat_request(request.messages, req_id)
        
        # 处理模型切换
        target_model_id = await _handle_model_switching(request, req_id)
        if not target_model_id:
            return _create_error_response("模型切换失败", req_id)
        
        # 设置参数
        await _set_request_parameters(page_instance, request, req_id)
        
        # 准备提示
        combined_prompt = prepare_combined_prompt(request.messages, req_id)
        if not combined_prompt.strip():
            return _create_error_response("提示内容为空", req_id)

        # 检查是否使用stream服务
        stream_port = os.environ.get('STREAM_PORT', '3120')
        use_stream = stream_port != '0'

        # 无论是否使用流式服务，都需要执行 Playwright 操作来触发 AI Studio 响应生成
        logger.info(f"[{req_id}] 执行 Playwright 页面交互 (流式服务: {'启用' if use_stream else '禁用'})")

        if use_stream:
            # 使用流式服务时，仍需要通过 Playwright 触发响应生成
            # 但响应内容将通过流式队列获取
            response_content = await _send_request_and_get_response_for_stream(
                page_instance, combined_prompt, req_id
            )
        else:
            # 传统模式：通过 Playwright 发送请求并直接获取响应
            response_content = await _send_request_and_get_response(
                page_instance, combined_prompt, req_id
            )

        if not response_content:
            return _create_error_response("AI Studio 响应为空", req_id)

        # 构建成功响应
        return _create_success_response(response_content, target_model_id, req_id)
        
    except Exception as e:
        logger.error(f"[{req_id}] 处理请求时发生错误: {e}", exc_info=True)
        from browser_manager import save_error_snapshot
        await save_error_snapshot(f"request_processing_error_{req_id}")
        return _create_error_response(f"处理请求时发生错误: {str(e)}", req_id)


async def _handle_model_switching(request: ChatCompletionRequest, req_id: str) -> Optional[str]:
    """
    处理模型切换逻辑
    
    Args:
        request: 聊天完成请求
        req_id: 请求ID
        
    Returns:
        目标模型ID，如果失败则返回 None
    """
    global current_ai_studio_model_id
    from browser_manager import page_instance
    
    target_model_id = request.model
    
    # 如果没有指定模型，使用当前模型
    if not target_model_id:
        if current_ai_studio_model_id:
            target_model_id = current_ai_studio_model_id
            logger.info(f"[{req_id}] 未指定模型，使用当前模型: {target_model_id}")
        else:
            target_model_id = DEFAULT_FALLBACK_MODEL_ID
            logger.warning(f"[{req_id}] 未指定模型且当前模型未知，使用默认模型: {target_model_id}")
    
    # 检查模型是否可用
    if not is_model_available(target_model_id):
        logger.error(f"[{req_id}] 请求的模型 '{target_model_id}' 不可用")
        return None
    
    # 检查是否需要切换模型
    if current_ai_studio_model_id != target_model_id:
        logger.info(f"[{req_id}] 需要从 '{current_ai_studio_model_id}' 切换到 '{target_model_id}'")
        
        async with model_switching_lock:
            # 再次检查（避免竞态条件）
            if current_ai_studio_model_id != target_model_id:
                success = await switch_ai_studio_model(page_instance, target_model_id, req_id)
                if success:
                    current_ai_studio_model_id = target_model_id
                    logger.info(f"[{req_id}] 模型切换成功: {target_model_id}")
                else:
                    logger.error(f"[{req_id}] 模型切换失败")
                    return None
            else:
                logger.info(f"[{req_id}] 模型已在其他请求中切换到目标模型")
    else:
        logger.info(f"[{req_id}] 当前模型已是目标模型: {target_model_id}")
    
    return target_model_id


async def _set_request_parameters(page: AsyncPage, request: ChatCompletionRequest, req_id: str):
    """
    设置请求参数
    
    Args:
        page: Playwright 页面实例
        request: 聊天完成请求
        req_id: 请求ID
    """
    logger.info(f"[{req_id}] 开始设置请求参数")
    
    # 获取缓存的参数
    cached_params = get_cached_params()
    params_to_update = {}
    
    try:
        # 设置温度参数
        if request.temperature is not None:
            await _set_temperature(page, request.temperature, cached_params, req_id)
            params_to_update['temperature'] = request.temperature
        
        # 设置最大输出令牌数
        if request.max_output_tokens is not None:
            await _set_max_output_tokens(page, request.max_output_tokens, cached_params, req_id)
            params_to_update['max_output_tokens'] = request.max_output_tokens
        
        # 设置 top_p 参数
        if request.top_p is not None:
            await _set_top_p(page, request.top_p, cached_params, req_id)
            params_to_update['top_p'] = request.top_p
        
        # 设置停止序列
        if request.stop is not None:
            await _set_stop_sequences(page, request.stop, cached_params, req_id)
            params_to_update['stop'] = request.stop
        
        # 更新缓存
        if params_to_update:
            update_cached_params(params_to_update)
        
        logger.info(f"[{req_id}] 请求参数设置完成")
        
    except Exception as e:
        logger.error(f"[{req_id}] 设置请求参数时出错: {e}", exc_info=True)
        raise


async def _set_temperature(page: AsyncPage, temperature: float, cached_params: Dict[str, Any], req_id: str):
    """设置温度参数"""
    cached_temp = cached_params.get('temperature')
    if cached_temp == temperature:
        logger.debug(f"[{req_id}] 温度参数已缓存，跳过设置: {temperature}")
        return
    
    try:
        temp_input = page.locator(TEMPERATURE_INPUT_SELECTOR)
        await expect_async(temp_input).to_be_visible(timeout=5000)
        
        # 使用与重构前完全相同的实现方式
        current_temp_str = await temp_input.input_value(timeout=3000)
        current_temp_float = float(current_temp_str)
        logger.info(f"[{req_id}] 页面当前温度: {current_temp_float}, 请求调整后温度: {temperature}")

        if abs(current_temp_float - temperature) < 0.001:
            logger.info(f"[{req_id}] 页面当前温度 ({current_temp_float}) 与请求温度 ({temperature}) 一致。更新缓存并跳过写入。")
            update_cached_params({"temperature": current_temp_float})
        else:
            logger.info(f"[{req_id}] 页面温度 ({current_temp_float}) 与请求温度 ({temperature}) 不同，正在更新...")
            await temp_input.fill(str(temperature), timeout=5000)
            await asyncio.sleep(0.1)
            new_temp_str = await temp_input.input_value(timeout=3000)
            new_temp_float = float(new_temp_str)
            if abs(new_temp_float - temperature) < 0.001:
                logger.info(f"[{req_id}] ✅ 温度已成功更新为: {new_temp_float}。更新缓存。")
                update_cached_params({"temperature": new_temp_float})
            else:
                logger.warning(f"[{req_id}] ⚠️ 温度更新后验证失败。页面显示: {new_temp_float}, 期望: {temperature}。清除缓存中的温度。")
                clear_cached_params(["temperature"])
        
        logger.info(f"[{req_id}] 温度参数已设置为: {temperature}")
        
    except Exception as e:
        logger.warning(f"[{req_id}] 设置温度参数失败: {e}")


async def _set_max_output_tokens(page: AsyncPage, max_tokens: int, cached_params: Dict[str, Any], req_id: str):
    """设置最大输出令牌数"""
    cached_tokens = cached_params.get('max_output_tokens')
    if cached_tokens == max_tokens:
        logger.debug(f"[{req_id}] 最大输出令牌数已缓存，跳过设置: {max_tokens}")
        return
    
    try:
        tokens_input = page.locator(MAX_OUTPUT_TOKENS_SELECTOR)
        await expect_async(tokens_input).to_be_visible(timeout=5000)
        
        # 使用与重构前完全相同的实现方式
        current_max_tokens_str = await tokens_input.input_value(timeout=3000)
        current_max_tokens_int = int(current_max_tokens_str)
        logger.info(f"[{req_id}] 页面当前最大输出 Tokens: {current_max_tokens_int}, 请求调整后最大输出 Tokens: {max_tokens}")

        if current_max_tokens_int == max_tokens:
            logger.info(f"[{req_id}] 页面当前最大输出 Tokens ({current_max_tokens_int}) 与请求值 ({max_tokens}) 一致。更新缓存并跳过写入。")
            update_cached_params({"max_output_tokens": current_max_tokens_int})
        else:
            logger.info(f"[{req_id}] 页面最大输出 Tokens ({current_max_tokens_int}) 与请求值 ({max_tokens}) 不同，正在更新...")
            await tokens_input.fill(str(max_tokens), timeout=5000)
            await asyncio.sleep(0.1)
            new_max_tokens_str = await tokens_input.input_value(timeout=3000)
            new_max_tokens_int = int(new_max_tokens_str)
            if new_max_tokens_int == max_tokens:
                logger.info(f"[{req_id}] ✅ 最大输出 Tokens 已成功更新为: {new_max_tokens_int}。更新缓存。")
                update_cached_params({"max_output_tokens": new_max_tokens_int})
            else:
                logger.warning(f"[{req_id}] ⚠️ 最大输出 Tokens 更新后验证失败。页面显示: {new_max_tokens_int}, 期望: {max_tokens}。清除缓存中的此参数。")
                clear_cached_params(["max_output_tokens"])
        
        logger.info(f"[{req_id}] 最大输出令牌数已设置为: {max_tokens}")
        
    except Exception as e:
        logger.warning(f"[{req_id}] 设置最大输出令牌数失败: {e}")


async def _set_top_p(page: AsyncPage, top_p: float, cached_params: Dict[str, Any], req_id: str):
    """设置 top_p 参数"""
    cached_top_p = cached_params.get('top_p')
    if cached_top_p == top_p:
        logger.debug(f"[{req_id}] Top-p 参数已缓存，跳过设置: {top_p}")
        return
    
    try:
        top_p_input = page.locator(TOP_P_INPUT_SELECTOR)
        await expect_async(top_p_input).to_be_visible(timeout=5000)
        
        # 使用与重构前完全相同的实现方式
        current_top_p_str = await top_p_input.input_value(timeout=3000)
        current_top_p_float = float(current_top_p_str)
        logger.info(f"[{req_id}] 页面当前 Top P: {current_top_p_float}, 请求调整后 Top P: {top_p}")

        if abs(current_top_p_float - top_p) > 1e-9:
            logger.info(f"[{req_id}] 页面 Top P ({current_top_p_float}) 与请求 Top P ({top_p}) 不同，正在更新...")
            await top_p_input.fill(str(top_p), timeout=5000)
            await asyncio.sleep(0.1)
            new_top_p_str = await top_p_input.input_value(timeout=3000)
            new_top_p_float = float(new_top_p_str)
            if abs(new_top_p_float - top_p) < 1e-9:
                logger.info(f"[{req_id}] ✅ Top P 已成功更新为: {new_top_p_float}")
            else:
                logger.warning(f"[{req_id}] ⚠️ Top P 更新后验证失败。页面显示: {new_top_p_float}, 期望: {top_p}")
        else:
            logger.info(f"[{req_id}] 页面 Top P ({current_top_p_float}) 与请求 Top P ({top_p}) 一致或在容差范围内，无需更改。")
        
        logger.info(f"[{req_id}] Top-p 参数已设置为: {top_p}")
        
    except Exception as e:
        logger.warning(f"[{req_id}] 设置 Top-p 参数失败: {e}")


async def _set_stop_sequences(page: AsyncPage, stop: Union[str, List[str]], cached_params: Dict[str, Any], req_id: str):
    """设置停止序列"""
    stop_list = [stop] if isinstance(stop, str) else stop
    cached_stop = cached_params.get('stop')
    
    if cached_stop == stop_list:
        logger.debug(f"[{req_id}] 停止序列已缓存，跳过设置: {stop_list}")
        return
    
    try:
        # 使用与重构前完全相同的实现方式
        stop_input_locator = page.locator(STOP_SEQUENCE_INPUT_SELECTOR)
        remove_chip_buttons_locator = page.locator(MAT_CHIP_REMOVE_BUTTON_SELECTOR)

        logger.info(f"[{req_id}] 尝试清空已有的停止序列...")
        initial_chip_count = await remove_chip_buttons_locator.count()
        removed_count = 0
        max_removals = initial_chip_count + 5

        while await remove_chip_buttons_locator.count() > 0 and removed_count < max_removals:
            try:
                await remove_chip_buttons_locator.first.click(timeout=2000)
                removed_count += 1
                await asyncio.sleep(0.15)
            except Exception:
                break

        logger.info(f"[{req_id}] 已有停止序列清空尝试完成。移除 {removed_count} 个。")

        if stop_list:
            logger.info(f"[{req_id}] 添加新的停止序列: {stop_list}")
            await expect_async(stop_input_locator).to_be_visible(timeout=5000)
            for seq in stop_list:
                await stop_input_locator.fill(seq, timeout=3000)
                await stop_input_locator.press("Enter", timeout=3000)
                await asyncio.sleep(0.2)
                current_input_val = await stop_input_locator.input_value(timeout=1000)
                if current_input_val:
                    logger.warning(f"[{req_id}] 添加停止序列 '{seq}' 后输入框未清空 (值为: '{current_input_val}')。")
            logger.info(f"[{req_id}] ✅ 新停止序列添加操作完成。")
        else:
            logger.info(f"[{req_id}] 没有提供新的有效停止序列来添加 (请求清空)。")

        logger.info(f"[{req_id}] 停止序列已设置为: {stop_list}")

    except Exception as e:
        logger.warning(f"[{req_id}] 设置停止序列失败: {e}")


def _create_error_response(message: str, req_id: str) -> Dict[str, Any]:
    """创建错误响应"""
    return {
        "error": {
            "message": f"[{req_id}] {message}",
            "type": "server_error",
            "code": "internal_error"
        }
    }


def _create_success_response(content: str, model_id: str, req_id: str) -> Dict[str, Any]:
    """创建成功响应"""
    return {
        "id": f"{CHAT_COMPLETION_ID_PREFIX}{req_id}-{int(time.time())}-{generate_random_string(3)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 0,  # AI Studio 不提供令牌计数
            "completion_tokens": 0,
            "total_tokens": 0
        }
    }


async def _send_request_and_get_response(page: AsyncPage, prompt: str, req_id: str) -> Optional[str]:
    """
    发送请求并获取响应

    Args:
        page: Playwright 页面实例
        prompt: 提示内容
        req_id: 请求ID

    Returns:
        响应内容，如果失败则返回 None
    """
    logger.info(f"[{req_id}] 开始发送请求并获取响应")

    try:
        # 输入提示
        await _input_prompt(page, prompt, req_id)

        # 提交请求
        await _submit_request(page, req_id)

        # 等待并获取响应
        response_content = await _wait_for_response(page, req_id)

        return response_content

    except Exception as e:
        logger.error(f"[{req_id}] 发送请求并获取响应时出错: {e}", exc_info=True)
        return None


async def _send_request_and_get_response_for_stream(page: AsyncPage, prompt: str, req_id: str) -> Optional[str]:
    """
    为流式服务发送请求并触发响应生成

    此函数执行与 _send_request_and_get_response 相同的 Playwright 操作，
    但不等待完整响应，而是让流式代理服务从队列中获取数据

    Args:
        page: Playwright 页面实例
        prompt: 提示内容
        req_id: 请求ID

    Returns:
        简单的确认消息，表示请求已提交
    """
    logger.info(f"[{req_id}] 开始为流式服务发送请求")

    try:
        # 输入提示
        await _input_prompt(page, prompt, req_id)

        # 提交请求
        await _submit_request(page, req_id)

        # 对于流式服务，我们不等待完整响应，而是返回确认消息
        # 实际的响应数据将通过流式队列获取
        logger.info(f"[{req_id}] 请求已提交，流式代理服务将处理响应")
        return "Request submitted for stream processing"

    except Exception as e:
        logger.error(f"[{req_id}] 为流式服务发送请求时出错: {e}", exc_info=True)
        return None


async def _input_prompt(page: AsyncPage, prompt: str, req_id: str):
    """输入提示内容"""
    logger.info(f"[{req_id}] 输入提示内容 (长度: {len(prompt)})")

    try:
        # 使用与重构前完全相同的实现方式
        from config import PROMPT_TEXTAREA_SELECTOR
        prompt_textarea_locator = page.locator(PROMPT_TEXTAREA_SELECTOR)
        autosize_wrapper_locator = page.locator('ms-prompt-input-wrapper ms-autosize-textarea')

        await expect_async(prompt_textarea_locator).to_be_visible(timeout=5000)
        logger.info(f"[{req_id}]   - 使用 JavaScript evaluate 填充提示文本...")

        # 使用 JavaScript evaluate 方法设置值并触发事件（与重构前完全一致）
        await prompt_textarea_locator.evaluate(
            '''
            (element, text) => {
                element.value = text;
                element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            }
            ''',
            prompt
        )
        await autosize_wrapper_locator.evaluate('(element, text) => { element.setAttribute("data-value", text); }', prompt)
        logger.info(f"[{req_id}]   - JavaScript evaluate 填充完成，data-value 已尝试更新。")

        logger.info(f"[{req_id}] 提示内容输入完成")

    except Exception as e:
        logger.error(f"[{req_id}] 输入提示内容失败: {e}")
        raise


async def _submit_request(page: AsyncPage, req_id: str):
    """提交请求"""
    logger.info(f"[{req_id}] 提交请求")

    try:
        # 使用与重构前完全相同的实现方式
        from config import PROMPT_TEXTAREA_SELECTOR
        prompt_textarea_locator = page.locator(PROMPT_TEXTAREA_SELECTOR)
        submit_button_locator = page.locator(SUBMIT_BUTTON_SELECTOR)

        logger.info(f"[{req_id}]   - 等待发送按钮启用 (填充提示后)...")
        wait_timeout_ms_submit_enabled = 40000  # 40 seconds

        await expect_async(submit_button_locator).to_be_enabled(timeout=wait_timeout_ms_submit_enabled)
        logger.info(f"[{req_id}]   - ✅ 发送按钮已启用。")

        await asyncio.sleep(0.3)  # Small delay after button is enabled, before pressing shortcut
        submitted_successfully_via_shortcut = False

        # 检测操作系统类型以确定快捷键
        import os
        host_os_from_launcher = os.environ.get('HOST_OS_FOR_SHORTCUT')
        is_mac_determined = False

        if host_os_from_launcher:
            logger.info(f"[{req_id}]   - 从启动器环境变量 HOST_OS_FOR_SHORTCUT 获取到操作系统提示: '{host_os_from_launcher}'")
            if host_os_from_launcher == "Darwin":
                is_mac_determined = True
            elif host_os_from_launcher in ["Windows", "Linux"]:
                is_mac_determined = False
            else:
                logger.warning(f"[{req_id}]   - 未知的 HOST_OS_FOR_SHORTCUT 值: '{host_os_from_launcher}'。将回退到浏览器检测。")
                host_os_from_launcher = None

        if not host_os_from_launcher:
            logger.info(f"[{req_id}]   - HOST_OS_FOR_SHORTCUT 未设置或值未知，将进行浏览器内部操作系统检测。")
            user_agent_data_platform = None
            try:
                user_agent_data_platform = await page.evaluate("() => navigator.userAgentData?.platform || ''")
            except Exception as e_ua_data:
                logger.warning(f"[{req_id}]   - navigator.userAgentData.platform 读取失败 ({e_ua_data})，尝试 navigator.userAgent。")
                user_agent_string = await page.evaluate("() => navigator.userAgent || ''")
                user_agent_string_lower = user_agent_string.lower()
                if "macintosh" in user_agent_string_lower or "mac os x" in user_agent_string_lower or "macintel" in user_agent_string_lower:
                    user_agent_data_platform = "macOS"
                elif "windows" in user_agent_string_lower:
                    user_agent_data_platform = "Windows"
                elif "linux" in user_agent_string_lower:
                    user_agent_data_platform = "Linux"
                else:
                    user_agent_data_platform = "Other"

            if user_agent_data_platform and user_agent_data_platform != "Other":
                user_agent_data_platform_lower = user_agent_data_platform.lower()
                is_mac_determined = "mac" in user_agent_data_platform_lower or "macos" in user_agent_data_platform_lower or "macintel" in user_agent_data_platform_lower
                logger.info(f"[{req_id}]   - 浏览器内部检测到平台: '{user_agent_data_platform}', 推断 is_mac: {is_mac_determined}")
            else:
                logger.warning(f"[{req_id}]   - 浏览器平台信息获取失败、为空或为'Other' ('{user_agent_data_platform}')。默认使用非Mac快捷键。")
                is_mac_determined = False

        shortcut_modifier = "Meta" if is_mac_determined else "Control"
        shortcut_key = "Enter"
        logger.info(f"[{req_id}]   - 最终选择快捷键: {shortcut_modifier}+{shortcut_key} (基于 is_mac_determined: {is_mac_determined})")

        logger.info(f"[{req_id}]   - 尝试将焦点设置到输入框...")
        await prompt_textarea_locator.focus(timeout=5000)
        await asyncio.sleep(0.1)

        logger.info(f"[{req_id}]   - 焦点设置完成，准备按下快捷键...")
        try:
            await page.keyboard.press(f'{shortcut_modifier}+{shortcut_key}')
            logger.info(f"[{req_id}]   - 已使用组合键方式模拟按下: {shortcut_modifier}+{shortcut_key}")
        except Exception as combo_err:
            logger.warning(f"[{req_id}]   - 组合键方式失败: {combo_err}，尝试分步按键...")
            try:
                await page.keyboard.down(shortcut_modifier)
                await asyncio.sleep(0.05)
                await page.keyboard.down(shortcut_key)
                await asyncio.sleep(0.05)
                await page.keyboard.up(shortcut_key)
                await asyncio.sleep(0.05)
                await page.keyboard.up(shortcut_modifier)
                logger.info(f"[{req_id}]   - 已使用分步按键方式模拟: {shortcut_modifier}+{shortcut_key}")
            except Exception as step_err:
                logger.error(f"[{req_id}]   - 分步按键也失败: {step_err}")

        await asyncio.sleep(0.75)  # 提供UI反应时间

        # 验证提交成功
        user_prompt_actual_textarea_locator = page.locator(
            'ms-prompt-input-wrapper textarea[aria-label="Start typing a prompt"]'
        )
        validation_attempts = 7
        validation_interval = 0.2

        for i in range(validation_attempts):
            try:
                current_value = await user_prompt_actual_textarea_locator.input_value(timeout=500)
                if current_value == "":
                    submitted_successfully_via_shortcut = True
                    logger.info(f"[{req_id}]   - ✅ 快捷键提交成功确认 (用户输入 textarea value 已清空 after {i+1} attempts)。")
                    break
            except Exception as e_val:
                logger.debug(f"[{req_id}]   - 获取用户输入 textarea value 时出错 (尝试 {i+1}): {e_val}")

            if i < validation_attempts - 1:
                await asyncio.sleep(validation_interval)

        if not submitted_successfully_via_shortcut:
            final_value_for_log = "(无法获取或未清空)"
            try:
                final_value_for_log = await user_prompt_actual_textarea_locator.input_value(timeout=300)
            except:
                pass
            logger.warning(f"[{req_id}]   - ⚠️ 快捷键提交后用户输入 textarea value ('{final_value_for_log}') 未在预期时间内 ({validation_attempts * validation_interval:.1f}s) 清空。")
            raise RuntimeError("Failed to confirm prompt submission via shortcut.")

        logger.info(f"[{req_id}] 请求已提交")

    except Exception as e:
        logger.error(f"[{req_id}] 提交请求失败: {e}")
        raise


async def _wait_for_response(page: AsyncPage, req_id: str) -> Optional[str]:
    """
    等待并获取响应

    Args:
        page: Playwright 页面实例
        req_id: 请求ID

    Returns:
        响应内容
    """
    logger.info(f"[{req_id}] 开始等待响应")

    try:
        # 使用与重构前完全相同的实现方式
        from config import PROMPT_TEXTAREA_SELECTOR, EDIT_MESSAGE_BUTTON_SELECTOR

        prompt_textarea_locator = page.locator(PROMPT_TEXTAREA_SELECTOR)
        submit_button_locator = page.locator(SUBMIT_BUTTON_SELECTOR)
        edit_button_locator = page.locator(EDIT_MESSAGE_BUTTON_SELECTOR)

        # 等待响应完成
        completion_detected = await _wait_for_response_completion(
            page,
            prompt_textarea_locator,
            submit_button_locator,
            edit_button_locator,
            req_id
        )

        if not completion_detected:
            logger.error(f"[{req_id}] 响应完成检测失败")
            return None

        # 获取最终响应内容
        response_content = await _get_final_response_content(page, req_id)

        if response_content:
            logger.info(f"[{req_id}] 响应获取成功 (长度: {len(response_content)})")
            return response_content
        else:
            logger.warning(f"[{req_id}] 响应内容为空")
            return None

    except Exception as e:
        logger.error(f"[{req_id}] 等待响应时出错: {e}", exc_info=True)
        return None


async def _wait_for_response_completion(
    page: AsyncPage,
    prompt_textarea_locator,
    submit_button_locator,
    edit_button_locator,
    req_id: str,
    timeout_ms=RESPONSE_COMPLETION_TIMEOUT,
    initial_wait_ms=INITIAL_WAIT_MS_BEFORE_POLLING
) -> bool:
    """等待响应完成（与重构前完全一致的实现）"""
    logger.info(f"[{req_id}] (WaitV3) 开始等待响应完成... (超时: {timeout_ms}ms)")
    await asyncio.sleep(initial_wait_ms / 1000)  # Initial brief wait

    start_time = time.time()
    wait_timeout_ms_short = 3000  # 3 seconds for individual element checks

    consecutive_empty_input_submit_disabled_count = 0

    while True:
        current_time_elapsed_ms = (time.time() - start_time) * 1000
        if current_time_elapsed_ms > timeout_ms:
            logger.error(f"[{req_id}] (WaitV3) 等待响应完成超时 ({timeout_ms}ms)。")
            return False

        # --- 主要条件: 输入框空 & 提交按钮禁用 ---
        is_input_empty = await prompt_textarea_locator.input_value() == ""
        is_submit_disabled = False
        try:
            is_submit_disabled = await submit_button_locator.is_disabled(timeout=wait_timeout_ms_short)
        except Exception:
            logger.warning(f"[{req_id}] (WaitV3) 检查提交按钮是否禁用超时。为本次检查假定其未禁用。")

        if is_input_empty and is_submit_disabled:
            consecutive_empty_input_submit_disabled_count += 1
            logger.debug(f"[{req_id}] (WaitV3) 主要条件满足 (第 {consecutive_empty_input_submit_disabled_count} 次): 输入框空，提交按钮禁用。检查编辑按钮...")

            # 检查编辑按钮是否出现
            try:
                await expect_async(edit_button_locator).to_be_visible(timeout=wait_timeout_ms_short)
                logger.info(f"[{req_id}] (WaitV3) ✅ 编辑按钮已出现，响应完成确认。")
                return True
            except Exception:
                logger.debug(f"[{req_id}] (WaitV3) 编辑按钮尚未出现，继续等待...")

            # 启发式完成: 如果主要条件持续满足，但编辑按钮仍未出现
            if consecutive_empty_input_submit_disabled_count >= 3:  # 例如，大约 1.5秒 (3 * 0.5秒轮询)
                logger.warning(f"[{req_id}] (WaitV3) 响应可能已完成 (启发式): 输入框空，提交按钮禁用，但在 {consecutive_empty_input_submit_disabled_count} 次检查后编辑按钮仍未出现。假定完成。")
                return True  # 启发式完成
        else:  # 主要条件 (输入框空 & 提交按钮禁用) 未满足
            consecutive_empty_input_submit_disabled_count = 0  # 重置计数器
            from config import DEBUG_LOGS_ENABLED
            if DEBUG_LOGS_ENABLED:
                reasons = []
                if not is_input_empty: reasons.append("输入框非空")
                if not is_submit_disabled: reasons.append("提交按钮非禁用")
                logger.debug(f"[{req_id}] (WaitV3) 主要条件未满足 ({', '.join(reasons)}). 继续轮询...")

        await asyncio.sleep(0.5)  # 轮询间隔


async def _get_final_response_content(page: AsyncPage, req_id: str) -> Optional[str]:
    """获取最终响应内容（与重构前完全一致的实现）"""
    logger.info(f"[{req_id}] (Helper GetContent) 开始获取最终响应内容...")

    # 首先尝试通过编辑按钮获取
    response_content = await _get_response_via_edit_button(page, req_id)
    if response_content is not None:
        logger.info(f"[{req_id}] (Helper GetContent) ✅ 成功通过编辑按钮获取内容。")
        return response_content

    logger.warning(f"[{req_id}] (Helper GetContent) 编辑按钮方法失败或返回空，回退到复制按钮方法...")
    response_content = await _get_response_via_copy_button(page, req_id)
    if response_content is not None:
        logger.info(f"[{req_id}] (Helper GetContent) ✅ 成功通过复制按钮获取内容。")
        return response_content

    logger.error(f"[{req_id}] (Helper GetContent) ❌ 所有获取方法都失败了。")
    return None


async def _get_response_via_edit_button(page: AsyncPage, req_id: str) -> Optional[str]:
    """通过编辑按钮获取响应（与重构前完全一致的实现）"""
    logger.info(f"[{req_id}] (Helper) 尝试通过编辑按钮获取响应...")

    try:
        last_message_container = page.locator('ms-chat-turn').last
        edit_button = last_message_container.get_by_label("Edit")
        finish_edit_button = last_message_container.get_by_label("Stop editing")
        autosize_textarea_locator = last_message_container.locator('ms-autosize-textarea')
        actual_textarea_locator = autosize_textarea_locator.locator('textarea')

        logger.info(f"[{req_id}]   - 尝试悬停最后一条消息以显示 'Edit' 按钮...")
        await last_message_container.hover(timeout=2500)
        await asyncio.sleep(0.3)

        logger.info(f"[{req_id}]   - 点击 'Edit' 按钮...")
        await edit_button.click(timeout=5000)
        await asyncio.sleep(0.5)

        logger.info(f"[{req_id}]   - 等待 textarea 可见...")
        await expect_async(actual_textarea_locator).to_be_visible(timeout=5000)

        logger.info(f"[{req_id}]   - 获取 textarea 内容...")
        response_text = await actual_textarea_locator.input_value(timeout=5000)

        logger.info(f"[{req_id}]   - 点击 'Stop editing' 按钮...")
        await finish_edit_button.click(timeout=5000)

        if response_text and response_text.strip():
            logger.info(f"[{req_id}]   - ✅ 成功获取响应内容 (长度: {len(response_text)})。")
            return response_text.strip()
        else:
            logger.warning(f"[{req_id}]   - ⚠️ 响应内容为空或仅包含空白字符。")
            return None

    except Exception as e:
        logger.error(f"[{req_id}]   - ❌ 通过编辑按钮获取响应失败: {e}")
        return None


async def _get_response_via_copy_button(page: AsyncPage, req_id: str) -> Optional[str]:
    """通过复制按钮获取响应（与重构前完全一致的实现）"""
    logger.info(f"[{req_id}] (Helper) 尝试通过复制按钮获取响应...")

    try:
        last_message_container = page.locator('ms-chat-turn').last
        more_options_button = last_message_container.get_by_label("Open options")
        copy_markdown_button = page.get_by_role("menuitem", name="Copy markdown")

        logger.info(f"[{req_id}]   - 尝试悬停最后一条消息以显示选项...")
        await last_message_container.hover(timeout=5000)
        await asyncio.sleep(0.5)

        logger.info(f"[{req_id}]   - 定位并点击 '更多选项' 按钮...")
        await more_options_button.click(timeout=5000)
        await asyncio.sleep(0.3)

        logger.info(f"[{req_id}]   - 点击 'Copy markdown' 选项...")
        await copy_markdown_button.click(timeout=5000)
        await asyncio.sleep(0.5)

        logger.info(f"[{req_id}]   - 从剪贴板读取内容...")
        clipboard_content = await page.evaluate("() => navigator.clipboard.readText()")

        if clipboard_content and clipboard_content.strip():
            logger.info(f"[{req_id}]   - ✅ 成功从剪贴板获取响应内容 (长度: {len(clipboard_content)})。")
            return clipboard_content.strip()
        else:
            logger.warning(f"[{req_id}]   - ⚠️ 剪贴板内容为空或仅包含空白字符。")
            return None

    except Exception as e:
        logger.error(f"[{req_id}]   - ❌ 通过复制按钮获取响应失败: {e}")
        return None



async def use_stream_response(req_id: str) -> AsyncGenerator[Dict[str, Any], None]:
    """
    从流式代理服务获取响应数据的异步生成器
    （与重构前完全一致的实现）

    Args:
        req_id: 请求ID

    Yields:
        包含响应数据的字典
    """
    from config import STREAM_QUEUE, STREAM_TIMEOUT_LOG_STATE
    import queue

    total_empty = 0
    log_state = STREAM_TIMEOUT_LOG_STATE  # 访问全局状态

    while True:
        data_chunk = None
        try:
            if STREAM_QUEUE is None:  # 检查 STREAM_QUEUE 是否为 None
                logger.error(f"[{req_id}] STREAM_QUEUE is None in use_stream_response.")
                yield {"done": True, "reason": "stream_system_error", "body": "Auxiliary stream not available.", "function": []}
                return

            data_chunk = await asyncio.to_thread(STREAM_QUEUE.get_nowait)

            if data_chunk is not None:
                total_empty = 0  # 成功读取时重置计数器
                if log_state["consecutive_timeouts"] > 0:  # 使用字典访问
                    logger.info(f"[{req_id}] Auxiliary stream data received after {log_state['consecutive_timeouts']} consecutive empty reads/timeouts. Resetting.")
                    log_state["consecutive_timeouts"] = 0  # 重置计数器

                try:
                    data = json.loads(data_chunk)
                    yield data
                    if data.get("done") is True:
                        return
                except json.JSONDecodeError as json_e:
                    logger.error(f"[{req_id}] JSONDecodeError in use_stream_response: {json_e}. Data: '{data_chunk}'")
                    total_empty += 1

        except queue.Empty:  # 更具体的异常
            total_empty += 1
        except json.JSONDecodeError as json_e:  # 更具体的异常
            logger.error(f"[{req_id}] JSONDecodeError in use_stream_response: {json_e}. Data: '{data_chunk}'")
            total_empty += 1
        except Exception as e_q_get:  # 通用异常作为后备
            logger.error(f"[{req_id}] Unexpected error getting from STREAM_QUEUE: {e_q_get}", exc_info=True)
            total_empty += 1

        # 超时检查逻辑（与重构前一致）
        if total_empty >= 300:  # 300次空读取后超时
            from queue_manager import log_stream_timeout_with_suppression
            log_stream_timeout_with_suppression(f"[{req_id}] Auxiliary stream timeout after {total_empty} empty reads.")
            yield {"done": True, "reason": "internal_timeout", "body": "", "function": []}
            return

        await asyncio.sleep(0.1)  # 异步休眠

