#!/usr/bin/env python3
import sys
import subprocess
import time
import re
import os
import signal
import atexit
import argparse
import threading
import traceback
import json
import asyncio

# 尝试导入 launch_server (用于实验性功能)
try:
    from camoufox.server import launch_server
except ImportError:
    # 不再退出，因为它是可选功能
    launch_server = None
    print("⚠️ 警告: 无法导入 'camoufox.server.launch_server'。实验性虚拟显示功能将不可用。")

# 尝试导入 Playwright (用于临时连接保存状态)
try:
    from playwright.async_api import async_playwright, Playwright, Browser, Page, BrowserContext
except ImportError:
    async_playwright = None
    print("⚠️ 警告: 无法导入 'playwright.async_api'。调试模式下的 '保存状态' 功能将不可用。")

# Configuration
SERVER_PY_FILENAME = "server.py"
PYTHON_EXECUTABLE = sys.executable
CAMOUFOX_START_TIMEOUT = 30 # seconds to wait for WS endpoint from output (subprocess mode)
EXPERIMENTAL_WAIT_TIMEOUT = 60 # seconds to wait for user to paste endpoint
STORAGE_STATE_PATH = os.path.join(os.path.dirname(__file__), "auth_state.json")
# --- 新增：认证文件目录 ---
AUTH_PROFILES_DIR = os.path.join(os.path.dirname(__file__), "auth_profiles")
ACTIVE_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, "active")
SAVED_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, "saved")

# --- 修改：全局变量需要同时支持两种模式 --- 
camoufox_proc = None # subprocess 模式
camoufox_server_thread = None # launch_server 模式
camoufox_server_instance = None # launch_server 返回值
stop_server_event = threading.Event() # launch_server 模式
server_py_proc = None

# --- 新增：确保目录存在 ---
def ensure_auth_dirs_exist():
    """确保认证文件目录存在"""
    print("--- 检查认证目录 ---")
    try:
        os.makedirs(ACTIVE_AUTH_DIR, exist_ok=True)
        print(f"   ✓ 激活认证目录: {ACTIVE_AUTH_DIR}")
        os.makedirs(SAVED_AUTH_DIR, exist_ok=True)
        print(f"   ✓ 保存认证目录: {SAVED_AUTH_DIR}")
    except OSError as e:
        print(f"   ❌ 创建认证目录时出错: {e}")
        sys.exit(1)
    print("--------------------")

