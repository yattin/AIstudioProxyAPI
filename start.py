# start.py
import os
import subprocess
import sys
import copy
import socket
import platform
import time
from typing import List

# --- 配置 ---
TARGET_PORT = 2048  # server.py 默认使用的端口
# -----------

# --- 跨平台端口检查和进程处理函数 ---
def is_port_in_use(port: int) -> bool:
    """检查指定端口是否被占用"""
    # 尝试 IPv4
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            # 允许地址重用，主要用于防止 TIME_WAIT 状态的干扰
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            return False  # 绑定成功，端口可用
        except OSError:
            # 端口被占用
            return True
        except Exception as e:
            print(f"警告：检查端口 {port} 时发生未知错误: {e}", file=sys.stderr)
            return True  # 未知错误，保守认为不可用

def find_pids_on_port(port: int) -> List[int]:
    """尝试查找占用指定端口的进程 PIDs (跨平台)"""
    pids = []
    system = platform.system()
    command = ""

    try:
        if system == "Linux" or system == "Darwin":  # Darwin is macOS
            command = f"lsof -ti :{port} -sTCP:LISTEN"
            # 在 Popen 中添加 close_fds=True (对 Unix 系统是个好习惯)
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, close_fds=True)
            stdout, stderr = process.communicate(timeout=5)  # 设置超时
            if process.returncode == 0 and stdout:
                pids = [int(pid) for pid in stdout.strip().split('\n') if pid.isdigit()]
            elif process.returncode != 0 and ("command not found" in stderr.lower() or "未找到命令" in stderr): # 检查 stderr
                print(f"错误：'lsof' 命令未找到。请确保它已安装并位于 PATH 中。", file=sys.stderr)
            # lsof 在找不到监听进程时返回 1，这不是错误
            elif process.returncode not in [0, 1]:
                print(f"警告：执行 lsof 命令失败，返回码 {process.returncode}。错误: {stderr.strip()}", file=sys.stderr)

        elif system == "Windows":
            command = f'netstat -ano -p TCP | findstr "LISTENING" | findstr ":{port} "'  # 更精确的 findstr
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate(timeout=10)  # netstat 可能较慢
            if process.returncode == 0 and stdout:
                for line in stdout.strip().split('\n'):
                    parts = line.split()
                    # 确保 parts[1] (本地地址) 存在且包含端口号
                    if len(parts) >= 4 and parts[0].upper() == 'TCP' and f":{port}" in parts[1]:
                        # PID 在最后
                        pid_str = parts[-1]
                        if pid_str.isdigit():
                            pids.append(int(pid_str))
                pids = list(set(pids)) # 去重
            # findstr 找不到匹配项时返回 1，这不是错误
            elif process.returncode not in [0, 1]:
                print(f"警告：执行 netstat/findstr 命令失败，返回码 {process.returncode}。错误: {stderr.strip()}", file=sys.stderr)
        else:
            print(f"警告：不支持的操作系统 '{system}' 用于查找进程。", file=sys.stderr)

    except FileNotFoundError:
        cmd_name = command.split()[0] if command else "相关工具"
        print(f"错误：命令 '{cmd_name}' 未找到。请确保相关工具已安装并位于 PATH 中。", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"错误：执行命令 '{command}' 超时。", file=sys.stderr)
    except Exception as e:
        print(f"错误：查找占用端口 {port} 的进程时出错：{e}", file=sys.stderr)
    return pids

