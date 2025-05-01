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

# 尝试导入 launch_server (用于实验性功能)
try:
    from camoufox.server import launch_server
except ImportError:
    # 不再退出，因为它是可选功能
    launch_server = None
    print("⚠️ 警告: 无法导入 'camoufox.server.launch_server'。实验性虚拟显示功能将不可用。")

# Configuration
SERVER_PY_FILENAME = "server.py"
PYTHON_EXECUTABLE = sys.executable
CAMOUFOX_START_TIMEOUT = 30 # seconds to wait for WS endpoint from output (subprocess mode)
EXPERIMENTAL_WAIT_TIMEOUT = 60 # seconds to wait for user to paste endpoint
STORAGE_STATE_PATH = os.path.join(os.path.dirname(__file__), "auth_state.json")

# --- 修改：全局变量需要同时支持两种模式 --- 
camoufox_proc = None # subprocess 模式
camoufox_server_thread = None # launch_server 模式
camoufox_server_instance = None # launch_server 返回值
stop_server_event = threading.Event() # launch_server 模式
server_py_proc = None

def cleanup():
    """Ensures subprocesses and server thread are terminated on exit."""
    global camoufox_proc, server_py_proc, camoufox_server_thread, stop_server_event, camoufox_server_instance
    print(f"\n--- 开始清理 --- ")
    if server_py_proc and server_py_proc.poll() is None:
        print(f"   正在终止 server.py (PID: {server_py_proc.pid})...")
        try:
            server_py_proc.terminate()
            server_py_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print(f"   server.py 未能优雅终止，强制终止 (SIGKILL)..." )
            server_py_proc.kill()
        except Exception as e:
            print(f"   终止 server.py 时出错: {e}")
        server_py_proc = None

    # --- 清理 subprocess (如果使用了该模式) ---
    if camoufox_proc and camoufox_proc.poll() is None:
        print(f"   正在终止 Camoufox 服务器进程 (PID: {camoufox_proc.pid})...")
        try:
            # 尝试更温和的 SIGTERM
            # camoufox_proc.terminate()
            # camoufox_proc.wait(timeout=5)
            # 根据之前的日志，terminate 可能无效，直接 kill
            print(f"   强制终止 (SIGKILL)...")
            camoufox_proc.kill()
            camoufox_proc.wait(timeout=2) # Wait briefly after kill
            print(f"   ✅ Camoufox 服务器进程已终止 (SIGKILL)。")
        # except subprocess.TimeoutExpired:
        #     print(f"   ⚠️ Camoufox 服务器进程未能优雅终止，强制终止 (SIGKILL)...")
        #     camoufox_proc.kill()
        #     try:
        #          camoufox_proc.wait(timeout=2) # Wait briefly after kill
        #     except: pass # Ignore errors after kill
        except Exception as e:
            print(f"   终止 Camoufox 服务器进程时出错: {e}")
        finally:
             camoufox_proc = None # Ensure it's None after handling
    # --- 清理后台线程 (如果使用了该模式) --- 
    if camoufox_server_thread and camoufox_server_thread.is_alive():
        print(f"   正在请求 Camoufox 服务器线程 (launch_server) 停止...")
        stop_server_event.set()
        if camoufox_server_instance and hasattr(camoufox_server_instance, 'close'):
            try:
                print("   尝试调用 camoufox_server_instance.close()...")
                camoufox_server_instance.close()
                print("   实例 close() 调用完成。")
            except Exception as e:
                print(f"   调用 close() 时出错: {e}")
        camoufox_server_thread.join(timeout=10)
        if camoufox_server_thread.is_alive():
            print(f"   ⚠️ Camoufox 服务器线程 (launch_server) 未能及时停止。")
        else:
             print(f"   ✅ Camoufox 服务器线程 (launch_server) 已停止。")
             
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
def start_camoufox_server_subprocess():
    """启动 Camoufox 服务器 (使用 subprocess) 并捕获其 WebSocket 端点。"""
    global camoufox_proc
    print(f"-------------------------------------------------")
    print(f"--- 步骤 2: 启动 Camoufox 服务器 (标准无头模式 - subprocess) ---")
    ws_endpoint = None
    cmd = [PYTHON_EXECUTABLE, "-m", "camoufox", "server"]
    print(f"   执行命令: {' '.join(cmd)}")
    try:
        camoufox_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding='utf-8', errors='replace'
        )
    except FileNotFoundError:
        print(f"❌ 错误: 无法执行命令。请确保 Python ({PYTHON_EXECUTABLE}) 和 camoufox 已正确安装且在 PATH 中。")
        sys.exit(1)
    except Exception as e:
         print(f"❌ 启动 camoufox server 时发生意外错误: {e}")
         sys.exit(1)

    print(f"⏳ 等待 Camoufox 服务器启动并输出 WebSocket 端点 (最长 {CAMOUFOX_START_TIMEOUT} 秒)..." )
    start_time = time.time()
    ws_regex = re.compile(r"(ws://\S+)")
    output_buffer = ""
    try:
        while time.time() - start_time < CAMOUFOX_START_TIMEOUT:
            if camoufox_proc.stdout:
                line = camoufox_proc.stdout.readline()
                if not line:
                    if camoufox_proc.poll() is not None:
                         print(f"   ❌ 错误: Camoufox 服务器进程在输出端点前意外退出 (代码: {camoufox_proc.returncode})。")
                         print("--- 服务器进程最后输出 ---"); print(output_buffer); print("---------------------------")
                         return None
                    else:
                         time.sleep(0.1); continue
                print(f"   [服务器输出] {line.strip()}")
                output_buffer += line
                match = ws_regex.search(line)
                if match:
                    ws_endpoint = match.group(1)
                    print(f"   ✅ 成功捕获 WebSocket 端点: {ws_endpoint}")
                    break
            else:
                 time.sleep(0.1)
            if camoufox_proc.poll() is not None and not ws_endpoint:
                 print(f"   ❌ 错误: Camoufox 服务器进程在循环期间意外退出 (代码: {camoufox_proc.returncode})。")
                 print("--- 服务器进程最后输出 ---"); print(output_buffer); print("---------------------------")
                 return None
    except Exception as e:
        print(f"   读取 Camoufox 服务器输出时出错: {e}"); cleanup(); sys.exit(1)
    if not ws_endpoint:
        print(f"❌ 错误: 在 {CAMOUFOX_START_TIMEOUT} 秒内未能从 Camoufox 服务器获取 WebSocket 端点。")
        print("--- 服务器进程超时前输出 ---"); print(output_buffer); print("---------------------------")
        cleanup(); sys.exit(1)
    print(f"   Camoufox 服务器正在后台运行 (PID: {camoufox_proc.pid})。")
    return ws_endpoint

