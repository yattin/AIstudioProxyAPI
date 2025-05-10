#!/usr/bin/env python3
# launch_camoufox.py
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
import threading
import queue
import logging
import logging.handlers
import socket
import platform

# --- 新的导入 ---
import uvicorn
from server import app # 从 server.py 导入 FastAPI app 对象
# -----------------

# 尝试导入 launch_server (用于内部启动模式，模拟 Camoufox 行为)
try:
    from camoufox.server import launch_server
except ImportError:
    if '--internal-launch' in sys.argv:
        print("❌ 致命错误：--internal-launch 模式需要 'camoufox.server.launch_server' 但无法导入。", file=sys.stderr)
        print("   这通常意味着 'camoufox' 包未正确安装或不在 PYTHONPATH 中。", file=sys.stderr)
        sys.exit(1)
    else:
        launch_server = None
        # print("⚠️ 警告：无法导入 'camoufox.server.launch_server'。相关的 Camoufox 内部模拟功能将不可用。", file=sys.stderr)


# --- 配置常量 ---
# SERVER_PY_FILENAME = "server.py" # 不再需要，因为我们直接导入 app
PYTHON_EXECUTABLE = sys.executable
ENDPOINT_CAPTURE_TIMEOUT = 45
DEFAULT_SERVER_PORT = 2048

AUTH_PROFILES_DIR = os.path.join(os.path.dirname(__file__), "auth_profiles")
ACTIVE_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, "active")
SAVED_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, "saved")

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
LAUNCHER_LOG_FILE_PATH = os.path.join(LOG_DIR, 'launch_app.log')

# --- 全局进程句柄 ---
camoufox_proc = None    # Camoufox 内部启动的子进程句柄
# server_py_proc = None # 不再需要，server.app 在本进程中运行

# --- 日志记录器实例 ---
logger = logging.getLogger("CamoufoxLauncher")

# --- WebSocket 端点正则表达式 ---
ws_regex = re.compile(r"(ws://\S+)")

# --- 用户输入标记 (这些主要由 server.py 内部使用，launcher 不再直接解析它们) ---
# USER_INPUT_START_MARKER = "__USER_INPUT_START__"
# USER_INPUT_END_MARKER = "__USER_INPUT_END__"

# --- 线程安全的输出队列处理函数 (_enqueue_output) ---
# (代码与上一版本相同，保持不变)
def _enqueue_output(stream, stream_name, output_queue, process_pid_for_log="<未知PID>"):
    log_prefix = f"[读取线程-{stream_name}-PID:{process_pid_for_log}]"
    try:
        for line_bytes in iter(stream.readline, b''):
            if not line_bytes:
                break
            try:
                line_str = line_bytes.decode('utf-8', errors='replace')
                output_queue.put((stream_name, line_str))
            except Exception as decode_err:
                logger.warning(f"{log_prefix} 解码错误: {decode_err}。原始数据 (前100字节): {line_bytes[:100]}")
                output_queue.put((stream_name, f"[解码错误: {decode_err}] {line_bytes[:100]}...\n"))
    except ValueError:
        logger.debug(f"{log_prefix} ValueError (流可能已关闭)。")
        pass
    except Exception as e:
        logger.error(f"{log_prefix} 读取流时发生意外错误: {e}", exc_info=True)
    finally:
        output_queue.put((stream_name, None))
        if hasattr(stream, 'close') and not stream.closed:
            try:
                stream.close()
            except Exception:
                pass
        logger.debug(f"{log_prefix} 线程退出。")

