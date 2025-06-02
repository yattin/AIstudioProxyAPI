# model_manager.py - 模型管理模块
# 负责模型列表处理、模型切换和排除模型管理

import asyncio
import json
import logging
import os
import time
from typing import List, Dict, Any, Optional, Set

from playwright.async_api import Page as AsyncPage, Error as PlaywrightAsyncError, expect as expect_async

from config import (
    AI_STUDIO_URL_PATTERN, INPUT_SELECTOR, MODELS_ENDPOINT_URL_CONTAINS,
    DEBUG_LOGS_ENABLED, EXCLUDED_MODELS_FILENAME
)


# --- 全局状态变量 ---
global_model_list_raw_json: Optional[List[Any]] = None
parsed_model_list: List[Dict[str, Any]] = []
model_list_fetch_event = asyncio.Event()
current_ai_studio_model_id: Optional[str] = None
model_switching_lock: Optional[asyncio.Lock] = None
excluded_model_ids: Set[str] = set()

logger = logging.getLogger("AIStudioProxyServer")


async def handle_model_list_response(response: Any):
    """
    处理模型列表响应
    
    Args:
        response: Playwright 响应对象
    """
    global global_model_list_raw_json, parsed_model_list, model_list_fetch_event, excluded_model_ids
    
    if MODELS_ENDPOINT_URL_CONTAINS in response.url and response.ok:
        logger.info(f"捕获到潜在的模型列表响应来自: {response.url} (状态: {response.status})")
        try:
            data = await response.json()
            models_array_container = None
            
            # 解析不同的数据结构
            if isinstance(data, list) and data:
                if isinstance(data[0], list) and data[0] and isinstance(data[0][0], list):
                    logger.info("检测到三层列表结构 data[0][0] is list. models_array_container 设置为 data[0]。")
                    models_array_container = data[0]
                elif isinstance(data[0], list) and data[0] and isinstance(data[0][0], str):
                    logger.info("检测到两层列表结构 data[0][0] is str. models_array_container 设置为 data。")
                    models_array_container = data
                elif isinstance(data[0], dict):
                    logger.info("检测到根列表，元素为字典。直接使用 data 作为 models_array_container。")
                    models_array_container = data
                else:
                    logger.warning(f"未知的列表嵌套结构。data[0] 类型: {type(data[0]) if data else 'N/A'}。data[0] 预览: {str(data[0])[:200] if data else 'N/A'}")
            elif isinstance(data, dict):
                if 'data' in data and isinstance(data['data'], list):
                    models_array_container = data['data']
                elif 'models' in data and isinstance(data['models'], list):
                    models_array_container = data['models']
                else:
                    for key, value in data.items():
                        if isinstance(value, list) and len(value) > 0 and isinstance(value[0], (dict, list)):
                            models_array_container = value
                            logger.info(f"模型列表数据在 '{key}' 键下通过启发式搜索找到。")
                            break
                    if models_array_container is None:
                        logger.warning("在字典响应中未能自动定位模型列表数组。")
                        if not model_list_fetch_event.is_set():
                            model_list_fetch_event.set()
                        return
            else:
                logger.warning(f"接收到的模型列表数据既不是列表也不是字典: {type(data)}")
                if not model_list_fetch_event.is_set():
                    model_list_fetch_event.set()
                return
            
            if models_array_container is not None:
                new_parsed_list = []
                for entry_in_container in models_array_container:
                    model_fields_list = None
                    if isinstance(entry_in_container, dict):
                        potential_id = entry_in_container.get('id', entry_in_container.get('model_id', entry_in_container.get('modelId')))
                        if potential_id:
                            model_fields_list = entry_in_container
                        else:
                            model_fields_list = list(entry_in_container.values())
                    elif isinstance(entry_in_container, list):
                        model_fields_list = entry_in_container
                    else:
                        logger.debug(f"Skipping entry of unknown type: {type(entry_in_container)}")
                        continue
                    
                    if not model_fields_list:
                        logger.debug("Skipping entry because model_fields_list is empty or None.")
                        continue
                    
                    # 解析模型字段
                    model_entry = _parse_model_fields(model_fields_list)
                    if model_entry:
                        new_parsed_list.append(model_entry)
                
                if new_parsed_list:
                    parsed_model_list = sorted(new_parsed_list, key=lambda m: m.get('display_name', '').lower())
                    global_model_list_raw_json = json.dumps({"data": parsed_model_list, "object": "list"})
                    
                    if DEBUG_LOGS_ENABLED:
                        log_output = f"成功解析和更新模型列表。总共解析模型数: {len(parsed_model_list)}.\n"
                        for i, item in enumerate(parsed_model_list[:min(3, len(parsed_model_list))]):
                            log_output += f"  Model {i+1}: ID={item.get('id')}, Name={item.get('display_name')}, Temp={item.get('default_temperature')}, MaxTokDef={item.get('default_max_output_tokens')}, MaxTokSup={item.get('supported_max_output_tokens')}, TopP={item.get('default_top_p')}\n"
                        logger.info(log_output)
                    
                    if not model_list_fetch_event.is_set():
                        model_list_fetch_event.set()
                elif not parsed_model_list:
                    logger.warning("解析后模型列表仍然为空。")
                    if not model_list_fetch_event.is_set():
                        model_list_fetch_event.set()
            else:
                logger.warning("models_array_container 为 None，无法解析模型列表。")
                if not model_list_fetch_event.is_set():
                    model_list_fetch_event.set()
                    
        except json.JSONDecodeError as json_err:
            logger.error(f"解析模型列表JSON失败: {json_err}. 响应 (前500字): {await response.text()[:500]}")
        except Exception as e_handle_list_resp:
            logger.exception(f"处理模型列表响应时发生未知错误: {e_handle_list_resp}")
        finally:
            if not model_list_fetch_event.is_set():
                logger.info("处理模型列表响应结束，强制设置 model_list_fetch_event。")
                model_list_fetch_event.set()


