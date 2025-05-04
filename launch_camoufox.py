#!/usr/bin/env python3
import sys
import subprocess
import time
import re
import os
import signal
import atexit
import argparse
import select
import traceback
import json
import asyncio
import threading
import queue

# 尝试导入 launch_server (用于实验性功能)
try:
    from camoufox.server import launch_server
except ImportError:
    # 如果在 internal-launch 模式下无法导入，则必须退出
    if '--internal-launch' in sys.argv:
        print("❌ 错误：内部启动模式需要 'camoufox.server.launch_server' 但无法导入。", file=sys.stderr)
        sys.exit(1)
    else:
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
# --- 修改：增加等待自动捕获端点的超时时间 ---
ENDPOINT_CAPTURE_TIMEOUT = 45 # seconds to wait for endpoint
STORAGE_STATE_PATH = os.path.join(os.path.dirname(__file__), "auth_state.json")
# --- 新增：认证文件目录 ---
AUTH_PROFILES_DIR = os.path.join(os.path.dirname(__file__), "auth_profiles")
ACTIVE_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, "active")
SAVED_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, "saved")

# --- 修改：全局变量需要同时支持两种模式 --- 
camoufox_proc = None # subprocess 模式 (现在是主要的 Camoufox 进程)
server_py_proc = None

# --- 新增：WebSocket 端点正则表达式 ---
ws_regex = re.compile(r"(ws://\S+)")

# --- 新增：用于后台读取子进程输出的函数 ---
def _enqueue_output(stream, output_queue):
    """Reads lines from a stream and puts them into a queue."""
    try:
        for line in iter(stream.readline, ''):
            output_queue.put(line)
    except ValueError:
        # stream might be closed prematurely
        pass
    except Exception as e:
        print(f"[Reader Thread] Error reading stream: {e}", file=sys.stderr)
    finally:
        # Signal EOF by putting None
        output_queue.put(None)
        stream.close() # Ensure the stream is closed from the reader side
        print("[Reader Thread] Exiting.", flush=True)

def ensure_auth_dirs_exist():
    """确保认证文件目录存在"""
    print("--- 检查认证目录 ---")
    try:
        os.makedirs(ACTIVE_AUTH_DIR, exist_ok=True)
        print(f"   ✓ 激活认证目录: {ACTIVE_AUTH_DIR}")
        os.makedirs(SAVED_AUTH_DIR, exist_ok=True)
        print(f"   ✓ 保存认证目录: {SAVED_AUTH_DIR}")
    except PermissionError as pe:
        print(f"   ❌ 权限错误: {pe}")
        sys.exit(1)
    except FileExistsError as fee:
        print(f"   ❌ 文件已存在错误: {fee}")
        sys.exit(1)
    except OSError as e:
        print(f"   ❌ 创建认证目录时出错: {e}")
        sys.exit(1)
    print("--------------------")