# --- 设置本启动器脚本的日志系统 (setup_launcher_logging) ---
# (代码与上一版本相同，保持不变)
def setup_launcher_logging(log_level=logging.INFO):
    os.makedirs(LOG_DIR, exist_ok=True)
    file_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s')
    console_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.setLevel(log_level)
    logger.propagate = False
    if os.path.exists(LAUNCHER_LOG_FILE_PATH):
        try:
            os.remove(LAUNCHER_LOG_FILE_PATH)
        except OSError:
            pass
    file_handler = logging.handlers.RotatingFileHandler(
        LAUNCHER_LOG_FILE_PATH, maxBytes=2*1024*1024, backupCount=3, encoding='utf-8', mode='w'
    )
    file_handler.setFormatter(file_log_formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(console_log_formatter)
    logger.addHandler(stream_handler)
    logger.info("=" * 30 + " Camoufox启动器日志系统已初始化 " + "=" * 30)
    logger.info(f"日志级别设置为: {logging.getLevelName(logger.getEffectiveLevel())}")
    logger.info(f"日志文件路径: {LAUNCHER_LOG_FILE_PATH}")

# --- 确保认证文件目录存在 (ensure_auth_dirs_exist) ---
# (代码与上一版本相同，保持不变)
def ensure_auth_dirs_exist():
    logger.info("正在检查并确保认证文件目录存在...")
    try:
        os.makedirs(ACTIVE_AUTH_DIR, exist_ok=True)
        logger.info(f"  ✓ 活动认证目录就绪: {ACTIVE_AUTH_DIR}")
        os.makedirs(SAVED_AUTH_DIR, exist_ok=True)
        logger.info(f"  ✓ 已保存认证目录就绪: {SAVED_AUTH_DIR}")
    except Exception as e:
        logger.error(f"  ❌ 创建认证目录失败: {e}", exc_info=True)
        sys.exit(1)

# --- 清理函数 (在脚本退出时执行) ---
def cleanup():
    """确保 Camoufox 内部子进程在脚本退出时被终止。"""
    global camoufox_proc # 只处理 camoufox_proc
    logger.info("--- 开始执行清理程序 (launch_camoufox.py) ---")

    # server.py 的 FastAPI 应用 (app) 会通过 Uvicorn 的关闭机制处理，
    # 通常在 SIGINT/SIGTERM 时由 FastAPI 的 lifespan 优雅关闭。

    if camoufox_proc and camoufox_proc.poll() is None:
        pid = camoufox_proc.pid
        logger.info(f"正在终止 Camoufox 内部子进程 (PID: {pid})...")
        try:
            if sys.platform != "win32" and hasattr(os, 'getpgid') and hasattr(os, 'killpg'):
                try:
                    pgid = os.getpgid(pid)
                    logger.info(f"  向 Camoufox 进程组 (PGID: {pgid}) 发送 SIGTERM 信号...")
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    logger.info(f"  Camoufox 进程组 (PID: {pid}) 未找到，尝试直接终止进程...")
                    camoufox_proc.terminate()
            else:
                logger.info(f"  向 Camoufox (PID: {pid}) 发送 SIGTERM 信号...")
                camoufox_proc.terminate()
            camoufox_proc.wait(timeout=5)
            logger.info(f"  ✓ Camoufox (PID: {pid}) 已通过 SIGTERM 成功终止。")
        except subprocess.TimeoutExpired:
            logger.warning(f"  ⚠️ Camoufox (PID: {pid}) SIGTERM 超时。正在发送 SIGKILL 强制终止...")
            if sys.platform != "win32" and hasattr(os, 'getpgid') and hasattr(os, 'killpg'):
                try:
                    pgid = os.getpgid(pid)
                    logger.info(f"  向 Camoufox 进程组 (PGID: {pgid}) 发送 SIGKILL 信号...")
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    logger.info(f"  Camoufox 进程组 (PID: {pid}) 在 SIGKILL 时未找到，尝试直接强制终止...")
                    camoufox_proc.kill()
            else:
                camoufox_proc.kill()
            try:
                camoufox_proc.wait(timeout=2)
                logger.info(f"  ✓ Camoufox (PID: {pid}) 已通过 SIGKILL 成功终止。")
            except Exception as e_kill:
                logger.error(f"  ❌ 等待 Camoufox (PID: {pid}) SIGKILL 完成时出错: {e_kill}")
        except Exception as e_term:
            logger.error(f"  ❌ 终止 Camoufox (PID: {pid}) 时发生错误: {e_term}", exc_info=True)
        finally:
            if hasattr(camoufox_proc, 'stdout') and camoufox_proc.stdout and not camoufox_proc.stdout.closed:
                camoufox_proc.stdout.close()
            if hasattr(camoufox_proc, 'stderr') and camoufox_proc.stderr and not camoufox_proc.stderr.closed:
                camoufox_proc.stderr.close()
        camoufox_proc = None
    elif camoufox_proc:
        logger.info(f"Camoufox 内部子进程 (PID: {camoufox_proc.pid if hasattr(camoufox_proc, 'pid') else 'N/A'}) 先前已自行结束，退出码: {camoufox_proc.poll()}。")
        camoufox_proc = None
    else:
        logger.info("Camoufox 内部子进程未运行或已清理。")

    logger.info("--- 清理程序执行完毕 (launch_camoufox.py) ---")

atexit.register(cleanup)
def signal_handler(sig, frame):
    logger.info(f"接收到信号 {signal.Signals(sig).name} ({sig})。正在启动退出程序...")
    # Uvicorn 应该会捕获 SIGINT/SIGTERM 并触发 lifespan 的关闭逻辑
    # sys.exit(0) 会确保 atexit 被调用
    sys.exit(0)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- 检查依赖项 (check_dependencies) ---
# (代码与上一版本相同，保持不变)
def check_dependencies():
    logger.info("--- 步骤 1: 检查依赖项 ---")
    required_modules = {}
    if launch_server is not None:
        required_modules["camoufox"] = "camoufox"
    missing_py_modules = []
    dependencies_ok = True
    if required_modules:
        logger.info("正在检查 Python 模块:")
        for module_name, install_package_name in required_modules.items():
            try:
                __import__(module_name)
                logger.info(f"  ✓ 模块 '{module_name}' 已找到。")
            except ImportError:
                logger.error(f"  ❌ 模块 '{module_name}' (包: '{install_package_name}') 未找到。")
                missing_py_modules.append(install_package_name)
                dependencies_ok = False
    else:
        if '--internal-launch' not in sys.argv :
             logger.info("未导入 camoufox.server，跳过对 'camoufox' Python 包的检查。")

    # server.py 现在是作为模块导入的，所以不再检查其文件是否存在，而是检查 app 是否能导入
    try:
        from server import app as server_app_check # 尝试导入 app
        if server_app_check:
             logger.info(f"  ✓ 成功从 'server.py' 导入 'app' 对象。")
    except ImportError as e_import_server:
        logger.error(f"  ❌ 无法从 'server.py' 导入 'app' 对象: {e_import_server}")
        logger.error(f"     请确保 'server.py' 文件存在且没有导入错误。")
        dependencies_ok = False


    if not dependencies_ok:
        logger.error("-------------------------------------------------")
        logger.error("❌ 依赖项检查失败！")
        if missing_py_modules:
            logger.error(f"   缺少的 Python 库: {', '.join(missing_py_modules)}")
            logger.error(f"   请尝试使用 pip 安装: pip install {' '.join(missing_py_modules)}")
        # (移除对 server.py 文件存在的单独检查，已合并到导入检查中)
        logger.error("-------------------------------------------------")
        sys.exit(1)
    else:
        logger.info("✅ 所有启动器依赖项检查通过。")


# --- 端口检查和清理函数 (is_port_in_use, find_pids_on_port, kill_process_interactive) ---
# (代码与上一版本相同，保持不变)
def is_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return False
        except OSError:
            return True
        except Exception as e:
            logger.warning(f"检查端口 {port} (主机 {host}) 时发生未知错误: {e}")
            return True

def find_pids_on_port(port: int) -> list[int]:
    pids = []
    system_platform = platform.system()
    command = ""
    try:
        if system_platform == "Linux" or system_platform == "Darwin":
            command = f"lsof -ti :{port} -sTCP:LISTEN"
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, close_fds=True)
            stdout, stderr = process.communicate(timeout=5)
            if process.returncode == 0 and stdout:
                pids = [int(pid) for pid in stdout.strip().split('\n') if pid.isdigit()]
            elif process.returncode != 0 and ("command not found" in stderr.lower() or "未找到命令" in stderr):
                logger.error(f"命令 'lsof' 未找到。请确保已安装。")
            elif process.returncode not in [0, 1]:
                logger.warning(f"执行 lsof 命令失败 (返回码 {process.returncode}): {stderr.strip()}")
        elif system_platform == "Windows":
            command = f'netstat -ano -p TCP | findstr "LISTENING" | findstr ":{port} "'
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate(timeout=10)
            if process.returncode == 0 and stdout:
                for line in stdout.strip().split('\n'):
                    parts = line.split()
                    if len(parts) >= 4 and parts[0].upper() == 'TCP' and f":{port}" in parts[1]:
                        if parts[-1].isdigit(): pids.append(int(parts[-1]))
                pids = list(set(pids))
            elif process.returncode not in [0, 1]:
                logger.warning(f"执行 netstat/findstr 命令失败 (返回码 {process.returncode}): {stderr.strip()}")
        else:
            logger.warning(f"不支持的操作系统 '{system_platform}' 用于查找占用端口的进程。")
    except FileNotFoundError:
        cmd_name = command.split()[0] if command else "相关工具"
        logger.error(f"命令 '{cmd_name}' 未找到。")
    except subprocess.TimeoutExpired:
        logger.error(f"执行命令 '{command}' 超时。")
    except Exception as e:
        logger.error(f"查找占用端口 {port} 的进程时出错: {e}", exc_info=True)
    return pids