def _parse_model_fields(model_fields_list) -> Optional[Dict[str, Any]]:
    """
    解析模型字段
    
    Args:
        model_fields_list: 模型字段列表或字典
        
    Returns:
        解析后的模型字典，如果解析失败则返回 None
    """
    model_id_path_str = None
    display_name_candidate = ""
    description_candidate = "N/A"
    default_max_output_tokens_val = None
    default_top_p_val = None
    default_temperature_val = 1.0
    supported_max_output_tokens_val = None
    current_model_id_for_log = "UnknownModelYet"
    
    try:
        if isinstance(model_fields_list, list):
            if not (len(model_fields_list) > 0 and isinstance(model_fields_list[0], (str, int, float))):
                logger.debug(f"Skipping list-based model_fields due to invalid first element: {str(model_fields_list)[:100]}")
                return None
            
            model_id_path_str = str(model_fields_list[0])
            current_model_id_for_log = model_id_path_str.split('/')[-1] if model_id_path_str and '/' in model_id_path_str else model_id_path_str
            display_name_candidate = str(model_fields_list[3]) if len(model_fields_list) > 3 else ""
            description_candidate = str(model_fields_list[4]) if len(model_fields_list) > 4 else "N/A"
            
            # 解析 max_output_tokens
            if len(model_fields_list) > 6 and model_fields_list[6] is not None:
                try:
                    val_int = int(model_fields_list[6])
                    default_max_output_tokens_val = val_int
                    supported_max_output_tokens_val = val_int
                except (ValueError, TypeError):
                    logger.warning(f"模型 {current_model_id_for_log}: 无法将列表索引6的值 '{model_fields_list[6]}' 解析为 max_output_tokens。")
            
            # 解析 top_p
            if len(model_fields_list) > 9 and model_fields_list[9] is not None:
                try:
                    raw_top_p = float(model_fields_list[9])
                    if not (0.0 <= raw_top_p <= 1.0):
                        logger.warning(f"模型 {current_model_id_for_log}: 原始 top_p值 {raw_top_p} (来自列表索引9) 超出 [0,1] 范围，将裁剪。")
                        default_top_p_val = max(0.0, min(1.0, raw_top_p))
                    else:
                        default_top_p_val = raw_top_p
                except (ValueError, TypeError):
                    logger.warning(f"模型 {current_model_id_for_log}: 无法将列表索引9的值 '{model_fields_list[9]}' 解析为 top_p。")
                    
        elif isinstance(model_fields_list, dict):
            model_id_path_str = str(model_fields_list.get('id', model_fields_list.get('model_id', model_fields_list.get('modelId'))))
            current_model_id_for_log = model_id_path_str.split('/')[-1] if model_id_path_str and '/' in model_id_path_str else model_id_path_str
            display_name_candidate = str(model_fields_list.get('displayName', model_fields_list.get('display_name', model_fields_list.get('name', ''))))
            description_candidate = str(model_fields_list.get('description', "N/A"))
            
            # 解析 max_output_tokens
            mot_parsed = model_fields_list.get('maxOutputTokens', model_fields_list.get('defaultMaxOutputTokens', model_fields_list.get('outputTokenLimit')))
            if mot_parsed is not None:
                try:
                    val_int = int(mot_parsed)
                    default_max_output_tokens_val = val_int
                    supported_max_output_tokens_val = val_int
                except (ValueError, TypeError):
                    logger.warning(f"模型 {current_model_id_for_log}: 无法将字典值 '{mot_parsed}' 解析为 max_output_tokens。")
            
            # 解析 top_p
            top_p_parsed = model_fields_list.get('topP', model_fields_list.get('defaultTopP'))
            if top_p_parsed is not None:
                try:
                    raw_top_p = float(top_p_parsed)
                    if not (0.0 <= raw_top_p <= 1.0):
                        logger.warning(f"模型 {current_model_id_for_log}: 原始 top_p值 {raw_top_p} (来自字典) 超出 [0,1] 范围，将裁剪。")
                        default_top_p_val = max(0.0, min(1.0, raw_top_p))
                    else:
                        default_top_p_val = raw_top_p
                except (ValueError, TypeError):
                    logger.warning(f"模型 {current_model_id_for_log}: 无法将字典值 '{top_p_parsed}' 解析为 top_p。")
            
            # 解析 temperature
            temp_parsed = model_fields_list.get('temperature', model_fields_list.get('defaultTemperature'))
            if temp_parsed is not None:
                try:
                    default_temperature_val = float(temp_parsed)
                except (ValueError, TypeError):
                    logger.warning(f"模型 {current_model_id_for_log}: 无法将字典值 '{temp_parsed}' 解析为 temperature。")
        else:
            logger.debug(f"Skipping entry because model_fields_list is not list or dict: {type(model_fields_list)}")
            return None
            
    except Exception as e_parse_fields:
        logger.error(f"解析模型字段时出错 for entry {str(model_fields_list)[:100]}: {e_parse_fields}")
        return None
    
    if model_id_path_str and model_id_path_str.lower() != "none":
        simple_model_id_str = model_id_path_str.split('/')[-1] if '/' in model_id_path_str else model_id_path_str
        if simple_model_id_str in excluded_model_ids:
            logger.info(f"模型 '{simple_model_id_str}' 在排除列表 excluded_model_ids 中，已跳过。")
            return None
        
        final_display_name_str = display_name_candidate if display_name_candidate else simple_model_id_str.replace("-", " ").title()
        model_entry_dict = {
            "id": simple_model_id_str,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "ai_studio",
            "display_name": final_display_name_str,
            "description": description_candidate,
            "raw_model_path": model_id_path_str,
            "default_temperature": default_temperature_val,
            "default_max_output_tokens": default_max_output_tokens_val,
            "supported_max_output_tokens": supported_max_output_tokens_val,
            "default_top_p": default_top_p_val
        }
        return model_entry_dict
    else:
        logger.debug(f"Skipping entry due to invalid model_id_path: {model_id_path_str} from entry {str(model_fields_list)[:100]}")
        return None