# --- 函数：使用 launch_server 启动 (实验性虚拟显示模式) ---
def run_launch_server_virtual_in_thread():
    """在后台线程中运行 launch_server(headless=True)。不捕获输出。"""
    global camoufox_server_instance, stop_server_event
    print(f"   后台线程: 准备调用 launch_server(headless=True)...", flush=True)
    try:
        # 直接调用，让它打印到控制台
        camoufox_server_instance = launch_server(headless=True)
        print("   后台线程: launch_server 调用完成 (可能已阻塞)。", flush=True)
        stop_server_event.wait() # 等待停止信号
        print("   后台线程: 收到停止信号，即将退出。", flush=True)
    except Exception as e:
        print(f"\n❌ Camoufox 服务器线程 (launch_server) 运行时发生错误: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
    finally:
        print("   后台线程: run_launch_server_virtual_in_thread 结束。", flush=True)

def start_camoufox_server_virtual():
    """启动 launch_server(headless=True) 并提示用户手动输入端点。"""
    global camoufox_server_thread
    if not launch_server:
         print("❌ 错误：无法启动实验性虚拟显示模式，因为 'launch_server' 未能导入。")
         return None
         
    print(f"-------------------------------------------------")
    print(f"--- 步骤 2: 启动 Camoufox 服务器 (实验性虚拟显示模式) ---")
    print(f"   ⚠️ 警告：此模式为实验性功能。")
    print(f"   将使用 camoufox.server.launch_server(headless=True) 启动。")
    
    ws_endpoint = None
    
    # 启动后台线程
    camoufox_server_thread = threading.Thread(
        target=run_launch_server_virtual_in_thread,
        daemon=True
    )
    camoufox_server_thread.start()

    # 给后台线程一点时间启动并打印信息
    print(f"   后台线程已启动。请在下方输出中查找 WebSocket 端点...")
    time.sleep(5) # 等待 5 秒

    # 检查线程是否还在运行
    if not camoufox_server_thread.is_alive():
        print(f"   ❌ 错误: Camoufox 服务器线程 (launch_server) 似乎未能成功启动或已意外退出。")
        print(f"   请检查上面的日志输出。无法继续。")
        return None
        
    # 提示用户输入
    print("-" * 40)
    print("   ▶️ 请在上面的控制台输出中找到类似以下的行:")
    print("      Websocket endpoint: ws://localhost:xxxxx/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    print("   ▶️ 然后将其完整复制并粘贴到下方提示符后，按 Enter。")
    print("-" * 40)
    
    try:
        # 增加超时，防止无限等待用户输入
        ws_endpoint = input(f"   请输入 WebSocket 端点 (等待 {EXPERIMENTAL_WAIT_TIMEOUT} 秒): ")
        # 添加简单的验证
        if not ws_endpoint or not ws_endpoint.strip().startswith("ws://"):
             print("   ❌ 输入无效或为空。请确保粘贴了正确的 ws:// 地址。")
             ws_endpoint = None
        else:
             ws_endpoint = ws_endpoint.strip()
             print(f"   ✅ 已获取用户输入的端点: {ws_endpoint}")
    except EOFError:
         print("   输入被中断。")
         ws_endpoint = None
    # 可以考虑添加超时处理逻辑，但 input() 本身不直接支持超时
    # 这里我们依赖用户在合理时间内输入

    if not ws_endpoint:
        print("   未能获取有效的 WebSocket 端点。将尝试停止服务器线程。")
        stop_server_event.set() # 请求停止
        return None

    print(f"   Camoufox 服务器 (launch_server) 正在后台运行。")
    return ws_endpoint

def start_main_server(ws_endpoint):
    """Starts the main server.py script, passing the WebSocket endpoint via environment variable."""
    print(f"DEBUG [launch_camoufox]: Received ws_endpoint in start_main_server: {ws_endpoint} (Type: {type(ws_endpoint)})" )
    global server_py_proc
    print(f"-------------------------------------------------")
    print(f"--- 步骤 3: 启动主 FastAPI 服务器 ({SERVER_PY_FILENAME}) ---")
    server_script_path = os.path.join(os.path.dirname(__file__), SERVER_PY_FILENAME)
    cmd = [PYTHON_EXECUTABLE, server_script_path]
    print(f"   执行命令: {' '.join(cmd)}")

    env = os.environ.copy()
    env['CAMOUFOX_WS_ENDPOINT'] = ws_endpoint
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="启动 Camoufox 服务器和 FastAPI 代理服务器。标准模式仅支持无头。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="(仅用于检查) 表明需要有头模式。此脚本不支持自动启动，将提示手动操作。"
    )
    parser.add_argument(
        "--experimental-virtual-display", action="store_true",
        help="(实验性) 尝试使用 launch_server 和虚拟显示无头模式。需要手动粘贴 WebSocket 端点。"
    )
    args = parser.parse_args()

    print(f"🚀 Camoufox 启动器 🚀")
    print(f"=================================================")
    check_dependencies()
    print(f"=================================================")

    print(f"--- 检查登录状态 ({os.path.basename(STORAGE_STATE_PATH)}) ---")
    auth_state_exists = os.path.exists(STORAGE_STATE_PATH)
    
    if auth_state_exists:
        print(f"   ✅ 找到登录状态文件 '{os.path.basename(STORAGE_STATE_PATH)}'。")
    else:
        print(f"   ⚠️ 未找到登录状态文件 '{os.path.basename(STORAGE_STATE_PATH)}'。标准模式将需要手动操作。实验性模式不可用。")
    print(f"-------------------------------------------------")
    
    launch_mode = None # 'standard', 'experimental', 'manual_required'
    ws_endpoint = None

    # 1. 确定模式：优先看标志，否则询问
    if args.experimental_virtual_display:
        print("--- 模式选择：命令行指定 [实验性虚拟显示模式] ---")
        if not launch_server:
             print("   ❌ 错误: 无法启动实验性模式，因为 'launch_server' 未能导入。")
             sys.exit(1)
        if not auth_state_exists:
             print(f"   ❌ 错误: 实验性虚拟显示模式需要有效的登录状态文件 '{os.path.basename(STORAGE_STATE_PATH)}'。")
             sys.exit(1)
        launch_mode = 'experimental'
    elif args.headed:
        print("--- 模式选择：命令行指定 [--headed] (需要手动操作) ---")
        launch_mode = 'manual_required'
    else:
        # 没有指定标志，询问用户
        print("--- 模式选择：请选择启动模式 ---")
        prompt = (
            "   [1] 标准无头模式 (推荐, 自动获取地址)\n"
            "   [2] 实验性虚拟显示模式 (可能无窗口, 需手动粘贴地址)\n"
            "   请输入选项 [1]: "
        )
        user_choice = input(prompt).strip()
        
        if user_choice == '2':
             print("   用户选择 [实验性虚拟显示模式]")
             if not launch_server:
                 print("   ❌ 错误: 无法启动实验性模式，因为 'launch_server' 未能导入。将使用标准模式。")
                 launch_mode = 'standard'
             elif not auth_state_exists:
                  print(f"   ❌ 错误: 实验性虚拟显示模式需要有效的登录状态文件 '{os.path.basename(STORAGE_STATE_PATH)}'。将使用标准模式。")
                  launch_mode = 'standard' # 虽然标准模式也需要，但会在下面处理
             else:
                 launch_mode = 'experimental'
        else: # 默认或选择 1
             print("   用户选择 [标准无头模式] (默认)")
             launch_mode = 'standard'

    print(f"-------------------------------------------------")

    # 2. 根据模式执行启动或打印指南
    if launch_mode == 'standard':
        print(f"--- 即将启动：标准无头模式 --- ")
        if not auth_state_exists:
             print(f"   ❌ 错误：标准模式启动前检测到缺少登录状态文件。需要手动操作。")
             launch_mode = 'manual_required' # 强制转为手动模式
        else:
            print(f"   将使用 subprocess 启动 'python -m camoufox server'...")
            ws_endpoint = start_camoufox_server_subprocess()
           
    elif launch_mode == 'experimental':
        print(f"--- 即将启动：实验性虚拟显示模式 --- ")
        # 前面已经检查过依赖和 auth_state
        print(f"   将使用 launch_server(headless=True) 启动...")
        ws_endpoint = start_camoufox_server_virtual()
       
    # --- 处理需要手动操作的情况 --- 
    if launch_mode == 'manual_required':
        print("--- 需要手动操作 ---")
        # 确保这里的字符串拼接和引号正确
        reason = "缺少登录状态文件。" if not auth_state_exists else "用户通过 --headed 请求。"
        print(f"   原因: {reason}此脚本的自动启动不支持此情况。" )
        print("   ▶️ 请按以下步骤操作:")
        print("      1. 打开一个新的终端窗口。")
        print("      2. 在新终端中手动运行 Camoufox 服务器 (推荐带 --headed): ")
        print(f"         {PYTHON_EXECUTABLE} -m camoufox server --headed")
        print("      3. 在弹出的浏览器窗口中完成登录 (如果需要)。")
        print("      4. 复制该命令输出的 WebSocket 端点 (类似 ws://localhost:xxxxx/...)。")
        print("      5. 将复制的端点设置为主服务器脚本的环境变量 CAMOUFOX_WS_ENDPOINT。")
        # 确保这里的引号正确配对，外双内单
        print(f"         例如 (在运行 server.py 的终端): export CAMOUFOX_WS_ENDPOINT='粘贴的端点'") 
        # 确保这里的 f-string 正确闭合
        print(f"      6. 然后直接运行主服务器脚本: {PYTHON_EXECUTABLE} {SERVER_PY_FILENAME}") 
        print("   -------------------------------------------------")
        sys.exit(1)
       
    # --- 结束手动操作处理 ---

    print(f"-------------------------------------------------")

    # 3. 启动主服务器
    if ws_endpoint:
        print(f"=================================================")
        start_main_server(ws_endpoint)
    else:
         print(f"❌ 未能成功启动 Camoufox 服务器并获取 WebSocket 端点 (模式: {launch_mode})。主服务器无法启动。")

    # Cleanup handled by atexit 