def cleanup():
    """Ensures subprocesses and server thread are terminated on exit."""
    global camoufox_proc, server_py_proc, camoufox_server_thread, stop_server_event, camoufox_server_instance
    print(f"\n--- 开始清理 --- ")
    # 1. 终止主 FastAPI 服务器进程 (server.py)
    if server_py_proc and server_py_proc.poll() is None:
        print(f"   正在终止 server.py (PID: {server_py_proc.pid})...")
        try:
            # 尝试发送 SIGTERM
            server_py_proc.terminate()
            server_py_proc.wait(timeout=5)
            print(f"   ✓ server.py 已终止 (SIGTERM)。")
        except subprocess.TimeoutExpired:
            print(f"   ⚠️ server.py 未能优雅终止 (SIGTERM 超时)，强制终止 (SIGKILL)..." )
            server_py_proc.kill()
            try: server_py_proc.wait(timeout=1) # 短暂等待 SIGKILL
            except: pass
            print(f"   ✓ server.py 已强制终止 (SIGKILL)。")
        except Exception as e:
            print(f"   ❌ 终止 server.py 时出错: {e}")
        server_py_proc = None
    else:
        if server_py_proc:
             print(f"   server.py 进程已自行结束 (代码: {server_py_proc.poll()})。")
        # else: server_py_proc was never started or already cleaned up

    # 2. 清理 Camoufox 资源 (根据启动模式不同)
    # --- 清理 subprocess (调试模式) --- 
    if camoufox_proc and camoufox_proc.poll() is None:
        print(f"   正在终止 Camoufox 服务器进程 (调试模式 - subprocess, PID: {camoufox_proc.pid})...")
        try:
            # 使用进程组 ID 终止 (如果可用)
            if sys.platform != "win32":
                print(f"   尝试使用进程组 (PGID: {os.getpgid(camoufox_proc.pid)}) 终止 (SIGKILL)...")
                os.killpg(os.getpgid(camoufox_proc.pid), signal.SIGKILL)
            else:
                 print(f"   尝试强制终止 (SIGKILL)...")
                 camoufox_proc.kill()
            camoufox_proc.wait(timeout=3) # Wait briefly after kill
            print(f"   ✓ Camoufox 服务器进程 (调试模式) 已终止 (SIGKILL)。")
        except ProcessLookupError:
             print(f"   ℹ️ Camoufox 服务器进程 (调试模式) 可能已自行终止。")
        except subprocess.TimeoutExpired:
             print(f"   ⚠️ 等待 Camoufox (调试模式) SIGKILL 后超时。")
        except Exception as e:
            print(f"   ❌ 终止 Camoufox 服务器进程 (调试模式) 时出错: {e}")
        finally:
             camoufox_proc = None # Ensure it's None after handling
    elif camoufox_proc: # Process exists but already terminated
         print(f"   Camoufox 服务器进程 (调试模式) 已自行结束 (代码: {camoufox_proc.poll()})。")
         camoufox_proc = None

    # --- 清理后台线程和 launch_server 实例 (无头模式) --- 
    if camoufox_server_thread and camoufox_server_thread.is_alive():
        print(f"   正在请求 Camoufox 服务器线程 (无头模式 - launch_server) 停止...")
        stop_server_event.set() # 发送停止信号给线程内的 wait
        
        # 尝试关闭 launch_server 返回的实例 (如果它支持)
        if camoufox_server_instance and hasattr(camoufox_server_instance, 'close'):
            try:
                print("      尝试调用 camoufox_server_instance.close()...")
                # 注意：close() 可能是阻塞的，或者需要异步处理
                # 这里假设它是快速的，或者 launch_server 内部处理了关闭
                camoufox_server_instance.close() 
                print("      实例 close() 调用完成。")
            except Exception as e:
                print(f"      调用 close() 时出错: {e}")
                
        camoufox_server_thread.join(timeout=10) # 等待线程结束
        if camoufox_server_thread.is_alive():
            print(f"   ⚠️ Camoufox 服务器线程 (无头模式) 未能及时停止。")
            # 强制退出可能比较困难且不安全，依赖 atexit
        else:
             print(f"   ✓ Camoufox 服务器线程 (无头模式) 已停止。")
        camoufox_server_thread = None # Mark as cleaned up
        camoufox_server_instance = None
    elif camoufox_server_thread: # Thread object exists but isn't alive
         print(f"   Camoufox 服务器线程 (无头模式) 已自行结束。")
         camoufox_server_thread = None
         camoufox_server_instance = None

    # --- 移除旧的 subprocess 清理逻辑 (已合并到上面) ---
    # if camoufox_proc and camoufox_proc.poll() is None:
    #     ...
    # --- 移除旧的后台线程清理逻辑 (已合并到上面) ---
    # if camoufox_server_thread and camoufox_server_thread.is_alive():
    #     ...
             
    print(f"--- 清理完成 --- ")

# Register cleanup function to be called on script exit
atexit.register(cleanup)
# Also register for SIGINT (Ctrl+C) and SIGTERM
signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))


def check_dependencies():
    """Checks for essential dependencies for the launcher."""
    print(f"-------------------------------------------------")
    print(f"--- 步骤 1: 检查依赖项 ---")
    print('将检查以下模块是否已安装:')
    required = {"playwright": "playwright", "camoufox": "camoufox"}
    missing = []
    ok = True
    for mod_name, install_name in required.items():
        print(f"   - {mod_name} ... ", end="")
        try:
            __import__(mod_name)
            print(f"✓ 已找到")
        except ImportError:
            print(f"❌ 未找到")
            missing.append(install_name)
            ok = False

    server_script_path = os.path.join(os.path.dirname(__file__), SERVER_PY_FILENAME)
    print(f"   - 服务器脚本 ({SERVER_PY_FILENAME}) ... ", end="")
    if not os.path.exists(server_script_path):
         print(f"❌ 未找到")
         print(f"     错误: 未在预期路径找到 '{SERVER_PY_FILENAME}' 文件。")
         print(f"     预期路径: {server_script_path}")
         print(f"     请确保 '{SERVER_PY_FILENAME}' 与此脚本位于同一目录。")
         ok = False
    else:
         print(f"✓ 已找到")

    if not ok:
        print(f"\n-------------------------------------------------")
        print(f"❌ 错误: 依赖项检查未通过！")
        if missing:
            install_cmd = f"pip install {' '.join(missing)}"
            print(f"   缺少以下 Python 库: {', '.join(missing)}")
            print(f"   请运行以下命令安装:")
            print(f"      {install_cmd}")
            print(f"   (如果已安装但仍提示未找到，请尝试删除 site-packages 中相关目录后重新安装)")
        if not os.path.exists(server_script_path):
             print(f"   缺少必要的服务器脚本文件: {SERVER_PY_FILENAME}")
             print(f"   请确保它和 launch_camoufox.py 在同一个文件夹内。")
        print(f"-------------------------------------------------")
        sys.exit(1)
    else:
        print(f"\n✅ 所有依赖检查通过。")