def kill_process(pid: int) -> bool:
    """尝试终止指定 PID 的进程 (跨平台)"""
    system = platform.system()
    command_desc = "" # 用于日志
    success = False
    # 将所有 print 输出到 sys.stderr，因为 start.py 的主要目的是启动后台进程
    print(f"  尝试终止进程 PID: {pid}...", end="", flush=True, file=sys.stderr)
    try:
        if system == "Linux" or system == "Darwin":
            # 先尝试 SIGTERM (优雅退出)
            command_desc = f"kill {pid} (SIGTERM)"
            result = subprocess.run(f"kill {pid}", shell=True, capture_output=True, text=True, check=False, timeout=3)
            if result.returncode == 0:
                print(" [SIGTERM 发送成功]", flush=True, file=sys.stderr)
                success = True
            else:
                # 如果 SIGTERM 失败，尝试 SIGKILL
                error_output_term = (result.stderr.strip() or result.stdout.strip())
                print(f" [SIGTERM 失败: {error_output_term if error_output_term else '无输出'}]。尝试 SIGKILL...", end="", flush=True, file=sys.stderr)
                command_desc = f"kill -9 {pid} (SIGKILL)"
                result_kill = subprocess.run(f"kill -9 {pid}", shell=True, capture_output=True, text=True, check=False, timeout=3)
                if result_kill.returncode == 0:
                    print(" [SIGKILL 发送成功]", flush=True, file=sys.stderr)
                    success = True
                else:
                    error_output_kill = (result_kill.stderr.strip() or result_kill.stdout.strip())
                    print(f" [SIGKILL 失败: {error_output_kill if error_output_kill else '无输出'}]", flush=True, file=sys.stderr)

        elif system == "Windows":
            # Windows: 使用 taskkill /F (强制终止)
            command_desc = f"taskkill /PID {pid} /T /F"
            result = subprocess.run(command_desc, shell=True, capture_output=True, text=True, check=False, timeout=5)
            output = result.stdout.strip()
            error_output = result.stderr.strip()
            if result.returncode == 0 and ("SUCCESS" in output.upper() or "成功" in output):
                print(" [taskkill /T /F 成功]", flush=True, file=sys.stderr)
                success = True
            # 常见的错误是"找不到进程"
            elif "could not find process" in error_output.lower() or "找不到具有指定 PID 的进程" in error_output or "找不到进程" in error_output:
                print(f" [taskkill 失败: 进程 {pid} 可能已退出]", flush=True, file=sys.stderr)
                success = True  # 认为目标已达成
            else:
                full_error = (error_output + " " + output).strip()
                print(f" [taskkill /T /F 失败: {full_error if full_error else '无输出'}]", flush=True, file=sys.stderr)
        else:
            print(f" [不支持的操作系统: {system}]", flush=True, file=sys.stderr)

    except FileNotFoundError:
        cmd_name = command_desc.split()[0] if command_desc else "命令"
        print(f" [错误：命令 '{cmd_name}' 未找到]", flush=True, file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f" [错误：终止进程 {pid} 的命令 '{command_desc}' 执行超时]", flush=True, file=sys.stderr)
    except Exception as e:
        print(f" [错误：终止进程 {pid} 时发生未知错误: {e}]", flush=True, file=sys.stderr)
    return success