def load_excluded_models(filename: str):
    """
    从文件加载排除的模型列表
    
    Args:
        filename: 排除模型文件名
    """
    global excluded_model_ids
    excluded_file_path = os.path.join(os.path.dirname(__file__), filename)
    
    try:
        if os.path.exists(excluded_file_path):
            with open(excluded_file_path, 'r', encoding='utf-8') as f:
                loaded_ids = {line.strip() for line in f if line.strip()}
            if loaded_ids:
                excluded_model_ids.update(loaded_ids)
                logger.info(f"✅ 从 '{filename}' 加载了 {len(loaded_ids)} 个模型到排除列表: {excluded_model_ids}")
            else:
                logger.info(f"'{filename}' 文件为空或不包含有效的模型 ID，排除列表未更改。")
        else:
            logger.info(f"模型排除列表文件 '{filename}' 未找到，排除列表为空。")
    except Exception as e:
        logger.error(f"❌ 从 '{filename}' 加载排除模型列表时出错: {e}", exc_info=True)


async def switch_ai_studio_model(page: AsyncPage, model_id: str, req_id: str) -> bool:
    """
    切换 AI Studio 模型

    Args:
        page: Playwright 页面实例
        model_id: 目标模型ID
        req_id: 请求ID

    Returns:
        切换是否成功
    """
    logger.info(f"[{req_id}] 开始切换模型到: {model_id}")
    original_prefs_str: Optional[str] = None
    original_prompt_model: Optional[str] = None
    new_chat_url = f"https://{AI_STUDIO_URL_PATTERN}prompts/new_chat"

    try:
        # 读取原始 localStorage
        original_prefs_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
        if original_prefs_str:
            try:
                original_prefs_obj = json.loads(original_prefs_str)
                original_prompt_model = original_prefs_obj.get("promptModel")
                logger.info(f"[{req_id}] 切换前 localStorage.promptModel 为: {original_prompt_model or '未设置'}")
            except json.JSONDecodeError:
                logger.warning(f"[{req_id}] 无法解析原始的 aiStudioUserPreference JSON 字符串。")
                original_prefs_str = None

        current_prefs_for_modification = json.loads(original_prefs_str) if original_prefs_str else {}
        full_model_path = f"models/{model_id}"

        # 检查是否已经是目标模型
        if current_prefs_for_modification.get("promptModel") == full_model_path:
            logger.info(f"[{req_id}] 模型已经设置为 {model_id} (localStorage 中已是目标值)，无需切换")
            if page.url != new_chat_url:
                logger.info(f"[{req_id}] 当前 URL 不是 new_chat ({page.url})，导航到 {new_chat_url}")
                await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=30000)
                await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=30000)
            return True

        # 更新 localStorage
        logger.info(f"[{req_id}] 从 {current_prefs_for_modification.get('promptModel', '未知')} 更新 localStorage.promptModel 为 {full_model_path}")
        current_prefs_for_modification["promptModel"] = full_model_path
        await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", json.dumps(current_prefs_for_modification))

        # 导航到新聊天页面
        logger.info(f"[{req_id}] localStorage 已更新，导航到 '{new_chat_url}' 应用新模型...")
        await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=30000)
        input_field = page.locator(INPUT_SELECTOR)
        await expect_async(input_field).to_be_visible(timeout=30000)
        logger.info(f"[{req_id}] 页面已导航到新聊天并加载完成，输入框可见")

        # 验证切换结果
        final_prefs_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
        final_prompt_model_in_storage: Optional[str] = None
        if final_prefs_str:
            try:
                final_prefs_obj = json.loads(final_prefs_str)
                final_prompt_model_in_storage = final_prefs_obj.get("promptModel")
            except json.JSONDecodeError:
                logger.warning(f"[{req_id}] 无法解析刷新后的 aiStudioUserPreference JSON 字符串。")

        if final_prompt_model_in_storage == full_model_path:
            logger.info(f"[{req_id}] ✅ AI Studio localStorage 中模型已成功设置为: {full_model_path}")

            # 验证页面显示
            page_display_match = await _verify_page_display(page, model_id, req_id)
            if page_display_match:
                return True
            else:
                logger.error(f"[{req_id}] ❌ 模型切换失败，因为页面显示的模型与期望不符 (即使localStorage可能已更改)。")
        else:
            logger.error(f"[{req_id}] ❌ AI Studio 未接受模型更改 (localStorage)。期望='{full_model_path}', 实际='{final_prompt_model_in_storage or '未设置或无效'}'.")

        # 切换失败，尝试恢复
        await _revert_model_change(page, original_prefs_str, original_prompt_model, new_chat_url, req_id)
        return False

    except Exception as e:
        logger.exception(f"[{req_id}] ❌ 切换模型过程中发生严重错误")
        from browser_manager import save_error_snapshot
        await save_error_snapshot(f"model_switch_error_{req_id}")

        # 异常恢复
        try:
            if original_prefs_str:
                logger.info(f"[{req_id}] 发生异常，尝试恢复 localStorage 至: {original_prompt_model or '未设置'}")
                await page.evaluate("(origPrefs) => localStorage.setItem('aiStudioUserPreference', origPrefs)", original_prefs_str)
                logger.info(f"[{req_id}] 异常恢复：导航到 '{new_chat_url}' 以应用恢复的 localStorage。")
                await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=15000)
                await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=15000)
        except Exception as recovery_err:
            logger.error(f"[{req_id}] 异常后恢复 localStorage 失败: {recovery_err}")
        return False