def cleanup():
    """Ensures subprocesses and server thread are terminated on exit."""
    global camoufox_proc, server_py_proc
    print(f"\n--- 开始清理 --- ")
    
    # 1. 终止主 FastAPI 服务器进程 (server.py)
    if server_py_proc and server_py_proc.poll() is None:
        print(f"   正在终止 server.py (PID: {server_py_proc.pid})...")
        try:
            # 尝试发送 SIGTERM
            print(f"   -> 发送 SIGTERM 到 server.py (PID: {server_py_proc.pid})")
            server_py_proc.terminate()

            # --- 新增：尝试读取 server.py 关闭时的输出 --- 
            print(f"   -> 等待最多 5 秒并尝试读取 server.py 的最后输出...") # 更新时间
            shutdown_read_start_time = time.time()
            try:
                 stdout_fd = server_py_proc.stdout.fileno()
                 stderr_fd = server_py_proc.stderr.fileno()
                 # 使用 select 监听，避免完全阻塞
                 while time.time() - shutdown_read_start_time < 5.0: # 更新时间
                     # 检查进程是否已退出
                     if server_py_proc.poll() is not None:
                          break
                     
                     fds_to_watch = []
                     # 只有在流对象仍然存在且未显式关闭时才添加到监视列表
                     if server_py_proc.stdout and not server_py_proc.stdout.closed:
                          fds_to_watch.append(stdout_fd)
                     if server_py_proc.stderr and not server_py_proc.stderr.closed:
                          fds_to_watch.append(stderr_fd)
                          
                     if not fds_to_watch: # 如果两个流都关闭了，则退出
                          break
                          
                     readable_fds, _, _ = select.select(fds_to_watch, [], [], 0.1) # 短暂等待
                     
                     for fd in readable_fds:
                         try:
                              if fd == stdout_fd:
                                   line = server_py_proc.stdout.readline()
                                   if line:
                                        print(f"   [server.py shutdown stdout]: {line.strip()}", flush=True)
                                   else:
                                        # EOF on stdout during shutdown read
                                        pass 
                              elif fd == stderr_fd:
                                   line = server_py_proc.stderr.readline()
                                   if line:
                                        print(f"   [server.py shutdown stderr]: {line.strip()}", flush=True)
                                   else:
                                        # EOF on stderr during shutdown read
                                        pass
                         except ValueError:
                              # 文件描述符可能已失效
                              print(f"   [server.py shutdown]: 读取时文件描述符无效，停止读取。")
                              break # 退出内部 for 循环
                         except Exception as read_line_err:
                              print(f"   [server.py shutdown]: 读取行时出错: {read_line_err}")
                              break # 退出内部 for 循环
                     else: # 跳出内部 for 后跳出外部 while
                           break
                     
                     # 如果 select 超时（没有可读的 fd），则继续循环直到 5 秒结束
                     
            except ValueError as ve:
                 # fileno() 可能在进程快速退出时失败
                 print(f"   [server.py shutdown]: 获取文件描述符时出错 (可能已关闭): {ve}")
            except Exception as e_read:
                 print(f"   [server.py shutdown]: 尝试读取关闭输出时出错: {e_read}")
            # --- 结束新增部分 ---

            # 现在等待进程真正结束
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
        print(f"   正在终止 Camoufox 服务器子进程 (PID: {camoufox_proc.pid})...")
        try:
            if sys.platform != "win32":
                # 尝试使用进程组终止（如果以 start_new_session=True 启动）
                try:
                    pgid = os.getpgid(camoufox_proc.pid)
                    print(f"   尝试使用进程组 (PGID: {pgid}) 终止 (SIGTERM)...")
                    os.killpg(pgid, signal.SIGTERM)
                    time.sleep(1) # 给点时间响应 SIGTERM
                    # 检查是否仍在运行
                    if camoufox_proc.poll() is None:
                        print(f"   进程组 SIGTERM 后仍在运行，尝试强制终止 (SIGKILL)..." )
                        os.killpg(pgid, signal.SIGKILL)
                        camoufox_proc.wait(timeout=3) # 等待 SIGKILL
                except ProcessLookupError:
                    print(f"   ℹ️ 进程组不存在或获取 PGID 失败，尝试直接终止 PID {camoufox_proc.pid}...")
                    camoufox_proc.terminate() # 先尝试 SIGTERM
                    try:
                        camoufox_proc.wait(timeout=5)
                        print(f"   ✓ Camoufox 子进程已终止 (SIGTERM)。")
                    except subprocess.TimeoutExpired:
                        print(f"   ⚠️ Camoufox 子进程未能优雅终止 (SIGTERM 超时)，强制终止 (SIGKILL)..." )
                        camoufox_proc.kill()
                        try: camoufox_proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                             print(f"   ⚠️ 等待 Camoufox SIGKILL 后超时。")
                        print(f"   ✓ Camoufox 子进程已强制终止 (SIGKILL)。")
                except Exception as e:
                    print(f"   ❌ 终止 Camoufox 子进程时出错: {e}")
        except ProcessLookupError:
            print(f"   ℹ️ Camoufox 服务器子进程可能已自行终止。")
        except subprocess.TimeoutExpired:
            print(f"   ⚠️ 等待 Camoufox 子进程终止时超时。")
        except Exception as e:
            print(f"   ❌ 终止 Camoufox 子进程时出错: {e}")
        finally:
             camoufox_proc = None # Ensure it's None after handling
    elif camoufox_proc: # Process exists but already terminated
         print(f"   Camoufox 服务器子进程已自行结束 (代码: {camoufox_proc.poll()})。")
         camoufox_proc = None
    else:
         print(f"   Camoufox 服务器子进程未启动或已清理。")

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
# def run_launch_server_headless_in_thread(...):
#     ...
# def run_launch_server_debug_direct_output(...):
#     ...