def main():
    """
    检查端口，提示用户进行代理设置，然后以 --headless 模式
    在新的独立后台进程中启动 launch_camoufox.py。
    重要提示：此模式需要 'auth_profiles/active/' 目录下有有效的认证文件。
    """
    # --- 功能介绍 ---
    def clear_screen():
        """跨平台清屏函数"""
        if sys.platform == "win32":
            os.system('cls')
        else:
            os.system('clear')

    clear_screen()
    # 所有介绍性 print 输出到 sys.stderr
    print("=================================================", file=sys.stderr)
    print("== Camoufox 代理配置启动器 (start.py) ==", file=sys.stderr)
    print("=================================================", file=sys.stderr)
    print("本脚本提供了一个用户友好的交互界面，用于在启动主程序 (launch_camoufox.py) 前设置 HTTP/HTTPS 代理。", file=sys.stderr)
    print("这将避免手动设置系统环境变量的麻烦，并能直接启动到API服务。", file=sys.stderr)
    print("本脚本将引导您完成代理设置，然后【后台独立】启动 Camoufox 主程序 (launch_camoufox.py)，", file=sys.stderr)
    print("主程序将以无头模式 (--headless) 运行。", file=sys.stderr)
    print("\n⚠️ 重要前提：", file=sys.stderr)
    print("   请确保您已经在 'auth_profiles/active/' 目录下", file=sys.stderr)
    print("   放置了有效的认证 JSON 文件 (例如 'Account.json')。", file=sys.stderr)
    print("   否则，无头模式将无法启动。", file=sys.stderr)
    print("-------------------------------------------------", file=sys.stderr)

    # --- 端口检查与处理 ---
    print(f"\n--- 初始检查: 目标端口 ({TARGET_PORT}) 是否被占用 ---", file=sys.stderr)
    max_retries = 3
    port_cleared_successfully = False

    for attempt in range(max_retries):
        if is_port_in_use(TARGET_PORT):
            print(f"  ❌ 端口 {TARGET_PORT} 当前被占用 (尝试 {attempt + 1}/{max_retries})。正在尝试识别占用进程...", file=sys.stderr)
            pids = find_pids_on_port(TARGET_PORT)
            if pids:
                print(f"     识别到以下进程 PID 可能占用了端口 {TARGET_PORT}: {pids}", file=sys.stderr)
                user_choice = ""
                # 确保 input 提示也到 stderr，但 input 本身从 stdin 读取
                sys.stderr.flush() # 确保提示先显示
                user_choice = input(f"     是否尝试自动终止这些进程？ (y/n, 输入 n 跳过并退出脚本): ").strip().lower()

                if user_choice == 'y':
                    print("     正在尝试终止进程...", file=sys.stderr)
                    all_killed_successfully_this_round = all(kill_process(pid) for pid in pids)

                    if all_killed_successfully_this_round: # 即使部分失败，也可能端口已释放
                        print("     终止尝试完成。等待 2 秒后重新检查端口...", file=sys.stderr)
                        time.sleep(2)
                        if not is_port_in_use(TARGET_PORT):
                            print(f"     ✅ 端口 {TARGET_PORT} 现在可用。", file=sys.stderr)
                            port_cleared_successfully = True
                            break  # 端口已清空，跳出重试循环
                        else:
                            print(f"     ❌ 尝试终止后，端口 {TARGET_PORT} 仍然被占用。", file=sys.stderr)
                    else: # 如果 all() 返回 false，说明至少有一个 kill_process 返回 false
                        print(f"     ⚠️ 未能成功终止所有识别到的进程。请检查上述日志。", file=sys.stderr)
                        # 即使这样，也检查一下端口，万一占用的进程自己退出了
                        if not is_port_in_use(TARGET_PORT):
                            print(f"     但端口 {TARGET_PORT} 现在可用了 (可能相关进程已自行退出)。", file=sys.stderr)
                            port_cleared_successfully = True
                            break
                        else:
                             print(f"     端口 {TARGET_PORT} 仍然被占用。", file=sys.stderr)


                    if attempt < max_retries - 1 and not port_cleared_successfully:
                        print(f"     将在下一轮重试 ({attempt + 2}/{max_retries})...", file=sys.stderr)
                        time.sleep(1)
                    elif not port_cleared_successfully:
                        print(f"     已达到最大重试次数。请手动检查并停止占用端口 {TARGET_PORT} 的程序后重试。", file=sys.stderr)
                        sys.exit(1)
                else:  # 用户选择 'n'
                    print(f"  用户选择不自动终止。请手动清理端口 {TARGET_PORT} 后重试。", file=sys.stderr)
                    sys.exit(1)
            else: # 未找到PID
                print(f"  未能自动识别占用端口 {TARGET_PORT} 的进程。", file=sys.stderr)
                print(f"  请手动检查端口 {TARGET_PORT} 是否被其他程序 (如之前未关闭的 server.py) 占用，并停止它。", file=sys.stderr)
                sys.exit(1)
        else: # 端口一开始就可用
            print(f"  ✅ 端口 {TARGET_PORT} 当前可用。", file=sys.stderr)
            port_cleared_successfully = True
            break

    if not port_cleared_successfully:
        print(f"错误：未能确保端口 {TARGET_PORT} 可用。退出。", file=sys.stderr)
        sys.exit(1)
    print("-------------------------------------------------\n", file=sys.stderr)

    # 1. 询问是否启用代理
    print("\n--- 步骤 1: 代理设置 (可选) ---", file=sys.stderr)
    default_proxy = "http://127.0.0.1:7890" # 常见的本地代理端口
    proxy_address = None
    enable_proxy = False
    while True:
        sys.stderr.flush()
        use_proxy_input = input(f"是否为无头模式启用 HTTP/HTTPS 代理？ (y/n, 默认为 n): ").strip().lower()
        if use_proxy_input == 'y':
            enable_proxy = True
            break
        elif use_proxy_input in ('n', ''):
            enable_proxy = False
            break
        else:
            print("输入无效，请输入 'y' 或 'n'。", file=sys.stderr)

    # 2. 如果启用代理，询问地址
    if enable_proxy:
        while True:
            sys.stderr.flush()
            use_default = input(f"是否使用默认代理地址 ({default_proxy})？ (y/n, 默认为 y): ").strip().lower()
            if use_default in ('y', ''):
                proxy_address = default_proxy
                break
            elif use_default == 'n':
                sys.stderr.flush()
                custom_proxy = input("请输入自定义代理地址 (例如 http://主机名:端口): ").strip()
                if custom_proxy:
                    if "://" in custom_proxy and ":" in custom_proxy.split("://")[1]:
                        proxy_address = custom_proxy
                        break
                    else:
                        print("地址格式似乎无效，请确保包含协议 (如 http:// 或 https://) 和端口号。", file=sys.stderr)
                else:
                    print("代理地址不能为空。", file=sys.stderr) # 提示到 stderr
            else:
                print("输入无效，请输入 'y' 或 'n'。", file=sys.stderr)
        print(f"代理已启用。将使用地址: {proxy_address}", file=sys.stderr)
    else:
        print("代理已禁用。", file=sys.stderr)
    print("--------------------------", file=sys.stderr)

    # 3. 为子进程准备环境
    print("\n--- 步骤 2: 准备环境 ---", file=sys.stderr)
    child_env = copy.deepcopy(os.environ)
    child_env['PYTHONIOENCODING'] = 'utf-8' # 确保子进程输出编码正确
    if proxy_address:
        child_env["HTTP_PROXY"] = proxy_address
        child_env["HTTPS_PROXY"] = proxy_address
        print(f"  已为新进程设置 HTTP_PROXY 和 HTTPS_PROXY 为: {proxy_address}", file=sys.stderr)
    else:
        child_env.pop("HTTP_PROXY", None)
        child_env.pop("HTTPS_PROXY", None)
        print("  已确保新进程未设置 HTTP_PROXY 和 HTTPS_PROXY。", file=sys.stderr)
    print("--------------------------", file=sys.stderr)

    # 4. 在新进程中以 headless 模式启动 launch_camoufox.py
    print("\n--- 步骤 3: 启动主程序 (无头模式, 后台运行) ---", file=sys.stderr)
    script_filename = "launch_camoufox.py"
    python_executable_to_use = sys.executable # 默认使用当前 Python 解释器

    # --- 构造 launch_camoufox.py 的绝对路径 ---
    # __file__ 是当前脚本 (start.py) 的路径
    current_script_dir = os.path.dirname(os.path.abspath(__file__))
    script_to_run_abs_path = os.path.join(current_script_dir, script_filename)

    if not os.path.exists(script_to_run_abs_path):
        print(f"错误：无法在路径 '{script_to_run_abs_path}' 找到主程序脚本 '{script_filename}'。请确保它与 start.py 在同一目录。", file=sys.stderr)
        sys.exit(1)

    if sys.platform == "win32":
        # 尝试找到 pythonw.exe 以实现无窗口后台运行
        _pythonw_candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.exists(_pythonw_candidate):
            print(f"  提示: 在 Windows 上，将使用 '{_pythonw_candidate}' 启动子进程以确保无窗口。", file=sys.stderr)
            python_executable_to_use = _pythonw_candidate
        else:
            print(f"  警告: 未在 '{os.path.dirname(sys.executable)}' 目录下找到 pythonw.exe。", file=sys.stderr)
            print(f"        将继续使用默认的 '{sys.executable}'。如果子进程仍然显示窗口，这可能是原因之一。", file=sys.stderr)

    # --- 构造启动命令，强制加入 --headless ---
    cmd_to_launch = [python_executable_to_use, script_to_run_abs_path, "--headless"]
    print(f"  准备在新的独立后台进程中启动: {script_to_run_abs_path} ...", file=sys.stderr)
    print(f"  执行命令: {' '.join(cmd_to_launch)}", file=sys.stderr)

    # --- 配置 Popen 参数以实现后台运行 ---
    popen_kwargs_for_background = {"env": child_env}
    if sys.platform == "win32":
        # DETACHED_PROCESS: 使子进程在其自己的进程组中运行，独立于父进程的控制台。
        # CREATE_NO_WINDOW: 如果启动的是控制台应用程序 (如 python.exe)，则不为其创建新的控制台窗口。
        # pythonw.exe 通常本身就不会创建窗口。
        popen_kwargs_for_background["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        # 对于 Windows，如果想完全确保句柄不被继承（尽管 Popen 默认行为通常可以），可以考虑：
        # popen_kwargs_for_background["close_fds"] = True # 但这在 Windows 上不常用，且可能与某些重定向冲突
        # 将子进程的 stdin, stdout, stderr 重定向到 DEVNULL，使其完全静默（日志由 launch_camoufox.py 内部处理）
        popen_kwargs_for_background["stdin"] = subprocess.DEVNULL
        popen_kwargs_for_background["stdout"] = subprocess.DEVNULL
        popen_kwargs_for_background["stderr"] = subprocess.DEVNULL
    else: # Unix-like 系统
        popen_kwargs_for_background["start_new_session"] = True # 使子进程成为新会话的领导者，从而与当前终端分离
        popen_kwargs_for_background["stdin"] = subprocess.DEVNULL
        popen_kwargs_for_background["stdout"] = subprocess.DEVNULL
        popen_kwargs_for_background["stderr"] = subprocess.DEVNULL

    try:
        subprocess.Popen(cmd_to_launch, **popen_kwargs_for_background)
        print(f"\n✅ {script_filename} 已成功在后台独立进程中以 --headless 模式启动。", file=sys.stderr)
        print("   现在可以安全地关闭此启动器窗口了。", file=sys.stderr)
        print(f"   主程序将在后台继续运行。请查看其日志文件 (通常在 'logs/launch_app.log') 以确认运行状态。", file=sys.stderr)
        print("--------------------------", file=sys.stderr)
    except FileNotFoundError:
        print(f"❌ 错误：无法找到 Python 解释器 ({python_executable_to_use}) 或脚本路径计算错误。", file=sys.stderr)
        print(f"   尝试启动的脚本路径: {script_to_run_abs_path}", file=sys.stderr)
        print(f"   请确保 '{script_filename}' 与 start.py 在同一个目录下，并且 Python 环境正常。", file=sys.stderr)
    except Exception as e:
        print(f"❌ 启动脚本时发生意外错误: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr) # 打印详细错误堆栈到 stderr
    
    print("启动器任务完成，将在几秒后自动退出...", file=sys.stderr)
    time.sleep(3)

if __name__ == "__main__":
    main()