async def _verify_page_display(page: AsyncPage, model_id: str, req_id: str) -> bool:
    """验证页面显示的模型是否正确"""
    page_display_match = False
    expected_display_name_for_target_id = None
    actual_displayed_model_name_on_page = "无法读取"

    if parsed_model_list:
        for m_obj in parsed_model_list:
            if m_obj.get("id") == model_id:
                expected_display_name_for_target_id = m_obj.get("display_name")
                break

    if not expected_display_name_for_target_id:
        logger.warning(f"[{req_id}] 无法在parsed_model_list中找到目标ID '{model_id}' 的显示名称，跳过页面显示名称验证。这可能不准确。")
        page_display_match = True
    else:
        try:
            model_name_locator = page.locator('mat-select[data-test-ms-model-selector] div.model-option-content span.gmat-body-medium')
            actual_displayed_model_name_on_page_raw = await model_name_locator.first.inner_text(timeout=5000)
            actual_displayed_model_name_on_page = actual_displayed_model_name_on_page_raw.strip()
            normalized_actual_display = actual_displayed_model_name_on_page.lower()
            normalized_expected_display = expected_display_name_for_target_id.strip().lower()

            if normalized_actual_display == normalized_expected_display:
                page_display_match = True
                logger.info(f"[{req_id}] ✅ 页面显示模型 ('{actual_displayed_model_name_on_page}') 与期望 ('{expected_display_name_for_target_id}') 一致。")
            else:
                logger.error(f"[{req_id}] ❌ 页面显示模型 ('{actual_displayed_model_name_on_page}') 与期望 ('{expected_display_name_for_target_id}') 不一致。(Raw page: '{actual_displayed_model_name_on_page_raw}')")
        except Exception as e_disp:
            logger.warning(f"[{req_id}] 读取页面显示的当前模型名称时出错: {e_disp}。将无法验证页面显示。")

    return page_display_match


