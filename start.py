import os
import subprocess
import sys
import copy
import socket
import platform
import time
from typing import List  # 明确导入 List

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
            s.bind(("127.0.0.1", port))
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
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate(timeout=5)  # 设置超时
            if process.returncode == 0 and stdout:
                pids = [int(pid) for pid in stdout.strip().split('\n') if pid.isdigit()]
            elif process.returncode != 0 and "command not found" in stderr:
                print(f"错误：'lsof' 命令未找到。请确保它已安装并位于 PATH 中。", file=sys.stderr)
            # lsof 在找不到监听进程时返回 1，这不是错误
            elif process.returncode != 0 and process.returncode != 1:
                print(f"警告：执行 lsof 命令失败，返回码 {process.returncode}。错误: {stderr.strip()}", file=sys.stderr)

        elif system == "Windows":
            command = f'netstat -ano -p TCP | findstr "LISTENING" | findstr ":{port} "'  # 更精确的 findstr
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate(timeout=10)  # netstat 可能较慢
            if process.returncode == 0 and stdout:
                for line in stdout.strip().split('\n'):
                    parts = line.split()
                    if len(parts) >= 4 and parts[0].upper() == 'TCP':
                        try:
                            # 端口号在本地地址之后，PID 在最后
                            local_address = parts[1]
                            if f":{port}" in local_address:
                                pid_str = parts[-1]
                                if pid_str.isdigit():
                                    pids.append(int(pid_str))
                        except (IndexError, ValueError):
                            continue  # 解析行失败，跳过
                pids = list(set(pids))
            # findstr 找不到匹配项时返回 1，这不是错误
            elif process.returncode != 0 and process.returncode != 1:
                print(f"警告：执行 netstat/findstr 命令失败，返回码 {process.returncode}。错误: {stderr.strip()}", file=sys.stderr)

        else:
            print(f"警告：不支持的操作系统 '{system}' 用于查找进程。", file=sys.stderr)

    except FileNotFoundError:
        cmd_name = command.split()[0] if command else ""
        print(f"错误：命令 '{cmd_name}' 未找到。请确保相关工具已安装并位于 PATH 中。", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"错误：执行命令 '{command}' 超时。", file=sys.stderr)
    except Exception as e:
        print(f"错误：查找占用端口 {port} 的进程时出错：{e}", file=sys.stderr)

    return pids

def kill_process(pid: int) -> bool:
    """尝试终止指定 PID 的进程 (跨平台)"""
    system = platform.system()
    command = ""
    success = False
    try:
        print(f"  尝试终止进程 PID: {pid}...", end="", flush=True)
        if system == "Linux" or system == "Darwin":
            # 先尝试 SIGTERM (优雅退出)
            command = f"kill {pid}"
            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=False, timeout=3)
            if result.returncode == 0:
                print(" [SIGTERM 发送成功]", flush=True)
                success = True
            else:
                # 如果 SIGTERM 失败，可能是进程不存在或权限不足
                error_output = result.stderr.strip() or result.stdout.strip()
                print(f" [SIGTERM 失败: {error_output}]", flush=True)
                # 不再自动尝试 SIGKILL，留给用户判断

        elif system == "Windows":
            # Windows: 使用 taskkill /F (强制终止)
            command = f"taskkill /PID {pid} /T /F"
            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=False, timeout=5)
            output = result.stdout.strip()
            error_output = result.stderr.strip()
            # taskkill 成功信息可能因语言而异，检查错误输出和返回码更可靠
            if result.returncode == 0 and ("成功" in output or "SUCCESS" in output.upper() or not error_output):
                # 有些情况即使成功 returncode 也可能非0但没错误输出
                print(" [taskkill /T /F 成功]", flush=True)
                success = True
            # 常见的错误是"找不到进程"
            elif "找不到进程" in error_output or "process not found" in error_output.lower():
                print(f" [taskkill 失败: 进程 {pid} 可能已退出]", flush=True)
                success = True  # 认为目标已达成
            else:
                full_error = (error_output + " " + output).strip()
                print(f" [taskkill /T /F 失败: {full_error}]", flush=True)
        else:
            print(f" [不支持的操作系统: {system}]", flush=True)

    except FileNotFoundError:
        cmd_name = command.split()[0] if command else ""
        print(f" [错误：命令 '{cmd_name}' 未找到]", flush=True)
    except subprocess.TimeoutExpired:
        print(f" [错误：终止进程 {pid} 的命令执行超时]", flush=True)
    except Exception as e:
        print(f" [错误：终止进程 {pid} 时发生未知错误: {e}]", flush=True)

    return success
# -------------------------------------