def start_main_server(ws_endpoint, launch_mode, active_auth_json=None):
    """Starts the main server.py script, passing info via environment variables."""
    print(f"DEBUG [launch_camoufox]: Received ws_endpoint in start_main_server: {ws_endpoint} (Type: {type(ws_endpoint)})" )
    global server_py_proc
    print(f"-------------------------------------------------")
    print(f"--- 步骤 3: 启动主 FastAPI 服务器 ({SERVER_PY_FILENAME}) ---")
    server_script_path = os.path.join(os.path.dirname(__file__), SERVER_PY_FILENAME)
    cmd = [PYTHON_EXECUTABLE, '-u', server_script_path]
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
        # 修改：捕获 server.py 的输出
        server_py_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, # 分开捕获 stderr
            text=True,
            encoding='utf-8',
            errors='ignore',
            env=env
        )
        print(f"   主服务器 server.py 已启动 (PID: {server_py_proc.pid})。正在捕获其输出...")

        # --- 实时读取并打印 server.py 的输出 --- 
        output_buffer = {"stdout": "", "stderr": ""}
        stdout_closed = False
        stderr_closed = False

        # Helper to read and print a line
        def read_and_print_line(stream, stream_name):
            nonlocal output_buffer, stdout_closed, stderr_closed
            if (stream_name == 'stdout' and stdout_closed) or \
               (stream_name == 'stderr' and stderr_closed) or \
               not stream:
                return True # Stream already closed
            line = stream.readline()
            if line:
                 print(f"   [server.py {stream_name}]: {line.strip()}", flush=True)
                 output_buffer[stream_name] += line
                 return False # Read successfully
            else:
                 print(f"   [server.py {stream_name}]: 输出流已关闭 (EOF).", flush=True)
                 if stream_name == 'stdout':
                     stdout_closed = True
                 else:
                     stderr_closed = True
                 return True # Stream is now closed

        # Loop until both stdout and stderr are closed
        stdout_fd = server_py_proc.stdout.fileno()
        stderr_fd = server_py_proc.stderr.fileno()

        while not (stdout_closed and stderr_closed):
            # Check if process exited prematurely
            return_code = server_py_proc.poll()
            if return_code is not None:
                print(f"   [server.py]: 进程在输出结束前意外退出 (代码: {return_code})。", flush=True)
                # Try one last read after exit before breaking
                try:
                     while True: # Drain stdout
                         if read_and_print_line(server_py_proc.stdout, "stdout"): break
                except: pass # Ignore errors on final read
                try:
                     while True: # Drain stderr
                          if read_and_print_line(server_py_proc.stderr, "stderr"): break
                except: pass
                # Explicitly update flags based on return value, though nonlocal should handle it too
                stdout_closed = True # Mark as closed since process exited
                stderr_closed = True
                break # Exit the reading loop

            # --- 使用 select 等待可读事件 --- 
            fds_to_watch = []
            if not stdout_closed: fds_to_watch.append(stdout_fd)
            if not stderr_closed: fds_to_watch.append(stderr_fd)

            if not fds_to_watch:
                 # Should not happen if loop condition is correct, but as safety break
                 break

            try:
                 # Wait up to 0.5 seconds for either stdout or stderr to have data
                 readable_fds, _, _ = select.select(fds_to_watch, [], [], 0.5)

                 for fd in readable_fds:
                     if fd == stdout_fd:
                         # Read one line if available
                         read_and_print_line(server_py_proc.stdout, "stdout")
                     elif fd == stderr_fd:
                         # Read one line if available
                         read_and_print_line(server_py_proc.stderr, "stderr")
            except ValueError:
                 # select might raise ValueError if a file descriptor becomes invalid (e.g., closed)
                 print("   [server.py]: select() 遇到无效的文件描述符，可能已关闭。更新状态...")
                 # Re-check poll and stream status on error
                 if server_py_proc.poll() is not None:
                      stdout_closed = True
                      stderr_closed = True
                 else:
                      if server_py_proc.stdout.closed: stdout_closed = True
                      if server_py_proc.stderr.closed: stderr_closed = True
            except Exception as select_err:
                 print(f"   [server.py]: select() 发生错误: {select_err}")
                 # Consider breaking or more robust error handling here
                 time.sleep(0.1) # Fallback sleep on select error

        # --- 结束后获取最终退出码 --- 
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
        description="启动 Camoufox 服务器和 FastAPI 代理服务器。支持无头模式和调试模式。", # 更新描述
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    # --- 新增：内部启动参数 --- 
    parser.add_argument(
        '--internal-launch', action='store_true', help=argparse.SUPPRESS # 隐藏此参数
    )
    parser.add_argument(
        '--internal-headless', action='store_true', help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--internal-debug', action='store_true', help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--internal-auth-file', type=str, default=None, help=argparse.SUPPRESS
    )

    # --- 修改：使用互斥组 ---
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--debug", action="store_true",
        help="启动调试模式 (有界面)，允许手动操作和保存认证文件。"
    )
    mode_group.add_argument(
        "--headless", action="store_true",
        help="启动无头模式 (实验性)。需要 'auth_profiles/active' 目录下有认证文件。"
    )
    args = parser.parse_args()

    # ======= 处理内部启动模式 =======
    if args.internal_launch:
        if not launch_server:
            print("❌ 内部错误：launch_server 未定义。", file=sys.stderr)
            sys.exit(1)

        internal_mode = 'debug' if args.internal_debug else 'headless'
        auth_file = args.internal_auth_file

        print(f"--- [内部启动] 模式: {internal_mode}, 认证文件: {os.path.basename(auth_file) if auth_file else '无'} ---", flush=True)
        print(f"--- [内部启动] 将尝试捕获 WebSocket 端点... ---", flush=True)

        try:
            # 直接调用 launch_server，让它打印到标准输出/错误
            if internal_mode == 'headless':
                if not auth_file or not os.path.exists(auth_file):
                    print(f"❌ [内部启动] 错误：无头模式需要有效的认证文件，但未提供或不存在: {auth_file}", file=sys.stderr, flush=True)
                    sys.exit(1)
                print(f"   [内部启动] 调用 launch_server(headless=True, storage_state='{os.path.basename(auth_file)}')", flush=True)
                launch_server(headless=True, storage_state=auth_file)
            else: # debug mode
                print("   [内部启动] 调用 launch_server(headless=False)", flush=True)
                launch_server(headless=False)
            print("--- [内部启动] launch_server 调用完成/返回 (可能已正常停止) --- ", flush=True)
        except Exception as e:
            print(f"❌ [内部启动] 执行 launch_server 时出错: {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            sys.exit(1)
        # launch_server 正常结束后退出
        sys.exit(0)

    # ===============================

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
    camoufox_proc = None # 重置确保变量存在

    # 1. 确定模式：优先看标志，否则询问用户
    if args.debug:
        print("--- 模式选择：命令行指定 [--debug] -> 调试模式 (有界面) ---")
        launch_mode = 'debug'
    elif args.headless:
        print("--- 模式选择：命令行指定 [--headless] -> 无头模式 (实验性) ---")
        launch_mode = 'headless'
    else:
        # 没有标志，询问用户
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

    # 2. 根据最终确定的 launch_mode 启动 Camoufox 子进程并捕获端点
    if launch_mode == 'debug':
        print(f"--- 即将启动：调试模式 (有界面) --- ")
        cmd = [sys.executable, __file__, '--internal-launch', '--internal-debug']
        print(f"   执行命令: {' '.join(cmd)}")
        print(f"   正在启动 Camoufox 子进程 (调试模式)...", flush=True)
        # 设置进程启动选项
        popen_kwargs = {
            'stdout': subprocess.PIPE,
            'stderr': subprocess.STDOUT, # 合并 stderr 到 stdout
            'text': True,
            'bufsize': 1, # 行缓冲
            'encoding': 'utf-8', # 显式指定编码
            'errors': 'ignore' # 忽略解码错误
        }
        if sys.platform != "win32":
            popen_kwargs['start_new_session'] = True
        else:
            popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP

        camoufox_proc = subprocess.Popen(cmd, **popen_kwargs)

        print(f"   Camoufox 子进程已启动 (PID: {camoufox_proc.pid})。等待 WebSocket 端点输出 (最多 {ENDPOINT_CAPTURE_TIMEOUT} 秒)...", flush=True)

        start_time = time.time()
        output_lines = [] # 存储输出以便调试

        # --- 修改：使用线程和队列读取 --- 
        output_queue = queue.Queue()
        reader_thread = threading.Thread(
            target=_enqueue_output,
            args=(camoufox_proc.stdout, output_queue),
            daemon=True # 设置为守护线程
        )
        reader_thread.start()

        ws_endpoint = None # 初始化
        # read_buffer = "" # 不再需要，按行处理

        while time.time() - start_time < ENDPOINT_CAPTURE_TIMEOUT:
            # 检查进程是否已意外退出
            if camoufox_proc.poll() is not None:
                print(f"   ⚠️ Camoufox 子进程在捕获端点期间意外退出 (代码: {camoufox_proc.returncode})。", flush=True)
                break

            try:
                # 从队列获取行，计算剩余超时时间
                remaining_timeout = ENDPOINT_CAPTURE_TIMEOUT - (time.time() - start_time)
                if remaining_timeout <= 0:
                    raise queue.Empty # 手动触发超时
                
                line = output_queue.get(timeout=max(0.1, min(remaining_timeout, 1.0))) # 动态超时

                if line is None: # EOF marker from reader thread
                    print("   ℹ️ 读取线程报告输出流已结束 (EOF)。", flush=True)
                    break # 退出循环

                # 正常处理行
                line = line.strip()
                print(f"   [Camoufox output]: {line}", flush=True) # 打印所有行
                output_lines.append(line)
                match = ws_regex.search(line) # 在行内搜索
                    if match:
                        ws_endpoint = match.group(1)
                    print(f"\n   ✅ 自动捕获到 WebSocket 端点: {ws_endpoint[:40]}...", flush=True)
                    break # 成功获取，退出循环

            except queue.Empty:
                # 超时或队列为空，检查进程状态并继续循环
                if time.time() - start_time >= ENDPOINT_CAPTURE_TIMEOUT:
                     # 真正的总超时
                     print(f"   ❌ 获取 WebSocket 端点超时 ({ENDPOINT_CAPTURE_TIMEOUT} 秒)。", flush=True)
                     ws_endpoint = None # 明确标记为 None
                     break
                # 否则只是 queue.get 的小超时，继续循环
                continue
            except Exception as read_err:
                print(f"   ❌ 处理队列或读取输出时出错: {read_err}", flush=True)
                break # 退出循环

            # 移除旧的 os.read 逻辑
            # try:
            #     chunk = os.read(stdout_fd, 4096)
            #     ...
            # except BlockingIOError:
            #     time.sleep(0.1)
            # except Exception as read_err:
            #     ...

        # --- 结束读取循环 --- 

        # --- 清理读取线程 (虽然是 daemon, 但尝试 join 一下) ---
        # if reader_thread.is_alive():
        #    print("   尝试等待读取线程结束...")
        #    # reader_thread.join(timeout=1.0) # 短暂等待

        # 检查最终结果 (逻辑不变)
        if not ws_endpoint:
            # ... (错误处理逻辑不变) ...
            sys.exit(1)
        else:
            # ... (调用 start_main_server 逻辑不变) ...
            print(f"   调用 start_main_server 完成。脚本将等待其结束...", flush=True)
            start_main_server(ws_endpoint, launch_mode) # 调用 server.py

    elif launch_mode == 'headless':
        print(f"--- 即将启动：无头模式 (实验性) --- ")
        active_json_path = None
        camoufox_proc = None # 重置

        # 检查 active profiles
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
            print(f"      请先使用 '--debug' 模式运行一次，登录后选择 '保存状态' (如果可用)，")
            print(f"      然后将生成的 '.json' 文件从 '{SAVED_AUTH_DIR}' (或 Playwright 保存的位置) 移动到 '{ACTIVE_AUTH_DIR}'。")
            sys.exit(1)
        else:
            active_json_path = found_json_files[0] # 选择第一个
            print(f"   ✓ 找到认证文件: {len(found_json_files)} 个。将使用第一个: {os.path.basename(active_json_path)}")

        try:
            # --- 启动子进程 --- 
            cmd = [
                sys.executable, __file__,
                '--internal-launch',
                '--internal-headless',
                '--internal-auth-file', active_json_path
            ]
            print(f"   执行命令: {' '.join(cmd)}")
            print(f"   正在启动 Camoufox 子进程 (无头模式)...", flush=True)
            popen_kwargs = {
                'stdout': subprocess.PIPE,
                'stderr': subprocess.STDOUT, # 合并 stderr
                'text': True,
                'bufsize': 1,
                'encoding': 'utf-8',
                'errors': 'ignore'
            }
            if sys.platform != "win32":
                popen_kwargs['start_new_session'] = True
            else:
                popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP

            camoufox_proc = subprocess.Popen(cmd, **popen_kwargs)
            print(f"   Camoufox 子进程已启动 (PID: {camoufox_proc.pid})。等待 WebSocket 端点输出 (最多 {ENDPOINT_CAPTURE_TIMEOUT} 秒)...", flush=True)

            start_time = time.time()
            output_lines = []

            # --- 修改：使用线程和队列读取 (与 debug 模式相同) --- 
            output_queue = queue.Queue()
            reader_thread = threading.Thread(
                target=_enqueue_output,
                args=(camoufox_proc.stdout, output_queue),
                daemon=True
            )
            reader_thread.start()

            ws_endpoint = None # 初始化
            # read_buffer = "" # 不再需要

            while time.time() - start_time < ENDPOINT_CAPTURE_TIMEOUT:
                if camoufox_proc.poll() is not None:
                    print(f"   ⚠️ Camoufox 子进程在捕获端点期间意外退出 (代码: {camoufox_proc.returncode})。", flush=True)
                    break

                try:
                    remaining_timeout = ENDPOINT_CAPTURE_TIMEOUT - (time.time() - start_time)
                    if remaining_timeout <= 0:
                         raise queue.Empty
                    
                    line = output_queue.get(timeout=max(0.1, min(remaining_timeout, 1.0)))

                    if line is None: # EOF
                        print("   ℹ️ 读取线程报告输出流已结束 (EOF)。", flush=True)
                        break

                    line = line.strip()
                    print(f"   [Camoufox output]: {line}", flush=True)
                    output_lines.append(line)
                    match = ws_regex.search(line)
                    if match:
                        ws_endpoint = match.group(1)
                        print(f"\n   ✅ 自动捕获到 WebSocket 端点: {ws_endpoint[:40]}...", flush=True)
                        break

                except queue.Empty:
                    if time.time() - start_time >= ENDPOINT_CAPTURE_TIMEOUT:
                         print(f"   ❌ 获取 WebSocket 端点超时 ({ENDPOINT_CAPTURE_TIMEOUT} 秒)。", flush=True)
                         ws_endpoint = None
                         break
                    continue
                except Exception as read_err:
                    print(f"   ❌ 处理队列或读取输出时出错: {read_err}", flush=True)
                    break

            # 移除旧的 os.read 逻辑
            # try:
            #     chunk = os.read(stdout_fd, 4096)
            #     ...
            # except BlockingIOError:
            #     ...
            # except Exception as read_err:
            #     ...

            # --- 结束读取循环 --- 

            # --- 清理读取线程 --- 
            # if reader_thread.is_alive():
            #    print("   尝试等待读取线程结束...")
            #    # reader_thread.join(timeout=1.0)

            # 检查最终结果 (逻辑不变)
            if not ws_endpoint:
                # ... (错误处理逻辑不变) ...
                sys.exit(1)
            else:
                # ... (调用 start_main_server 逻辑不变) ...
            print(f"   调用 start_main_server 完成。脚本将等待其结束...", flush=True)
                start_main_server(ws_endpoint, launch_mode, active_json_path) # 调用 server.py

        except Exception as e: # 添加通用异常处理
            print(f"   ❌ 启动 Camoufox 子进程或捕获端点时出错: {e}")
            traceback.print_exc()
            ws_endpoint = None
            # 确保子进程被终止 (与 debug 模式相同)
            if camoufox_proc and camoufox_proc.poll() is None:
                 print("   正在终止未完成的 Camoufox 子进程...")
                 try:
                      if sys.platform != "win32": os.killpg(os.getpgid(camoufox_proc.pid), signal.SIGKILL)
                      else: subprocess.run(['taskkill', '/F', '/T', '/PID', str(camoufox_proc.pid)], check=False, capture_output=True)
                      camoufox_proc.wait(timeout=3)
                 except Exception as kill_err:
                      print(f"    终止子进程时出错: {kill_err}")
            sys.exit(1)


# Cleanup handled by atexit 