async def _revert_model_change(page: AsyncPage, original_prefs_str: Optional[str], original_prompt_model: Optional[str], new_chat_url: str, req_id: str):
    """恢复模型更改"""
    logger.info(f"[{req_id}] 模型切换失败。尝试恢复到页面当前实际显示的模型的状态...")

    # 读取当前页面显示的模型
    current_displayed_name_for_revert_raw = "无法读取"
    current_displayed_name_for_revert_stripped = "无法读取"
    try:
        model_name_locator_revert = page.locator('mat-select[data-test-ms-model-selector] div.model-option-content span.gmat-body-medium')
        current_displayed_name_for_revert_raw = await model_name_locator_revert.first.inner_text(timeout=5000)
        current_displayed_name_for_revert_stripped = current_displayed_name_for_revert_raw.strip()
        logger.info(f"[{req_id}] 恢复：页面当前显示的模型名称 (原始: '{current_displayed_name_for_revert_raw}', 清理后: '{current_displayed_name_for_revert_stripped}')")
    except Exception as e_read_disp_revert:
        logger.warning(f"[{req_id}] 恢复：读取页面当前显示模型名称失败: {e_read_disp_revert}。将尝试回退到原始localStorage。")
        if original_prefs_str:
            logger.info(f"[{req_id}] 恢复：由于无法读取当前页面显示，尝试将 localStorage 恢复到原始状态: '{original_prompt_model or '未设置'}'")
            await page.evaluate("(origPrefs) => localStorage.setItem('aiStudioUserPreference', origPrefs)", original_prefs_str)
            logger.info(f"[{req_id}] 恢复：导航到 '{new_chat_url}' 以应用恢复的原始 localStorage 设置...")
            await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=20000)
            await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=20000)
            logger.info(f"[{req_id}] 恢复：页面已导航到新聊天并加载，已尝试应用原始 localStorage。")
        else:
            logger.warning(f"[{req_id}] 恢复：无有效的原始 localStorage 状态可恢复，也无法读取当前页面显示。")
        return

    # 根据页面显示找到对应的模型ID
    model_id_to_revert_to = None
    if parsed_model_list and current_displayed_name_for_revert_stripped != "无法读取":
        normalized_current_display_for_revert = current_displayed_name_for_revert_stripped.lower()
        for m_obj in parsed_model_list:
            parsed_list_display_name = m_obj.get("display_name", "").strip().lower()
            if parsed_list_display_name == normalized_current_display_for_revert:
                model_id_to_revert_to = m_obj.get("id")
                logger.info(f"[{req_id}] 恢复：页面显示名称 '{current_displayed_name_for_revert_stripped}' 对应模型ID: {model_id_to_revert_to}")
                break
        if not model_id_to_revert_to:
            logger.warning(f"[{req_id}] 恢复：无法在 parsed_model_list 中找到与页面显示名称 '{current_displayed_name_for_revert_stripped}' 匹配的模型ID。")
    else:
        if current_displayed_name_for_revert_stripped == "无法读取":
            logger.warning(f"[{req_id}] 恢复：因无法读取页面显示名称，故不能从 parsed_model_list 转换ID。")
        else:
            logger.warning(f"[{req_id}] 恢复：parsed_model_list 为空，无法从显示名称 '{current_displayed_name_for_revert_stripped}' 转换模型ID。")

    # 应用恢复
    if model_id_to_revert_to:
        base_prefs_for_final_revert = {}
        try:
            current_ls_content_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
            if current_ls_content_str:
                base_prefs_for_final_revert = json.loads(current_ls_content_str)
            elif original_prefs_str:
                base_prefs_for_final_revert = json.loads(original_prefs_str)
        except json.JSONDecodeError:
            logger.warning(f"[{req_id}] 恢复：解析现有 localStorage 以构建恢复偏好失败。")

        path_to_revert_to = f"models/{model_id_to_revert_to}"
        base_prefs_for_final_revert["promptModel"] = path_to_revert_to
        logger.info(f"[{req_id}] 恢复：准备将 localStorage.promptModel 设置回页面实际显示的模型的路径: '{path_to_revert_to}'")
        await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", json.dumps(base_prefs_for_final_revert))
        logger.info(f"[{req_id}] 恢复：导航到 '{new_chat_url}' 以应用恢复到 '{model_id_to_revert_to}' 的 localStorage 设置...")
        await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=30000)
        await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=30000)
        logger.info(f"[{req_id}] 恢复：页面已导航到新聊天并加载。localStorage 应已设置为反映模型 '{model_id_to_revert_to}'。")
    else:
        logger.error(f"[{req_id}] 恢复：无法将模型恢复到页面显示的状态，因为未能从显示名称 '{current_displayed_name_for_revert_stripped}' 确定有效模型ID。")
        if original_prefs_str:
            logger.warning(f"[{req_id}] 恢复：作为最终后备，尝试恢复到原始 localStorage: '{original_prompt_model or '未设置'}'")
            await page.evaluate("(origPrefs) => localStorage.setItem('aiStudioUserPreference', origPrefs)", original_prefs_str)
            logger.info(f"[{req_id}] 恢复：导航到 '{new_chat_url}' 以应用最终后备的原始 localStorage。")
            await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=20000)
            await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=20000)
            logger.info(f"[{req_id}] 恢复：页面已导航到新聊天并加载，已应用最终后备的原始 localStorage。")
        else:
            logger.warning(f"[{req_id}] 恢复：无有效的原始 localStorage 状态可作为最终后备。")


