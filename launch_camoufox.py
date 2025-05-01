#!/usr/bin/env python3
import sys
import subprocess
import time
import re
import os
import signal
import atexit

# Configuration
SERVER_PY_FILENAME = "server.py"
PYTHON_EXECUTABLE = sys.executable
CAMOUFOX_SERVER_CHECK_RETRIES = 5
CAMOUFOX_SERVER_CHECK_DELAY = 2 # seconds
CAMOUFOX_START_TIMEOUT = 30 # seconds to wait for WS endpoint

# Global process references for cleanup
camoufox_proc = None
server_py_proc = None

def cleanup():
    """Ensures subprocesses are terminated on exit."""
    global camoufox_proc, server_py_proc
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

    if camoufox_proc and camoufox_proc.poll() is None:
        print(f"   正在终止 camoufox server (PID: {camoufox_proc.pid})...")
        try:
            camoufox_proc.terminate()
            camoufox_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print(f"   camoufox server 未能优雅终止，强制终止 (SIGKILL)...")
            camoufox_proc.kill()
        except Exception as e:
            print(f"   终止 camoufox server 时出错: {e}")
        camoufox_proc = None
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


def start_camoufox_server():
    """Starts 'python -m camoufox server' and captures its WebSocket endpoint."""
    global camoufox_proc
    print(f"-------------------------------------------------")
    print(f"--- 步骤 2: 启动 Camoufox 服务器 ---")
    ws_endpoint = None
    cmd = [PYTHON_EXECUTABLE, "-m", "camoufox", "server"]
    print(f"   执行命令: {' '.join(cmd)}")

    try:
        camoufox_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding='utf-8',
            errors='replace'
        )
    except FileNotFoundError:
        print(f"❌ 错误: 无法执行命令。请确保 Python ({PYTHON_EXECUTABLE}) 和 camoufox 已正确安装。")
        sys.exit(1)
    except Exception as e:
         print(f"❌ 启动 camoufox server 时发生意外错误: {e}")
         sys.exit(1)

    print(f"⏳   等待 Camoufox 服务器启动并输出 WebSocket 端点 (最长 {CAMOUFOX_START_TIMEOUT} 秒)..." )
    start_time = time.time()

    ws_regex = re.compile(r"(ws://\S+)")

    try:
        while time.time() - start_time < CAMOUFOX_START_TIMEOUT:
            if camoufox_proc.stdout:
                line = camoufox_proc.stdout.readline()
                if not line:
                    if camoufox_proc.poll() is not None:
                         print(f"   错误: Camoufox 服务器进程意外退出 (代码: {camoufox_proc.returncode})。无法获取 WebSocket 端点。")
                         break
                    else:
                         time.sleep(0.1)
                         continue

                print(line.strip())
                match = ws_regex.search(line)
                if match:
                    ws_endpoint = match.group(1)
                    print(f"   ✅ 成功捕获 WebSocket 端点: {ws_endpoint}")
                    break
            else:
                 time.sleep(0.1)

            if camoufox_proc.poll() is not None:
                print(f"   错误: Camoufox 服务器进程在输出端点前退出 (代码: {camoufox_proc.returncode})。")
                break

    except Exception as e:
        print(f"   读取 Camoufox 服务器输出时出错: {e}")
        cleanup()
        sys.exit(1)

    if not ws_endpoint:
        print(f"❌ 错误: 在 {CAMOUFOX_START_TIMEOUT} 秒内未能从 Camoufox 服务器获取 WebSocket 端点。")
        cleanup()
        sys.exit(1)

    print(f"DEBUG [launch_camoufox]: Returning ws_endpoint: {ws_endpoint} (Type: {type(ws_endpoint)})" )
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
    print(f"🚀 Camoufox 启动器 (模仿 auto_connect_aistudio.cjs) 🚀")
    print(f"=================================================")
    check_dependencies()
    print(f"=================================================")
    ws_endpoint = start_camoufox_server()
    print(f"=================================================")
    start_main_server(ws_endpoint)
    # Cleanup should run automatically via atexit now 