# --- 函数：使用 subprocess 启动 (标准模式) ---
# !! 此函数存在错误，将被移除 !!
# def start_camoufox_server_debug_mode(): ... (整个函数将被删除)

# --- 函数：使用 launch_server 启动 (实验性虚拟显示模式) ---
# !! 此函数不再用于主流程，仅保留作为参考或未来可能的扩展 !!
# def run_launch_server_virtual_in_thread(): ...
# def start_camoufox_server_virtual(): ...

# --- 新增：函数用于无头模式后台线程 ---
def run_launch_server_headless_in_thread(json_path: str, stop_event: threading.Event):
    """在后台线程中运行 launch_server(headless=True, storage_state=json_path)。
    """
    global camoufox_server_instance
    if not launch_server:
        print("   后台线程: ❌ 错误: launch_server 未导入，无法启动。", file=sys.stderr, flush=True)
        return

    print(f"   后台线程: 使用认证文件 '{os.path.basename(json_path)}' 准备调用 launch_server(headless=True)...", flush=True)
    try:
        # 运行 launch_server
        # 注意：这里假设 launch_server 会阻塞直到服务器停止
        camoufox_server_instance = launch_server(headless=True, storage_state=json_path)
        print("   后台线程: launch_server 调用完成 (可能已阻塞)。等待停止信号...", flush=True)
        stop_event.wait() # 等待主线程的停止信号
        print("   后台线程: 收到停止信号，即将退出。", flush=True)

    except RuntimeError as e:
        if "Server process terminated unexpectedly" in str(e):
            print(f"   后台线程: ⚠️ 检测到服务器进程终止，这通常是关闭过程的一部分。", flush=True)
        else:
            print(f"\n   后台线程: ❌ 意外 RuntimeError: {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
    except Exception as e:
        print(f"\n   后台线程: ❌ 其他错误: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
    finally:
        print("   后台线程: run_launch_server_headless_in_thread 结束。", flush=True)

# --- 新增：函数用于调试模式后台线程 (直接输出) ---
def run_launch_server_debug_direct_output(stop_event: threading.Event):
    global camoufox_server_instance
    if not launch_server:
        print("ERROR (Thread-Debug): launch_server not imported.", file=sys.stderr, flush=True)
        return
    try:
        print("INFO (Thread-Debug): Calling launch_server(headless=False)... Output will appear directly.", flush=True)
        camoufox_server_instance = launch_server(headless=False)
        print("INFO (Thread-Debug): launch_server call returned. Waiting for stop signal.", flush=True)
        stop_event.wait()
        print("INFO (Thread-Debug): Stop signal received, exiting.", flush=True)
    except RuntimeError as re:
        # 特别处理服务器意外终止的情况
        if "Server process terminated unexpectedly" in str(re):
            print("INFO (Thread-Debug): Camoufox服务器已终止，可能是正常关闭的一部分", flush=True)
        else:
            print(f"ERROR (Thread-Debug): 运行时错误: {re}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
    except Exception as e:
        print(f"ERROR (Thread-Debug): {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
    finally:
        print("INFO (Thread-Debug): Thread exiting.", flush=True)

def start_main_server(ws_endpoint, launch_mode, active_auth_json=None):
    """Starts the main server.py script, passing info via environment variables."""
    print(f"DEBUG [launch_camoufox]: Received ws_endpoint in start_main_server: {ws_endpoint} (Type: {type(ws_endpoint)})" )
    global server_py_proc
    print(f"-------------------------------------------------")
    print(f"--- 步骤 3: 启动主 FastAPI 服务器 ({SERVER_PY_FILENAME}) ---")
    server_script_path = os.path.join(os.path.dirname(__file__), SERVER_PY_FILENAME)
    cmd = [PYTHON_EXECUTABLE, server_script_path]
    print(f"   执行命令: {' '.join(cmd)}")

    env = os.environ.copy()
    env['CAMOUFOX_WS_ENDPOINT'] = ws_endpoint
    env['LAUNCH_MODE'] = launch_mode # 传递启动模式
    if active_auth_json:
        env['ACTIVE_AUTH_JSON_PATH'] = active_auth_json # 传递激活的JSON路径
    else:
        # 确保在非 headless 模式下不传递旧的路径
        if 'ACTIVE_AUTH_JSON_PATH' in env:
            del env['ACTIVE_AUTH_JSON_PATH']

    print(f"   设置环境变量 LAUNCH_MODE={launch_mode}")
    if active_auth_json:
        print(f"   设置环境变量 ACTIVE_AUTH_JSON_PATH={os.path.basename(active_auth_json)}")
    print(f"   设置环境变量 CAMOUFOX_WS_ENDPOINT={ws_endpoint[:25]}...")

    try:
        server_py_proc = subprocess.Popen(cmd, text=True, env=env)
        print(f"   主服务器正在后台启动... (查看后续日志)")

        server_py_proc.wait()
        print(f"\n👋 主服务器进程已结束 (代码: {server_py_proc.returncode})。")

    except FileNotFoundError:
        print(f"❌ 错误: 无法执行命令。请确保 Python ({PYTHON_EXECUTABLE}) 和 '{SERVER_PY_FILENAME}' 存在。")
        cleanup()
        sys.exit(1)
    except Exception as e:
        print(f"❌ 启动主服务器时发生意外错误: {e}")
        cleanup()
        sys.exit(1)

async def save_auth_state_debug(ws_endpoint: str): # 新增 async 函数用于保存状态
    """Connects temporarily to the debug browser instance and saves auth state."""
    if not async_playwright:
        print("❌ 错误: Playwright 不可用，无法保存认证状态。")
        return False

    print("   尝试临时连接到调试浏览器以保存认证状态...")
    pw_instance = None
    browser = None
    saved = False
    try:
        async with async_playwright() as pw_instance:
            try:
                browser = await pw_instance.firefox.connect(ws_endpoint, timeout=10000) # 增加超时
                print(f"      ✓ 临时连接成功: {browser.version}")

                # 假设只有一个上下文
                if not browser.contexts:
                     print("      ❌ 错误: 未找到浏览器上下文。")
                     return False

                context = browser.contexts[0]
                save_path = os.path.join(SAVED_AUTH_DIR, 'Account.json')
                print(f"      保存当前状态到: {save_path}...")
                await context.storage_state(path=save_path)
                print(f"      ✓ 认证状态已保存。")
                saved = True
            except TimeoutError:
                 print(f"      ❌ 错误: 连接到 {ws_endpoint} 超时。无法保存状态。")
            except Exception as e:
                 print(f"      ❌ 保存认证状态时出错: {e}")
                 traceback.print_exc()
            finally:
                if browser and browser.is_connected():
                    print("      断开临时连接...")
                    await browser.close()
    except Exception as pw_err:
         print(f"   ❌ 启动或停止 Playwright for saving 时出错: {pw_err}")

    return saved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="启动 Camoufox 服务器和 FastAPI 代理服务器。默认启动无头模式 (实验性)。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="启动调试模式 (有界面)，允许手动操作和保存认证文件，而不是默认的无头模式。"
    )
    args = parser.parse_args()

    print(f"🚀 Camoufox 启动器 🚀")
    print(f"=================================================")
    ensure_auth_dirs_exist() # <--- 调用目录创建函数
    check_dependencies()
    print(f"=================================================")

    print(f"--- 检查遗留登录状态 ({os.path.basename(STORAGE_STATE_PATH)}) ---") # 修改提示
    auth_state_exists = os.path.exists(STORAGE_STATE_PATH)

    if auth_state_exists:
        print(f"   ⚠️ 警告：找到旧的登录状态文件 '{os.path.basename(STORAGE_STATE_PATH)}'。") # 修改提示
        print(f"      此文件不再直接使用。请通过 '调试模式' 生成新的认证文件并放入 'auth_profiles/active'。")
    # else: # 不再需要提示未找到旧文件
    #    print(f"   ✓ 未找到旧的登录状态文件 '{os.path.basename(STORAGE_STATE_PATH)}' (预期行为)。") # 确认新行为
    print(f"-------------------------------------------------")

    launch_mode = None # 'headless', 'debug'
    ws_endpoint = None

    # 1. 确定模式：优先看标志，否则询问用户
    if args.debug: # 检查新的 --debug 标志
        print("--- 模式选择：命令行指定 [--debug] -> 调试模式 (有界面) ---")
        launch_mode = 'debug'
    else:
        # 没有 --debug 标志，询问用户
        print("\n--- 请选择启动模式 ---")
        print("   [1] 无头模式 (实验性) ")
        print("   [2] 调试模式 (有界面)")
        user_choice = ''
        while user_choice not in ['1', '2']:
             user_choice = input("   请输入选项 [1]: ").strip() or '1' # 默认为 1
             if user_choice == '1':
                 print("   用户选择 [1] -> 无头模式 (实验性)")
                 launch_mode = 'headless'
             elif user_choice == '2':
                 print("   用户选择 [2] -> 调试模式 (有界面)")
                 launch_mode = 'debug'
             else:
                 print("   无效输入，请输入 1 或 2。")

    print(f"-------------------------------------------------")

    # 2. 根据最终确定的 launch_mode 执行启动逻辑
    if launch_mode == 'debug':
        print(f"--- 即将启动：调试模式 (有界面) --- ")
        ws_endpoint = None
        camoufox_server_instance = None # Reset instance variable
        stop_server_event.clear() # Ensure event is clear before starting thread

        # <<< 新逻辑：启动后台线程直接输出，主线程等待用户输入 >>>
        try:
            print(f"   正在后台启动 Camoufox 服务器 (有界面)...", flush=True)
            camoufox_server_thread = threading.Thread(
                target=run_launch_server_debug_direct_output, # 使用新的直接输出函数
                args=(stop_server_event,),
                daemon=True
            )
            camoufox_server_thread.start()
            print(f"   后台线程已启动。", flush=True)

            # 短暂等待，让后台线程有机会打印启动信息
            time.sleep(2) # Wait 2 seconds

            print(f"\n--- 请查看上面或新窗口中的 Camoufox 输出 --- ")
            print(f"--- 找到 'Websocket endpoint: ws://...' 行并复制端点 --- ")
            print(f"    (格式为: ws://localhost:xxxxx/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx)")

            # 循环提示直到获得有效输入或用户中断
            ws_regex = re.compile(r"\s*(ws://\S+)\s*")
            while ws_endpoint is None:
                try:
                    pasted_endpoint = input("   请粘贴 WebSocket 端点并按回车: ").strip()
                    if not pasted_endpoint:
                        continue # 忽略空输入

                    match = ws_regex.fullmatch(pasted_endpoint) # 使用 fullmatch
                    if match:
                        ws_endpoint = match.group(1)
                        print(f"   ✅ 已获取端点: {ws_endpoint}")
                    else:
                        print(f"   ❌ 格式错误，请确保粘贴了完整的 'ws://...' 端点。")
                except EOFError: # 用户可能按了 Ctrl+D
                    print("\n   检测到 EOF，退出。")
                    sys.exit(1)
                except KeyboardInterrupt: # 用户按了 Ctrl+C
                     print("\n   检测到中断信号，退出。")
                     sys.exit(1)

        except Exception as e:
            print(f"   ❌ 启动 Camoufox 调试线程或获取用户输入时出错: {e}")
            traceback.print_exc()
            sys.exit(1)

        # <<< 结束新逻辑 >>>

        # 如果成功获取端点，则启动主服务器
        if ws_endpoint:
            print(f"-------------------------------------------------", flush=True)
            print(f"   ✅ WebSocket 端点已获取。准备调用 start_main_server...", flush=True)
            start_main_server(ws_endpoint, launch_mode)
            print(f"   调用 start_main_server 完成。脚本将等待其结束...", flush=True)
        else:
            # 这个分支理论上只会在启动线程/输入环节出错时到达
            print(f"--- 未能成功获取 WebSocket 端点，无法启动主服务器。 ---", flush=True)
            # 确保仍在运行的后台线程被通知停止
            if camoufox_server_thread and camoufox_server_thread.is_alive():
                print("   通知后台线程停止...")
                stop_server_event.set()
            sys.exit(1)

    elif launch_mode == 'headless':
        print(f"--- 即将启动：无头模式 (实验性) --- ")
        active_json_path = None

        # 步骤 9: 检查 active profiles
        print(f"   检查激活认证目录: {ACTIVE_AUTH_DIR}")
        found_json_files = []
        if os.path.isdir(ACTIVE_AUTH_DIR):
            try:
                for filename in sorted(os.listdir(ACTIVE_AUTH_DIR)):
                    if filename.lower().endswith('.json'):
                        full_path = os.path.join(ACTIVE_AUTH_DIR, filename)
                        found_json_files.append(full_path)
            except OSError as e:
                print(f"   ❌ 扫描目录时出错: {e}")
                sys.exit(1)

        if not found_json_files:
            print(f"   ❌ 错误: 未在 '{ACTIVE_AUTH_DIR}' 目录中找到任何 '.json' 认证文件。")
            print(f"      请先使用 '--debug' 模式运行一次，选择 '1' 保存认证文件，然后将其从 '{SAVED_AUTH_DIR}' 移动到 '{ACTIVE_AUTH_DIR}'。")
            sys.exit(1)
        else:
            active_json_path = found_json_files[0] # 选择第一个
            print(f"   ✓ 找到认证文件: {len(found_json_files)} 个。将使用第一个: {os.path.basename(active_json_path)}")

        # 启动后台线程
        stop_server_event.clear() # 重置停止事件
        ws_endpoint = None

        print("   启动后台线程运行 launch_server...")
        camoufox_server_thread = threading.Thread(
            target=run_launch_server_headless_in_thread,
            args=(active_json_path, stop_server_event),
            daemon=True
        )
        camoufox_server_thread.start()

        # 等待几秒让服务器启动并输出信息
        time.sleep(2)

        print(f"\n--- 请查看上面输出中的 'Websocket endpoint:' 行 --- ")
        print(f"--- 复制形如 'ws://localhost:xxxxx/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' 的端点 --- ")

        # 循环提示直到获得有效输入
        ws_regex = re.compile(r"\s*(ws://\S+)\s*")
        while ws_endpoint is None:
            try:
                pasted_endpoint = input("   请粘贴 WebSocket 端点并按回车: ").strip()
                if not pasted_endpoint:
                    continue

                match = ws_regex.fullmatch(pasted_endpoint)
                if match:
                    ws_endpoint = match.group(1)
                    print(f"   ✅ 已获取端点: {ws_endpoint}")
                else:
                    print(f"   ❌ 格式错误，请确保粘贴了完整的 'ws://...' 端点。")
            except EOFError:
                print("\n   检测到 EOF，退出。")
                sys.exit(1)
            except KeyboardInterrupt:
                print("\n   检测到中断信号，退出。")
                sys.exit(1)

        # 如果成功获取端点，则启动主服务器
        if ws_endpoint:
            print(f"-------------------------------------------------", flush=True)
            print(f"   ✅ WebSocket 端点已获取。准备调用 start_main_server...", flush=True)
            start_main_server(ws_endpoint, launch_mode, active_json_path)
            print(f"   调用 start_main_server 完成。脚本将等待其结束...", flush=True)
        else:
            print(f"--- 未能成功获取 WebSocket 端点，无法启动主服务器。 ---", flush=True)
            # 确保仍在运行的后台线程被通知停止
            if camoufox_server_thread and camoufox_server_thread.is_alive():
                print("   通知后台线程停止...")
                stop_server_event.set()
            sys.exit(1)

        print(f"-------------------------------------------------", flush=True)

        # 步骤 14: 更新 cleanup (已完成)
        # 步骤 15-19: 修改 server.py (已完成)

        # print("启动器脚本执行完毕。") # 可以取消注释这个来确认

# Cleanup handled by atexit 