async def handle_initial_model_state_and_storage():
    """处理初始模型状态和存储（与重构前完全一致的实现）"""
    global current_ai_studio_model_id

    from browser_manager import page_instance

    if not page_instance or page_instance.is_closed():
        logger.warning("页面实例不可用，无法获取当前模型状态。")
        return

    logger.info("--- (新) 处理初始模型状态, localStorage 和 isAdvancedOpen ---")
    needs_reload_and_storage_update = False
    reason_for_reload = ""

    try:
        initial_prefs_str = await page_instance.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
        if not initial_prefs_str:
            needs_reload_and_storage_update = True
            reason_for_reload = "localStorage.aiStudioUserPreference 未找到。"
            logger.info(f"   判定需要刷新和存储更新: {reason_for_reload}")
        else:
            logger.info("   localStorage 中找到 'aiStudioUserPreference'。正在解析...")
            try:
                pref_obj = json.loads(initial_prefs_str)
                is_advanced_open = pref_obj.get("isAdvancedOpen")
                prompt_model = pref_obj.get("promptModel")

                if is_advanced_open is not True:
                    needs_reload_and_storage_update = True
                    reason_for_reload = f"isAdvancedOpen 不是 true (当前: {is_advanced_open})。"
                    logger.info(f"   判定需要刷新和存储更新: {reason_for_reload}")
                elif not prompt_model or not prompt_model.startswith("models/"):
                    needs_reload_and_storage_update = True
                    reason_for_reload = f"promptModel 无效或缺失 (当前: {prompt_model})。"
                    logger.info(f"   判定需要刷新和存储更新: {reason_for_reload}")
                else:
                    # 从 promptModel 提取模型ID
                    model_id_from_storage = prompt_model.replace("models/", "")
                    current_ai_studio_model_id = model_id_from_storage
                    logger.info(f"   ✅ localStorage 有效且 isAdvancedOpen=true。初始模型 ID 从 localStorage 设置为: {current_ai_studio_model_id}")
            except json.JSONDecodeError:
                needs_reload_and_storage_update = True
                reason_for_reload = "解析 localStorage.aiStudioUserPreference JSON 失败。"
                logger.error(f"   判定需要刷新和存储更新: {reason_for_reload}")

        if needs_reload_and_storage_update:
            logger.info(f"   执行刷新和存储更新流程，原因: {reason_for_reload}")
            logger.info("   步骤 1: 调用 _set_model_from_page_display(set_storage=True) 更新 localStorage 和全局模型 ID...")
            await _set_model_from_page_display(page_instance, set_storage=True)

            current_page_url = page_instance.url
            logger.info(f"   步骤 2: 重新加载页面 ({current_page_url}) 以应用 isAdvancedOpen=true...")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"   尝试重新加载页面 (第 {attempt + 1}/{max_retries} 次): {current_page_url}")
                    await page_instance.goto(current_page_url, wait_until="domcontentloaded", timeout=40000)
                    await expect_async(page_instance.locator(INPUT_SELECTOR)).to_be_visible(timeout=30000)
                    logger.info(f"   ✅ 页面已成功重新加载到: {page_instance.url}")
                    break  # 成功则跳出循环
                except Exception as reload_err:
                    logger.warning(f"   ⚠️ 页面重新加载尝试 {attempt + 1}/{max_retries} 失败: {reload_err}")
                    if attempt < max_retries - 1:
                        logger.info(f"   将在5秒后重试...")
                        await asyncio.sleep(5)
                    else:
                        logger.error(f"   ❌ 页面重新加载在 {max_retries} 次尝试后最终失败: {reload_err}. 后续模型状态可能不准确。", exc_info=True)
                        from browser_manager import save_error_snapshot
                        await save_error_snapshot(f"initial_storage_reload_fail_attempt_{attempt+1}")

            logger.info("   步骤 3: 重新加载后，再次调用 _set_model_from_page_display(set_storage=False) 以同步全局模型 ID...")
            await _set_model_from_page_display(page_instance, set_storage=False)
            logger.info(f"   ✅ 刷新和存储更新流程完成。最终全局模型 ID: {current_ai_studio_model_id}")
        else:
            logger.info("   localStorage 状态良好 (isAdvancedOpen=true, promptModel有效)，无需刷新页面。")

    except Exception as e:
        logger.error(f"❌ (新) 处理初始模型状态和 localStorage 时发生严重错误: {e}", exc_info=True)
        try:
            logger.warning("   由于发生错误，尝试回退仅从页面显示设置全局模型 ID (不写入localStorage)...")
            await _set_model_from_page_display(page_instance, set_storage=False)
        except Exception as fallback_err:
            logger.error(f"   回退设置模型ID也失败: {fallback_err}")