def kill_process_interactive(pid: int) -> bool:
    system_platform = platform.system()
    success = False
    logger.info(f"  尝试终止进程 PID: {pid}...")
    try:
        if system_platform == "Linux" or system_platform == "Darwin":
            result_term = subprocess.run(f"kill {pid}", shell=True, capture_output=True, text=True, timeout=3, check=False)
            if result_term.returncode == 0:
                logger.info(f"    ✓ PID {pid} 已发送 SIGTERM 信号。")
                success = True
            else:
                logger.warning(f"    PID {pid} SIGTERM 失败: {result_term.stderr.strip() or result_term.stdout.strip()}. 尝试 SIGKILL...")
                result_kill = subprocess.run(f"kill -9 {pid}", shell=True, capture_output=True, text=True, timeout=3, check=False)
                if result_kill.returncode == 0:
                    logger.info(f"    ✓ PID {pid} 已发送 SIGKILL 信号。")
                    success = True
                else:
                    logger.error(f"    ✗ PID {pid} SIGKILL 失败: {result_kill.stderr.strip() or result_kill.stdout.strip()}.")
        elif system_platform == "Windows":
            command_desc = f"taskkill /PID {pid} /T /F"
            result = subprocess.run(command_desc, shell=True, capture_output=True, text=True, timeout=5, check=False)
            output = result.stdout.strip()
            error_output = result.stderr.strip()
            if result.returncode == 0 and ("SUCCESS" in output.upper() or "成功" in output):
                logger.info(f"    ✓ PID {pid} 已通过 taskkill /F 终止。")
                success = True
            elif "could not find process" in error_output.lower() or "找不到" in error_output:
                logger.info(f"    PID {pid} 执行 taskkill 时未找到 (可能已退出)。")
                success = True
            else:
                logger.error(f"    ✗ PID {pid} taskkill /F 失败: {(error_output + ' ' + output).strip()}.")
        else:
            logger.warning(f"    不支持的操作系统 '{system_platform}' 用于终止进程。")
    except Exception as e:
        logger.error(f"    终止 PID {pid} 时发生意外错误: {e}", exc_info=True)
    return success