def main():
    """
    检查端口，提示用户进行代理设置，然后以 --headless 模式
    在新的独立进程中启动 launch_camoufox.py。
    重要提示：此模式需要 'auth_profiles/active/' 目录下有有效的认证文件。
    """
    # --- 功能介绍 ---
    print("\n"*50)
    print("=================================================")
    print("== Camoufox 代理配置启动器 (start.py) ==")
    print("=================================================")
    print("脚本提供了一个用户友好的交互界面，")
    print("让你在启动主程序 (launch_camoufox.py) 之前就能方便地设置 HTTP 和 HTTPS 代理。")
    print("这避免了手动设置系统环境变量的麻烦，并能直接启动到API服务。")
    print("本脚本将引导您完成代理设置，")
    print("然后【后台】独立启动 Camoufox 主程序，")
    print("主程序(launch_camoufox.py) 将以无头模式 (--headless) 运行。")
    print("\n⚠️ 重要前提：")
    print("   请确保您已经在 'auth_profiles/active/' 目录下")
    print("   放置了有效的认证 JSON 文件 (例如 'Account.json')。")
    print("   否则，无头模式将无法启动。")
    print("-------------------------------------------------")

    # --- 新增：端口检查与处理 ---
    print(f"\n--- 初始检查: 目标端口 ({TARGET_PORT}) 是否被占用 ---")
    max_retries = 3  # 最多尝试自动清理3次
    retry_count = 0
    port_cleared = False

    while retry_count < max_retries:
        if is_port_in_use(TARGET_PORT):
            print(f"  ❌ 端口 {TARGET_PORT} 当前被占用 (尝试 {retry_count + 1}/{max_retries})。正在尝试识别占用进程...")
            pids = find_pids_on_port(TARGET_PORT)
            if pids:
                print(f"     识别到以下进程 PID 可能占用了端口 {TARGET_PORT}: {pids}")
                user_choice = ""
                while user_choice not in ['y', 'n']:
                    user_choice = input(f"     是否尝试自动终止这些进程？ (y/n, 输入 n 跳过并退出脚本): ").strip().lower()

                if user_choice == 'y':
                    print("     正在尝试终止进程...")
                    killed_any = False
                    all_killed_successfully = True  # 假设全部成功
                    for pid in pids:
                        if kill_process(pid):
                            killed_any = True
                        else:
                            all_killed_successfully = False  # 记录任何一次失败

                    if killed_any:
                        print("     终止尝试完成。等待 1.5 秒后重新检查端口...")
                        time.sleep(1.5)
                        if not is_port_in_use(TARGET_PORT):
                            print(f"     ✅ 端口 {TARGET_PORT} 现在可用。")
                            port_cleared = True
                            break  # 端口已清空，跳出重试循环
                        else:
                            print(f"     ❌ 尝试终止后，端口 {TARGET_PORT} 仍然被占用。")
                            if not all_killed_successfully:
                                print(f"     (提示: 可能部分进程终止失败，需要管理员权限或进程已退出)")
                            retry_count += 1
                            if retry_count < max_retries:
                                print(f"     将在下一轮重试 ({retry_count + 1}/{max_retries})...")
                                time.sleep(1)  # 重试前稍作等待
                            else:
                                print(f"     已达到最大重试次数。请手动检查并停止占用端口 {TARGET_PORT} 的程序后重试。")
                                sys.exit(1)  # 退出脚本
                    else:
                        print("     未能成功终止任何识别到的进程。")
                        print(f"     请手动检查并停止占用端口 {TARGET_PORT} 的程序后重试。")
                        print(f"     (提示: 可能需要管理员权限来终止这些进程)")
                        sys.exit(1)  # 退出脚本
                else:  # 用户选择 'n'
                    print(f"  用户选择不自动终止。请手动清理端口 {TARGET_PORT} 后重试。")
                    sys.exit(1)  # 退出脚本
            else:
                print(f"  未能自动识别占用端口 {TARGET_PORT} 的进程。")
                print(f"  请手动检查端口 {TARGET_PORT} 是否被其他程序 (如之前未关闭的 server.py) 占用，并停止它。")
                sys.exit(1)  # 退出脚本
        else:
            print(f"  ✅ 端口 {TARGET_PORT} 当前可用。")
            port_cleared = True
            break  # 端口一开始就可用，跳出循环

    if not port_cleared:
        # 理论上不应该执行到这里，但在循环结束后再次确认
        print(f"错误：未能确保端口 {TARGET_PORT} 可用。退出。")
        sys.exit(1)

    print("-------------------------------------------------\n")

    # 1. 询问是否启用代理
    print("\n--- 步骤 1: 代理设置 (可选) ---")
    default_proxy = "http://127.0.0.1:7890"
    proxy_address = None
    enable_proxy = False
    while True:
        use_proxy_input = input(f"是否为无头模式启用 HTTP/HTTPS 代理？ (y/n, 默认为 n): ").strip().lower()
        if use_proxy_input == 'y':
            enable_proxy = True
            break
        elif use_proxy_input in ('n', ''):
            enable_proxy = False
            break
        else:
            print("输入无效，请输入 'y' 或 'n'。")

    # 2. 如果启用代理，询问地址
    if enable_proxy:
        while True:
            use_default = input(f"是否使用默认代理地址 ({default_proxy})？ (y/n, 默认为 y): ").strip().lower()
            if use_default in ('y', ''):
                proxy_address = default_proxy
                break
            elif use_default == 'n':
                custom_proxy = input("请输入自定义代理地址 (例如 http://主机名:端口): ").strip()
                if custom_proxy:
                    # 简单的验证，确保不是空的，并且至少看起来像个URL（但不严格）
                    if "://" in custom_proxy and ":" in custom_proxy.split("://")[1]:
                         proxy_address = custom_proxy
                         break
                    else:
                         print("地址格式似乎无效，请确保包含协议 (如 http:// 或 https://) 和端口号。")
                else:
                    print("代理地址不能为空。")
            else:
                print("输入无效，请输入 'y' 或 'n'。")
        print(f"代理已启用。将使用地址: {proxy_address}")
    else:
        print("代理已禁用。")
    print("--------------------------")

    # 3. 为子进程准备环境
    print("\n--- 步骤 2: 准备环境 ---")
    child_env = copy.deepcopy(os.environ)
    if proxy_address:
        child_env["HTTP_PROXY"] = proxy_address
        child_env["HTTPS_PROXY"] = proxy_address
        print(f"已为新进程设置 HTTP_PROXY 和 HTTPS_PROXY 为: {proxy_address}")
    else:
        # 确保代理环境变量在子环境中不存在或为空
        child_env.pop("HTTP_PROXY", None)
        child_env.pop("HTTPS_PROXY", None)
        print("已确保新进程未设置 HTTP_PROXY 和 HTTPS_PROXY。")
    print("--------------------------")


    # 4. 在新进程中以 headless 模式启动 launch_camoufox.py
    print("\n--- 步骤 3: 启动主程序 (无头模式) ---")
    script_filename = "launch_camoufox.py"
    python_executable = sys.executable # 使用运行此脚本的同一个 Python 解释器

    # --- 构造 script_to_run 的绝对路径 ---
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_to_run_path = os.path.join(script_dir, script_filename)
    # -----------------------------------------

    # --- 构造启动命令，强制加入 --headless ---
    cmd = [python_executable, script_to_run_path, "--headless"]
    # --------------------------------------

    print(f"准备在新的独立进程中以无头模式启动: {script_to_run_path} ...")
    print(f"执行命令: {' '.join(cmd)}") # 打印将要执行的命令

    try:
        popen_kwargs = {
            "env": child_env,
            # --- 修改：将 stdout/stderr 重定向到 DEVNULL 实现彻底分离 ---
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL,
            # ---------------------------------------------------------
        }
        if sys.platform == "win32":
            # --- 修改：使用 DETACHED_PROCESS | CREATE_NO_WINDOW 实现后台运行 ---
            popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
            # ----------------------------------------------------------------
        else:
            # --- 保留 start_new_session=True ---
            popen_kwargs["start_new_session"] = True
            # -----------------------------------

        # --- 启动子进程 ---
        subprocess.Popen(cmd, **popen_kwargs)
        # -------------------

        print(f"\n✅ {script_filename} 已成功在后台独立进程中以 --headless 模式启动。")
        print("   现在可以安全地关闭此启动器窗口了。")
        print("   主程序将在后台继续运行（需要认证文件有效）。")
        print("   你可以通过查看系统进程或相关日志来确认其运行状态。")
        print("--------------------------")

    except FileNotFoundError:
        # 这里现在应该不太可能发生，除非 python_executable 有问题或 script_to_run_path 计算错误
        print(f"❌ 错误：无法找到 Python 解释器 ({python_executable}) 或脚本路径计算错误。")
        print(f"   尝试启动的脚本路径: {script_to_run_path}")
        print(f"   请确保 '{script_filename}' 与 start.py 在同一个目录下，并且 Python 环境正常。")
        print("--------------------------")
    except Exception as e:
        print(f"❌ 启动脚本时发生意外错误: {e}")
        # 打印更详细的错误信息可能有助于调试
        import traceback
        traceback.print_exc()
        print("--------------------------")

    # 可选：如果希望启动器窗口停留一会，可以取消下面这行的注释
    input("按回车键退出此启动器脚本...")

if __name__ == "__main__":
    main()