async def _set_model_from_page_display(page: AsyncPage, set_storage: bool = False):
    """从页面显示设置模型（与重构前完全一致的实现）"""
    global current_ai_studio_model_id

    try:
        logger.info("   尝试从页面显示元素读取当前模型名称...")
        model_name_locator = page.locator('mat-select[data-test-ms-model-selector] div.model-option-content span.gmat-body-medium')
        displayed_model_name_from_page_raw = await model_name_locator.first.inner_text(timeout=7000)
        displayed_model_name = displayed_model_name_from_page_raw.strip()
        logger.info(f"   页面当前显示模型名称 (原始: '{displayed_model_name_from_page_raw}', 清理后: '{displayed_model_name}')")

        found_model_id_from_display = None
        if not model_list_fetch_event.is_set():
            logger.info("   等待模型列表数据 (最多5秒) 以便转换显示名称...")
            try:
                await asyncio.wait_for(model_list_fetch_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("   等待模型列表超时，可能无法准确转换显示名称为ID。")

        if parsed_model_list:
            for model_obj in parsed_model_list:
                if model_obj.get("display_name") and model_obj.get("display_name").strip() == displayed_model_name:
                    found_model_id_from_display = model_obj.get("id")
                    logger.info(f"   显示名称 '{displayed_model_name}' 对应模型 ID: {found_model_id_from_display}")
                    break
            if not found_model_id_from_display:
                logger.warning(f"   未在已知模型列表中找到与显示名称 '{displayed_model_name}' 匹配的 ID。")
        else:
            logger.warning("   模型列表尚不可用，无法将显示名称转换为ID。")

        new_model_value = found_model_id_from_display if found_model_id_from_display else displayed_model_name
        if current_ai_studio_model_id != new_model_value:
            current_ai_studio_model_id = new_model_value
            logger.info(f"   全局 current_ai_studio_model_id 已更新为: {current_ai_studio_model_id}")
        else:
            logger.info(f"   全局 current_ai_studio_model_id ('{current_ai_studio_model_id}') 与从页面获取的值一致，未更改。")

        if set_storage:
            logger.info(f"   准备为页面状态设置 localStorage (确保 isAdvancedOpen=true)...")
            existing_prefs_for_update_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
            prefs_to_set = {}
            if existing_prefs_for_update_str:
                try:
                    prefs_to_set = json.loads(existing_prefs_for_update_str)
                except json.JSONDecodeError:
                    logger.warning("   解析现有 localStorage.aiStudioUserPreference 失败，将创建新的偏好设置。")

            prefs_to_set["isAdvancedOpen"] = True
            logger.info(f"     强制 isAdvancedOpen: true")
            prefs_to_set["areToolsOpen"] = False
            logger.info(f"     强制 areToolsOpen: false")

            if found_model_id_from_display:
                new_prompt_model_path = f"models/{found_model_id_from_display}"
                prefs_to_set["promptModel"] = new_prompt_model_path
                logger.info(f"     设置 promptModel 为: {new_prompt_model_path} (基于找到的ID)")
            elif "promptModel" not in prefs_to_set:
                logger.warning(f"     无法从页面显示 '{displayed_model_name}' 找到模型ID，且 localStorage 中无现有 promptModel。promptModel 将不会被主动设置以避免潜在问题。")

            # 设置默认值
            default_keys_if_missing = {
                "bidiModel": "models/gemini-1.0-pro-001",
                "isSafetySettingsOpen": False,
                "hasShownSearchGroundingTos": False,
                "autosaveEnabled": True,
                "theme": "system",
                "bidiOutputFormat": 3,
                "isSystemInstructionsOpen": False,
                "warmWelcomeDisplayed": True,
                "getCodeLanguage": "Node.js",
                "getCodeHistoryToggle": False,
                "fileCopyrightAcknowledged": True
            }
            for key, val_default in default_keys_if_missing.items():
                if key not in prefs_to_set:
                    prefs_to_set[key] = val_default

            await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", json.dumps(prefs_to_set))
            logger.info(f"   ✅ localStorage.aiStudioUserPreference 已更新。isAdvancedOpen: {prefs_to_set.get('isAdvancedOpen')}, areToolsOpen: {prefs_to_set.get('areToolsOpen')}, promptModel: '{prefs_to_set.get('promptModel', '未设置/保留原样')}'。")

    except Exception as e_set_disp:
        logger.error(f"   尝试从页面显示设置模型时出错: {e_set_disp}", exc_info=True)


def get_model_by_id(model_id: str) -> Optional[Dict[str, Any]]:
    """
    根据模型ID获取模型信息

    Args:
        model_id: 模型ID

    Returns:
        模型信息字典，如果未找到则返回 None
    """
    for model in parsed_model_list:
        if model.get("id") == model_id:
            return model
    return None


def get_all_models() -> List[Dict[str, Any]]:
    """
    获取所有可用模型列表

    Returns:
        模型列表
    """
    return parsed_model_list.copy()


def is_model_available(model_id: str) -> bool:
    """
    检查模型是否可用

    Args:
        model_id: 模型ID

    Returns:
        模型是否可用
    """
    return any(model.get("id") == model_id for model in parsed_model_list)


async def wait_for_model_list(timeout: float = 30.0) -> bool:
    """
    等待模型列表加载完成

    Args:
        timeout: 超时时间（秒）

    Returns:
        是否成功加载模型列表
    """
    try:
        await asyncio.wait_for(model_list_fetch_event.wait(), timeout=timeout)
        return len(parsed_model_list) > 0
    except asyncio.TimeoutError:
        logger.warning(f"等待模型列表加载超时 ({timeout}秒)")
        return False


def initialize_model_manager():
    """初始化模型管理器"""
    global model_switching_lock

    # 初始化锁
    model_switching_lock = asyncio.Lock()

    # 加载排除的模型列表
    load_excluded_models(EXCLUDED_MODELS_FILENAME)

    logger.info("模型管理器已初始化")