# --- 带超时的用户输入函数 (input_with_timeout) ---
# (代码与上一版本相同，保持不变)
def input_with_timeout(prompt_message: str, timeout_seconds: int = 30) -> str:
    print(prompt_message, end='', flush=True)
    if sys.platform == "win32":
        user_input_container = [None]
        def get_input_in_thread():
            try:
                user_input_container[0] = sys.stdin.readline().strip()
            except Exception:
                user_input_container[0] = ""
        input_thread = threading.Thread(target=get_input_in_thread, daemon=True)
        input_thread.start()
        input_thread.join(timeout=timeout_seconds)
        if input_thread.is_alive():
            print("\n输入超时。将使用默认值。", flush=True)
            return ""
        return user_input_container[0] if user_input_container[0] is not None else ""
    else:
        readable_fds, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
        if readable_fds:
            return sys.stdin.readline().strip()
        else:
            print("\n输入超时。将使用默认值。", flush=True)
            return ""

# --- 主执行逻辑 ---
if __name__ == "__main__":
    if '--internal-launch' not in sys.argv:
        setup_launcher_logging(log_level=logging.INFO)
    else:
        # 内部启动模式，不需要 launcher 的完整日志，但可以简单提示
        # print(f"INFO: launch_camoufox.py running in --internal-launch mode.", file=sys.stderr)
        pass # 保持安静，让父进程捕获其 stdout/stderr

    parser = argparse.ArgumentParser(
        description="Camoufox 浏览器模拟与 FastAPI 代理服务器的启动器。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('--internal-launch', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--internal-headless', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--internal-debug', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--internal-auth-file', type=str, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--server-port", type=int, default=DEFAULT_SERVER_PORT, help=f"FastAPI 服务器监听的端口号 (默认: {DEFAULT_SERVER_PORT})")
    mode_selection_group = parser.add_mutually_exclusive_group()
    mode_selection_group.add_argument("--debug", action="store_true", help="启动调试模式 (浏览器界面可见，允许交互式认证)")
    mode_selection_group.add_argument("--headless", action="store_true", help="启动无头模式 (浏览器无界面，需要预先保存的认证文件)")
    args = parser.parse_args()

    if args.internal_launch:
        if not launch_server:
            print("❌ 致命错误 (--internal-launch): camoufox.server.launch_server 不可用。脚本无法继续。", file=sys.stderr)
            sys.exit(1)
        internal_mode = 'debug' if args.internal_debug else 'headless'
        auth_file = args.internal_auth_file
        print(f"--- [内部Camoufox启动] 模式: {internal_mode}, 认证文件: {os.path.basename(auth_file) if auth_file else '无'} ---", flush=True)
        print(f"--- [内部Camoufox启动] 正在调用 camoufox.server.launch_server 以获取 WebSocket 端点... ---", flush=True)
        try:
            if internal_mode == 'headless':
                if not auth_file or not os.path.exists(auth_file):
                    print(f"❌ 错误 (--internal-launch): 无头模式需要一个有效的认证文件路径，但提供的是 '{auth_file}'", file=sys.stderr, flush=True)
                    sys.exit(1)
                launch_server(headless=True, storage_state=auth_file)
            else:
                launch_server(headless=False)
            print(f"--- [内部Camoufox启动] camoufox.server.launch_server 调用已完成。 --- ", flush=True)
        except Exception as e_internal_launch:
            print(f"❌ 错误 (--internal-launch): 执行 camoufox.server.launch_server 时发生异常: {e_internal_launch}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    logger.info("🚀 Camoufox 启动器开始运行 🚀")
    logger.info("=================================================")
    ensure_auth_dirs_exist()
    check_dependencies() # 现在会检查 app 是否能从 server.py 导入
    logger.info("=================================================")
    deprecated_auth_state_path = os.path.join(os.path.dirname(__file__), "auth_state.json")
    if os.path.exists(deprecated_auth_state_path):
        logger.warning(f"检测到已弃用的认证文件: {deprecated_auth_state_path}。此文件不再被直接使用。")
        logger.warning("请使用调试模式生成新的认证文件，并按需管理 'auth_profiles' 目录中的文件。")

    final_launch_mode = None
    if args.debug:
        final_launch_mode = 'debug'
        logger.info("通过 --debug 参数选择启动模式: 调试模式")
    elif args.headless:
        final_launch_mode = 'headless'
        logger.info("通过 --headless 参数选择启动模式: 无头模式")
    else:
        logger.info("--- 请选择启动模式 (未通过命令行参数指定) ---")
        user_mode_choice = input_with_timeout(f"  请输入启动模式 [1] 无头模式, [2] 调试模式 (默认: 1 无头模式，{15}秒超时): ", 15) or '1'
        if user_mode_choice == '1':
            final_launch_mode = 'headless'
            logger.info("用户选择: 无头模式")
        elif user_mode_choice == '2':
            final_launch_mode = 'debug'
            logger.info("用户选择: 调试模式")
        else:
            final_launch_mode = 'headless'
            logger.info(f"无效输入 '{user_mode_choice}' 或超时，默认启动模式: 无头模式")
    logger.info("-------------------------------------------------")

    server_target_port = args.server_port
    logger.info(f"--- 步骤 2: 检查 FastAPI 服务器目标端口 ({server_target_port}) 是否被占用 ---")
    port_is_available = False
    uvicorn_bind_host = "0.0.0.0" # Uvicorn 将绑定的主机
    if is_port_in_use(server_target_port, host=uvicorn_bind_host):
        logger.warning(f"  ❌ 端口 {server_target_port} (主机 {uvicorn_bind_host}) 当前被占用。")
        pids_on_port = find_pids_on_port(server_target_port)
        if pids_on_port:
            logger.warning(f"     识别到以下进程 PID 可能占用了端口 {server_target_port}: {pids_on_port}")
            if final_launch_mode == 'debug':
                sys.stderr.flush() # 确保日志先于 input 提示显示
                choice = input(f"     是否尝试终止这些进程？ (y/n, 输入 n 将继续并可能导致启动失败): ").strip().lower()
                if choice == 'y':
                    logger.info("     用户选择尝试终止进程...")
                    all_killed = all(kill_process_interactive(pid) for pid in pids_on_port)
                    if all_killed: # 即使部分失败，也可能端口已释放
                        logger.info("     所有识别的进程终止尝试完成。等待2秒后重新检查...")
                        time.sleep(2)
                        if not is_port_in_use(server_target_port, host=uvicorn_bind_host):
                            logger.info(f"     ✅ 端口 {server_target_port} (主机 {uvicorn_bind_host}) 现在可用。")
                            port_is_available = True
                        else:
                            logger.error(f"     ❌ 尝试终止后，端口 {server_target_port} (主机 {uvicorn_bind_host}) 仍然被占用。")
                    else: # kill_process_interactive 返回了 False
                        logger.warning("     并非所有进程都被成功终止。端口可能仍被占用。")
                        if not is_port_in_use(server_target_port, host=uvicorn_bind_host): # 再次检查，万一呢
                             logger.info(f"     但端口 {server_target_port} (主机 {uvicorn_bind_host}) 现在可用了 (可能相关进程已自行退出)。")
                             port_is_available = True

                else: # 用户选择 'n'
                    logger.info("     用户选择不自动终止。将继续尝试启动服务器。")
            else: # 无头模式
                logger.error(f"     无头模式下，不会尝试自动终止占用端口的进程。服务器启动可能会失败。")
        else: # 未找到占用进程的PID
            logger.warning(f"     未能自动识别占用端口 {server_target_port} 的进程。服务器启动可能会失败。")

        if not port_is_available and final_launch_mode == 'debug' and choice != 'n':
             logger.error(f"调试模式下端口 {server_target_port} 问题未解决。若要强行继续，请在提示时选择 'n'。")
             # sys.exit(1) # 可以选择在这里退出
        elif not port_is_available and final_launch_mode == 'headless':
             logger.error(f"无头模式下端口 {server_target_port} 被占用，服务器启动极有可能失败。请先手动清理端口。")
             # sys.exit(1) # 无头模式下更应该严格
    else:
        logger.info(f"  ✅ 端口 {server_target_port} (主机 {uvicorn_bind_host}) 当前可用。")
        port_is_available = True

    if not port_is_available:
        logger.warning(f"--- 端口 {server_target_port} 仍可能被占用。继续启动服务器，它将自行处理端口绑定。 ---")
    else:
        logger.info(f"--- 端口 {server_target_port} 检查完毕。 ---")


    captured_ws_endpoint = None
    auth_file_for_server_lifespan = None # 重命名变量以更清晰
    camoufox_internal_base_cmd = [PYTHON_EXECUTABLE, '-u', __file__, '--internal-launch']
    camoufox_popen_kwargs = {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE, 'env': os.environ.copy()}
    camoufox_popen_kwargs['env']['PYTHONIOENCODING'] = 'utf-8'

    if final_launch_mode == 'debug':
        logger.info("--- 步骤 3: 内部启动 Camoufox (调试模式)... ---")
        # 新增: 调试模式下的认证文件选择逻辑
        logger.info(f"  调试模式: 检查可用的认证文件...")
        available_profiles = []
        for profile_dir_path_str, dir_label in [(ACTIVE_AUTH_DIR, "active"), (SAVED_AUTH_DIR, "saved")]:
            profile_dir_path = os.path.join(os.path.dirname(__file__), profile_dir_path_str) # 确保是绝对或相对工作区的正确路径
            if os.path.exists(profile_dir_path):
                try:
                    for filename in os.listdir(profile_dir_path):
                        if filename.lower().endswith(".json"):
                            full_path = os.path.join(profile_dir_path, filename)
                            # 使用 dir_label 来区分来源，例如 "active/auth.json" 或 "saved/auth.json"
                            available_profiles.append({"name": f"{dir_label}/{filename}", "path": full_path})
                except OSError as e:
                    logger.warning(f"   ⚠️ 警告: 无法读取目录 '{profile_dir_path}': {e}")

        if available_profiles:
            print('-'*60 + "\n   找到以下可用的认证文件:", flush=True)
            for i, profile in enumerate(available_profiles):
                print(f"     {i+1}: {profile['name']}", flush=True)
            print("     N: 不加载任何文件 (使用浏览器当前状态)\n" + '-'*60, flush=True)

            choice_prompt = "   请选择要加载的认证文件编号 (输入 N 或直接回车则不加载): "
            choice = input_with_timeout(choice_prompt, 30) # 使用已有的带超时输入函数

            if choice.strip().lower() not in ['n', '']:
                try:
                    choice_index = int(choice.strip()) - 1
                    if 0 <= choice_index < len(available_profiles):
                        selected_profile = available_profiles[choice_index]
                        auth_file_for_server_lifespan = selected_profile["path"] # 存储选择的文件
                        logger.info(f"   已选择加载认证文件: {selected_profile['name']}")
                        print(f"   已选择加载: {selected_profile['name']}", flush=True)
                    else:
                        logger.info("   无效的选择编号。将不加载认证文件。")
                        print("   无效的选择编号。将不加载认证文件。", flush=True)
                except ValueError:
                    logger.info("   无效的输入。将不加载认证文件。")
                    print("   无效的输入。将不加载认证文件。", flush=True)
            else:
                logger.info("   好的，不加载认证文件。")
                print("   好的，不加载认证文件。", flush=True)
            print('-'*60, flush=True)
        else:
            logger.info("   未找到认证文件。将使用浏览器当前状态。")
            print("   未找到认证文件。将使用浏览器当前状态。", flush=True)
        # 结束: 调试模式下的认证文件选择逻辑

        camoufox_internal_full_cmd = camoufox_internal_base_cmd + ['--internal-debug']
        if auth_file_for_server_lifespan: # 如果在调试模式下选择了文件
            camoufox_internal_full_cmd.extend(['--internal-auth-file', auth_file_for_server_lifespan])
        if sys.platform != "win32":
            camoufox_popen_kwargs['start_new_session'] = True
    elif final_launch_mode == 'headless':
        logger.info("--- 步骤 3: 内部启动 Camoufox (无头模式)... ---")
        logger.info(f"  正在扫描活动认证文件目录: {ACTIVE_AUTH_DIR}")
        try:
            active_json_files = [f for f in os.listdir(ACTIVE_AUTH_DIR) if f.lower().endswith('.json')]
            if not active_json_files:
                logger.error(f"  ❌ 错误: 在活动认证目录 '{ACTIVE_AUTH_DIR}' 中未找到任何 '.json' 认证文件。")
                sys.exit(1)
            auth_file_for_server_lifespan = os.path.join(ACTIVE_AUTH_DIR, sorted(active_json_files)[0])
            logger.info(f"  将使用认证文件进行无头模式启动: {os.path.basename(auth_file_for_server_lifespan)}")
            camoufox_internal_full_cmd = camoufox_internal_base_cmd + ['--internal-headless', '--internal-auth-file', auth_file_for_server_lifespan]
            if sys.platform == "win32":
                camoufox_popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            else:
                camoufox_popen_kwargs['start_new_session'] = True
        except FileNotFoundError:
            logger.error(f"  ❌ 错误: 活动认证目录 '{ACTIVE_AUTH_DIR}' 不存在。")
            sys.exit(1)
        except Exception as e_listdir:
            logger.error(f"  ❌ 错误: 扫描活动认证目录时发生错误: {e_listdir}", exc_info=True)
            sys.exit(1)
    else:
        logger.critical("未知的 final_launch_mode，退出。")
        sys.exit(1)

    try:
        logger.info(f"  将执行 Camoufox 内部启动命令: {' '.join(camoufox_internal_full_cmd)}")
        camoufox_proc = subprocess.Popen(camoufox_internal_full_cmd, **camoufox_popen_kwargs)
        logger.info(f"  Camoufox 内部进程已启动 (PID: {camoufox_proc.pid})。正在等待 WebSocket 端点输出 (最长 {ENDPOINT_CAPTURE_TIMEOUT} 秒)...")
        camoufox_output_q = queue.Queue()
        camoufox_stdout_reader = threading.Thread(target=_enqueue_output, args=(camoufox_proc.stdout, "stdout", camoufox_output_q, camoufox_proc.pid), daemon=True)
        camoufox_stderr_reader = threading.Thread(target=_enqueue_output, args=(camoufox_proc.stderr, "stderr", camoufox_output_q, camoufox_proc.pid), daemon=True)
        camoufox_stdout_reader.start()
        camoufox_stderr_reader.start()
        ws_capture_start_time = time.time()
        camoufox_ended_streams_count = 0
        while time.time() - ws_capture_start_time < ENDPOINT_CAPTURE_TIMEOUT:
            if camoufox_proc.poll() is not None:
                logger.error(f"  Camoufox 内部进程 (PID: {camoufox_proc.pid}) 在等待 WebSocket 端点期间已意外退出，退出码: {camoufox_proc.poll()}。")
                break
            try:
                stream_name, line_from_camoufox = camoufox_output_q.get(timeout=0.2)
                if line_from_camoufox is None:
                    camoufox_ended_streams_count += 1
                    logger.debug(f"  [InternalCamoufox-{stream_name}-PID:{camoufox_proc.pid}] 输出流已关闭 (EOF)。")
                    if camoufox_ended_streams_count >= 2:
                        logger.info(f"  Camoufox 内部进程 (PID: {camoufox_proc.pid}) 的所有输出流均已关闭。")
                        break
                    continue
                log_line_content = f"[InternalCamoufox-{stream_name}-PID:{camoufox_proc.pid}]: {line_from_camoufox.rstrip()}"
                if stream_name == "stderr" or "ERROR" in line_from_camoufox.upper():
                    logger.warning(log_line_content)
                else:
                    logger.info(log_line_content)
                ws_match = ws_regex.search(line_from_camoufox)
                if ws_match:
                    captured_ws_endpoint = ws_match.group(1)
                    logger.info(f"  ✅ 成功从 Camoufox 内部进程捕获到 WebSocket 端点: {captured_ws_endpoint[:40]}...")
                    break
            except queue.Empty:
                continue
        if camoufox_stdout_reader.is_alive(): camoufox_stdout_reader.join(timeout=0.5)
        if camoufox_stderr_reader.is_alive(): camoufox_stderr_reader.join(timeout=0.5)
        if not captured_ws_endpoint:
            logger.error(f"  ❌ 未能在 {ENDPOINT_CAPTURE_TIMEOUT} 秒内从 Camoufox 内部进程 (PID: {camoufox_proc.pid if camoufox_proc else 'N/A'}) 捕获到 WebSocket 端点。")
            if camoufox_proc and camoufox_proc.poll() is None:
                logger.error("  Camoufox 内部进程仍在运行，但未输出预期的 WebSocket 端点。请检查其日志或行为。")
            sys.exit(1)
    except Exception as e_launch_camoufox_internal:
        logger.critical(f"  ❌ 在内部启动 Camoufox 或捕获其 WebSocket 端点时发生致命错误: {e_launch_camoufox_internal}", exc_info=True)
        sys.exit(1)

    if captured_ws_endpoint:
        logger.info("-------------------------------------------------")
        logger.info(f"--- 步骤 4: 启动集成的 FastAPI 服务器 (监听端口: {server_target_port}) ---")
        try:
            # 设置环境变量供 server.app.lifespan 使用
            os.environ['CAMOUFOX_WS_ENDPOINT'] = captured_ws_endpoint
            os.environ['LAUNCH_MODE'] = final_launch_mode
            if final_launch_mode == 'headless' and auth_file_for_server_lifespan:
                os.environ['ACTIVE_AUTH_JSON_PATH'] = auth_file_for_server_lifespan
            elif final_launch_mode == 'debug' and auth_file_for_server_lifespan: # 新增：调试模式也设置环境变量
                os.environ['ACTIVE_AUTH_JSON_PATH'] = auth_file_for_server_lifespan
            
            # 控制 server.py 内部的日志和 print 重定向
            # 推荐：在调试模式下，不重定向 server.py 的 print，以便 input() 提示可见
            # 在无头模式下，可以考虑重定向 print 到日志
            server_redirect_print = 'true' if final_launch_mode == 'headless' else 'false'
            os.environ['SERVER_REDIRECT_PRINT'] = server_redirect_print
            os.environ['SERVER_LOG_LEVEL'] = 'INFO' # 或者根据需要调整

            logger.info(f"  为 server.app 设置的环境变量:")
            logger.info(f"    CAMOUFOX_WS_ENDPOINT={captured_ws_endpoint[:40]}...")
            logger.info(f"    LAUNCH_MODE={final_launch_mode}")
            if 'ACTIVE_AUTH_JSON_PATH' in os.environ:
                logger.info(f"    ACTIVE_AUTH_JSON_PATH={os.path.basename(os.environ['ACTIVE_AUTH_JSON_PATH'])}")
            logger.info(f"    SERVER_REDIRECT_PRINT={server_redirect_print}")
            logger.info(f"    SERVER_LOG_LEVEL={os.environ['SERVER_LOG_LEVEL']}")

            logger.info(f"  即将运行 Uvicorn，加载 server:app ...")
            uvicorn.run(
                app, # 从 server.py 导入的 FastAPI app 对象
                host="0.0.0.0",
                port=server_target_port,
                log_config=None # 重要：让 server.py 的 lifespan 中的日志配置生效
                                # 而不是被 uvicorn 的默认日志覆盖或冲突
            )
            # Uvicorn 运行是阻塞的，直到服务器停止 (例如 Ctrl+C)
            logger.info("Uvicorn 服务器已停止。")

        except SystemExit as e_sysexit: # Uvicorn 可能通过 sys.exit() 退出
            logger.info(f"Uvicorn 或其子系统通过 sys.exit({e_sysexit.code}) 退出。")
            # atexit 注册的 cleanup 会执行
        except Exception as e_uvicorn:
            logger.critical(f"❌ 运行 Uvicorn 时发生致命错误: {e_uvicorn}", exc_info=True)
            # atexit 注册的 cleanup 会执行
            sys.exit(1) # 确保以错误码退出
    else:
        logger.error("  ❌ 未能捕获到 WebSocket 端点，无法启动 FastAPI 服务器。")
        sys.exit(1)

    logger.info("🚀 Camoufox 启动器主逻辑执行完毕 🚀")