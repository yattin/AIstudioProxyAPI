#!/usr/bin/env python3
import re
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, scrolledtext
import subprocess
import os
import sys
import platform
import threading
import time
import socket
from typing import List, Dict, Any, Optional

# --- Configuration & Globals ---
PYTHON_EXECUTABLE = sys.executable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAUNCH_CAMOUFOX_PY = os.path.join(SCRIPT_DIR, "launch_camoufox.py")
SERVER_PY_FILENAME = "server.py" # For context

AUTH_PROFILES_DIR = os.path.join(SCRIPT_DIR, "auth_profiles") # 确保这些目录存在
ACTIVE_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, "active")
SAVED_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, "saved")

DEFAULT_FASTAPI_PORT = 2048

managed_process_info: Dict[str, Any] = {
    "popen": None,
    "service_name_key": None,
    "monitor_thread": None,
    "stdout_thread": None,
    "stderr_thread": None,
    "output_area": None,
    "fully_detached": False # 新增：标记进程是否完全独立
}

# --- Internationalization (i18n) ---
LANG_TEXTS = {
    "title": {"zh": "AI Studio Proxy API Launcher GUI", "en": "AI Studio Proxy API Launcher GUI"},
    "status_idle": {"zh": "空闲，请选择操作。", "en": "Idle. Select an action."},
    "port_section_label": {"zh": "服务端口配置", "en": "Service Port Configuration"},
    "port_input_description_lbl": {"zh": "提示: 下方所有启动选项均会使用此端口号。", "en": "Note: All launch options below will use this port number."},
    "port_label": {"zh": "服务端口 (FastAPI):", "en": "Service Port (FastAPI):"},
    "query_pids_btn": {"zh": "查询端口进程", "en": "Query Port Processes"},
    "stop_selected_pid_btn": {"zh": "停止选中进程", "en": "Stop Selected Process"},
    "pids_on_port_label": {"zh": "端口占用情况 (PID - 名称):", "en": "Processes on Port (PID - Name):"}, # Static version for initialization
    "pids_on_port_label_dynamic": {"zh": "端口 {port} 占用情况 (PID - 名称):", "en": "Processes on Port {port} (PID - Name):"}, # Dynamic version
    "no_pids_found": {"zh": "未找到占用该端口的进程。", "en": "No processes found on this port."},
    "launch_options_label": {"zh": "启动选项", "en": "Launch Options"},
    "launch_options_note_revised": {"zh": "提示：有头模式用于调试和认证，会打开浏览器和新控制台。\n无头模式在后台独立运行 (关闭GUI后服务仍运行)，需预先认证。",
                                    "en": "Tip: Headed mode is for debug and auth (opens browser & console).\nHeadless mode runs independently in background (service persists after GUI close), requires pre-auth."},
    "launch_headed_interactive_btn": {"zh": "启动有头模式", "en": "Launch Headed Mode"},
    "launch_headless_independent_btn": {"zh": "启动无头模式", "en": "Launch Headless Mode"},
    "stop_gui_service_btn": {"zh": "停止当前GUI管理的服务", "en": "Stop Current GUI-Managed Service"},
    "status_label": {"zh": "状态", "en": "Status"},
    "output_label": {"zh": "输出日志", "en": "Output Log"},
    "menu_language_fixed": {"zh": "Language", "en": "Language"},
    "menu_lang_zh_option": {"zh": "中文 (Chinese)", "en": "中文 (Chinese)"},
    "menu_lang_en_option": {"zh": "英文 (English)", "en": "英文 (English)"},
    "confirm_quit_title": {"zh": "确认退出", "en": "Confirm Quit"},
    "confirm_quit_message": {"zh": "服务 '{service_name}' 仍在运行。是否停止并退出?", "en": "Service '{service_name}' is still running. Stop it and quit?"},
    "confirm_quit_message_independent": {"zh": "独立后台服务 '{service_name}' 可能仍在运行。直接退出GUI吗 (服务将继续运行)?", "en": "Independent background service '{service_name}' may still be running. Quit GUI (service will continue to run)?"},
    "error_title": {"zh": "错误", "en": "Error"},
    "info_title": {"zh": "信息", "en": "Info"},
    "warning_title": {"zh": "警告", "en": "Warning"},
    "service_already_running": {"zh": "服务 ({service_name}) 已在运行。", "en": "A service ({service_name}) is already running."},
    "proxy_config_title": {"zh": "代理配置", "en": "Proxy Configuration"},
    "proxy_config_message_generic": {"zh": "是否为此启动启用 HTTP/HTTPS 代理?", "en": "Enable HTTP/HTTPS proxy for this launch?"},
    "proxy_address_title": {"zh": "代理地址", "en": "Proxy Address"},
    "proxy_address_prompt": {"zh": "输入代理地址 (例如 http://host:port)\n默认: {default_proxy}", "en": "Enter proxy address (e.g., http://host:port)\nDefault: {default_proxy}"},
    "proxy_configured_status": {"zh": "代理已配置: {proxy_addr}", "en": "Proxy configured: {proxy_addr}"},
    "proxy_skip_status": {"zh": "用户跳过代理设置。", "en": "Proxy setup skipped by user."},
    "script_not_found_error_msgbox": {"zh": "启动失败: 未找到 Python 执行文件或脚本。\n命令: {cmd}", "en": "Failed to start: Python executable or script not found.\nCommand: {cmd}"},
    "startup_error_title": {"zh": "启动错误", "en": "Startup Error"},
    "startup_script_not_found_msgbox": {"zh": "必需的脚本 '{script}' 在当前目录未找到。\n请将此GUI启动器与 launch_camoufox.py 和 server.py 放在同一目录。", "en": "Required script '{script}' not found in the current directory.\nPlace this GUI launcher in the same directory as launch_camoufox.py and server.py."},
    "service_starting_status": {"zh": "{service_name} 启动中... PID: {pid}", "en": "{service_name} starting... PID: {pid}"},
    "service_stopped_gracefully_status": {"zh": "{service_name} 已平稳停止。", "en": "{service_name} stopped gracefully."},
    "service_stopped_exit_code_status": {"zh": "{service_name} 已停止。退出码: {code}", "en": "{service_name} stopped. Exit code: {code}"},
    "service_stop_fail_status": {"zh": "{service_name} (PID: {pid}) 未能平稳终止。正在强制停止...", "en": "{service_name} (PID: {pid}) did not terminate gracefully. Forcing kill..."},
    "service_killed_status": {"zh": "{service_name} (PID: {pid}) 已被强制停止。", "en": "{service_name} (PID: {pid}) killed."},
    "error_stopping_service_msgbox": {"zh": "停止 {service_name} (PID: {pid}) 时出错: {e}", "en": "Error stopping {service_name} (PID: {pid}): {e}"},
    "no_service_running_status": {"zh": "当前没有GUI管理的服务在运行。", "en": "No GUI-managed service is currently running."},
    "stopping_initiated_status": {"zh": "{service_name} (PID: {pid}) 停止已启动。最终状态待定。", "en": "{service_name} (PID: {pid}) stopping initiated. Final status pending."},
    "service_name_headed_interactive": {"zh": "有头交互服务", "en": "Headed Interactive Service"},
    "service_name_headless_independent": {"zh": "独立无头服务", "en": "Independent Headless Service"},
    "status_headed_launch": {"zh": "有头模式：启动中，请关注新控制台的提示...", "en": "Headed Mode: Launching, check new console for prompts..."},
    "status_headless_independent_launch": {"zh": "独立无头服务：启动中...此服务将在GUI关闭后继续运行。", "en": "Independent Headless Service: Launching... This service will persist after GUI closes."},
    "info_service_is_independent": {"zh": "当前服务为独立后台进程，关闭GUI不会停止它。请使用系统工具或端口管理手动停止此服务。", "en": "The current service is an independent background process. Closing the GUI will not stop it. Please manage this service manually using system tools or port management."},
    "warn_cannot_stop_independent_service": {"zh": "当前运行的是独立后台服务，无法通过此按钮停止。请手动管理。", "en": "The currently running service is independent and cannot be stopped by this button. Please manage it manually."},
    "enter_valid_port_warn": {"zh": "请输入有效的端口号 (1024-65535)。", "en": "Please enter a valid port number (1024-65535)."},
    "pid_list_empty_for_stop_warn": {"zh": "进程列表为空或未选择进程。", "en": "PID list is empty or no process selected."},
    "confirm_stop_pid_title": {"zh": "确认停止进程", "en": "Confirm Stop Process"},
    "confirm_stop_pid_message": {"zh": "确定要尝试停止 PID {pid} ({name}) 吗?", "en": "Are you sure you want to attempt to stop PID {pid} ({name})?"},
    "status_error_starting": {"zh": "启动 {service_name} 失败。", "en": "Error starting {service_name}."},
    "status_script_not_found": {"zh": "错误: 未找到 {service_name} 的可执行文件/脚本。", "en": "Error: Executable/script not found for {service_name}."},
    "error_getting_process_name": {"zh": "获取 PID {pid} 的进程名失败。", "en": "Failed to get process name for PID {pid}."},
    "pid_info_format": {"zh": "PID: {pid} (端口: {port}) - 名称: {name}", "en": "PID: {pid} (Port: {port}) - Name: {name}"},
    "status_stopping_service": {"zh": "正在停止 {service_name} (PID: {pid})...", "en": "Stopping {service_name} (PID: {pid})..."},
    "error_title_invalid_selection": {"zh": "无效的选择格式: {selection}", "en": "Invalid selection format: {selection}"},
    "error_parsing_pid": {"zh": "无法从 '{selection}' 解析PID。", "en": "Could not parse PID from '{selection}'."},
    "terminate_request_sent": {"zh": "终止请求已发送。", "en": "Termination request sent."},
    "terminate_attempt_failed": {"zh": "尝试终止 PID {pid} ({name}) 可能失败。", "en": "Attempt to terminate PID {pid} ({name}) may have failed."},
    "unknown_process_name_placeholder": {"zh": "未知进程名", "en": "Unknown Process Name"},
    "kill_custom_pid_label": {"zh": "或输入PID终止:", "en": "Or Enter PID to Kill:"},
    "kill_custom_pid_btn": {"zh": "终止指定PID", "en": "Kill Specified PID"},
    "pid_input_empty_warn": {"zh": "请输入要终止的PID。", "en": "Please enter a PID to kill."},
    "pid_input_invalid_warn": {"zh": "输入的PID无效，请输入纯数字。", "en": "Invalid PID entered. Please enter numbers only."},
    "confirm_kill_custom_pid_title": {"zh": "确认终止PID", "en": "Confirm Kill PID"}
}
current_language = 'zh'
root_widget: Optional[tk.Tk] = None
process_status_text_var: Optional[tk.StringVar] = None
port_entry_var: Optional[tk.StringVar] = None
pid_listbox_widget: Optional[tk.Listbox] = None
custom_pid_entry_var: Optional[tk.StringVar] = None
widgets_to_translate: List[Dict[str, Any]] = []

def is_port_in_use(port: int) -> bool: # Simplified, launch_camoufox.py handles detailed checks
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port)) # Check against 127.0.0.1 for GUI context
            return False
        except OSError: return True
        except Exception: return True # Broad exception for safety

def get_process_name_by_pid(pid: int) -> str:
    system = platform.system()
    name = get_text("unknown_process_name_placeholder") # Default to i18n unknown
    cmd_args = []
    try:
        if system == "Windows":
            cmd_args = ["tasklist", "/NH", "/FO", "CSV", "/FI", f"PID eq {pid}"]
            process = subprocess.run(cmd_args, capture_output=True, text=True, check=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
            if process.stdout.strip():
                parts = process.stdout.strip().split('","')
                if len(parts) > 0: name = parts[0].strip('"')
        elif system == "Linux":
            cmd_args = ["ps", "-p", str(pid), "-o", "comm="]
            process = subprocess.run(cmd_args, capture_output=True, text=True, check=True, timeout=3)
            if process.stdout.strip(): name = process.stdout.strip()
        elif system == "Darwin":  # macOS 系统
            # 首先获取命令名
            cmd_args = ["ps", "-p", str(pid), "-o", "comm="]
            process = subprocess.run(cmd_args, capture_output=True, text=True, check=True, timeout=3)
            raw_path = process.stdout.strip() if process.stdout.strip() else ""
            
            # 然后获取完整进程命令行
            cmd_args = ["ps", "-p", str(pid), "-o", "command="]
            process = subprocess.run(cmd_args, capture_output=True, text=True, check=True, timeout=3)
            full_command = process.stdout.strip() if process.stdout.strip() else ""
            
            if raw_path:
                # 提取路径中的文件名
                base_name = os.path.basename(raw_path)
                # 返回 "文件名 (完整路径)" 格式
                name = f"{base_name} ({raw_path})"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        # print(f"Error getting name for PID {pid} with '{' '.join(cmd_args)}': {e}", file=sys.stderr) # Less verbose
        pass
    except Exception:
        # print(f"Unexpected error getting name for PID {pid}: {e}", file=sys.stderr) # Less verbose
        pass
    return name

def find_processes_on_port(port: int) -> List[Dict[str, Any]]:
    process_details = []
    pids_only: List[int] = []
    system = platform.system()
    command_pid = ""
    try:
        if system == "Linux" or system == "Darwin":
            command_pid = f"lsof -ti tcp:{port} -sTCP:LISTEN"
            process = subprocess.Popen(command_pid, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, universal_newlines=True, close_fds=True)
            stdout_pid, _ = process.communicate(timeout=5)
            if process.returncode == 0 and stdout_pid:
                pids_only = [int(p) for p in stdout_pid.strip().splitlines() if p.isdigit()]
        elif system == "Windows":
            # Execute netstat without pre-filtering by "LISTENING" or port, for more robust Python parsing
            command_pid = 'netstat -ano -p TCP'
            process = subprocess.Popen(command_pid, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, universal_newlines=True, creationflags=subprocess.CREATE_NO_WINDOW)
            stdout_pid, _ = process.communicate(timeout=10)
            if process.returncode == 0 and stdout_pid:
                for line in stdout_pid.strip().splitlines():
                    parts = line.split()
                    # Expected "netstat -ano -p TCP" output columns on English systems:
                    # Proto  Local Address          Foreign Address        State           PID
                    # parts[0] parts[1]             parts[2]               parts[3]        parts[4]
                    # We need to be careful as "State" (parts[3]) might be localized.
                    # However, for TCP, a line representing a listening socket will typically have
                    # a local address like "0.0.0.0:port" or "[::]:port" and state "LISTENING".
                    if len(parts) >= 5 and parts[0].upper() == 'TCP':
                        # Check for LISTENING state explicitly. This assumes English output for "LISTENING".
                        # If `netstat` output is localized, this check might need adjustment or removal,
                        # relying solely on port matching for listening sockets (which is less ideal).
                        if parts[3].upper() != 'LISTENING':
                            continue

                        local_address_full = parts[1]
                        try:
                            last_colon_idx = local_address_full.rfind(':')
                            if last_colon_idx == -1:
                                continue # Malformed local address, no port found

                            extracted_port_str = local_address_full[last_colon_idx+1:]
                            
                            # Precise port matching
                            if extracted_port_str.isdigit() and int(extracted_port_str) == port:
                                pid_str = parts[4] # PID is at index 4 with "netstat -ano"
                                if pid_str.isdigit():
                                    pids_only.append(int(pid_str))
                        except (ValueError, IndexError):
                            # Skip lines that don't parse correctly (e.g., non-integer port/PID)
                            continue
                pids_only = list(set(pids_only)) # Remove duplicates
    except Exception:
        # print(f"Error finding PIDs on port {port} via '{command_pid}': {e}", file=sys.stderr) # Less verbose
        pass
    for pid_val in pids_only:
        name = get_process_name_by_pid(pid_val)
        process_details.append({"pid": pid_val, "name": name})
    return process_details

def kill_process_pid(pid: int) -> bool:
    system = platform.system()
    success = False
    try:
        # print(f"  Attempting to terminate process PID: {pid}...", end="", flush=True) # GUI status bar is better
        if system == "Linux" or system == "Darwin":
            subprocess.run(["kill", "-TERM", str(pid)], check=False, timeout=3, capture_output=True) # Capture to avoid console spam
            time.sleep(0.5)
            try:
                subprocess.run(["kill", "-0", str(pid)], check=True, timeout=1, capture_output=True)
                subprocess.run(["kill", "-KILL", str(pid)], check=True, timeout=3, capture_output=True)
                success = True
            except subprocess.CalledProcessError: # kill -0 failed, process is gone
                success = True
        elif system == "Windows":
            result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, check=False, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode == 0: success = True
            elif "not found" in (result.stderr or result.stdout or "").lower(): success = True # Already gone
    except Exception:
        # print(f" [Error killing process {pid}: {e}]", flush=True) # Less verbose
        pass
    return success

def get_text(key: str, **kwargs) -> str:
    try:
        text_template = LANG_TEXTS[key][current_language]
    except KeyError:
        text_template = LANG_TEXTS[key].get('en', f"<{key}_MISSING_{current_language}>")
    return text_template.format(**kwargs) if kwargs else text_template

def update_status_bar(message_key: str, **kwargs):
    message = get_text(message_key, **kwargs)
    if process_status_text_var:
        process_status_text_var.set(message)
    # print(f"GUI Status: {message}", flush=True) # Optionally keep for external logging
    if managed_process_info.get("output_area"):
        def _update_output(): # Closure to ensure correct message is used
            current_message = message # Capture message at time of call
            if managed_process_info.get("output_area") and root_widget:
                managed_process_info["output_area"].config(state=tk.NORMAL)
                managed_process_info["output_area"].insert(tk.END, f"[STATUS] {current_message}\n")
                managed_process_info["output_area"].see(tk.END)
                managed_process_info["output_area"].config(state=tk.DISABLED)
        if root_widget: root_widget.after_idle(_update_output)

def enqueue_stream_output(stream, stream_name_prefix):
    try:
        for line_bytes in iter(stream.readline, b''): # Read as bytes
            if not line_bytes: break
            line = line_bytes.decode(sys.stdout.encoding or 'utf-8', errors='replace') # Decode
            if managed_process_info.get("output_area") and root_widget:
                def _update_stream_output(line_to_insert): # Closure
                    current_line = line_to_insert # Capture line
                    if managed_process_info.get("output_area"):
                        managed_process_info["output_area"].config(state=tk.NORMAL)
                        managed_process_info["output_area"].insert(tk.END, current_line) # Already has prefix and newline from source
                        managed_process_info["output_area"].see(tk.END)
                        managed_process_info["output_area"].config(state=tk.DISABLED)
                root_widget.after_idle(_update_stream_output, f"[{stream_name_prefix}] {line}") # Pass formatted line
            else: print(f"[{stream_name_prefix}] {line.strip()}", flush=True)
    except ValueError: pass # Stream closed
    except Exception: # print(f"Error reading {stream_name_prefix}: {e}", file=sys.stderr) # Less verbose
        pass
    finally:
        if hasattr(stream, 'close') and not stream.closed: stream.close()

def is_service_running(): # Checks if a GUI-managed, non-detached service is running
    return managed_process_info.get("popen") and \
           managed_process_info["popen"].poll() is None and \
           not managed_process_info.get("fully_detached", False)

def is_any_service_known(): # Checks if Popen object exists, even if detached or finished
    return managed_process_info.get("popen") is not None

def monitor_process_thread_target():
    popen = managed_process_info.get("popen")
    service_name_key = managed_process_info.get("service_name_key")
    is_detached = managed_process_info.get("fully_detached", False)

    if not popen or not service_name_key: return

    stdout_thread = None; stderr_thread = None
    if popen.stdout: # Only if capture_output was true for this process
        stdout_thread = threading.Thread(target=enqueue_stream_output, args=(popen.stdout, "stdout"), daemon=True)
        managed_process_info["stdout_thread"] = stdout_thread
        stdout_thread.start()
    if popen.stderr: # Only if capture_output was true
        stderr_thread = threading.Thread(target=enqueue_stream_output, args=(popen.stderr, "stderr"), daemon=True)
        managed_process_info["stderr_thread"] = stderr_thread
        stderr_thread.start()
    
    popen.wait() # This will block until the process terminates
    exit_code = popen.returncode
    
    if stdout_thread and stdout_thread.is_alive(): stdout_thread.join(timeout=1)
    if stderr_thread and stderr_thread.is_alive(): stderr_thread.join(timeout=1)

    # Update status only if this specific monitored instance is still the active one
    # and it wasn't a fully detached process (whose lifecycle isn't tied to GUI status in the same way)
    if managed_process_info.get("service_name_key") == service_name_key:
        service_name = get_text(service_name_key)
        if not is_detached: # Only update status for non-detached services that finish
            if exit_code == 0: update_status_bar("service_stopped_gracefully_status", service_name=service_name)
            else: update_status_bar("service_stopped_exit_code_status", service_name=service_name, code=exit_code)
        
        # Clear Popen for this specific instance, regardless of detached status, as it has finished.
        # If it was detached, it finished on its own. If not, it was managed.
        managed_process_info["popen"] = None
        managed_process_info["service_name_key"] = None
        managed_process_info["fully_detached"] = False # Reset flag

def get_current_port_from_gui() -> int:
    try:
        port_str = port_entry_var.get()
        if not port_str: messagebox.showwarning(get_text("warning_title"), get_text("enter_valid_port_warn")); return DEFAULT_FASTAPI_PORT
        port = int(port_str)
        if not (1024 <= port <= 65535): raise ValueError("Port out of range")
        return port
    except ValueError:
        messagebox.showwarning(get_text("warning_title"), get_text("enter_valid_port_warn"))
        port_entry_var.set(str(DEFAULT_FASTAPI_PORT))
        return DEFAULT_FASTAPI_PORT

def _configure_proxy_env_vars() -> Dict[str, str]:
    proxy_env = {}
    if messagebox.askyesno(get_text("proxy_config_title"), get_text("proxy_config_message_generic"), parent=root_widget):
        default_proxy = os.environ.get("HTTP_PROXY", "") or os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7890")
        proxy_addr = simpledialog.askstring(get_text("proxy_address_title"), get_text("proxy_address_prompt", default_proxy=default_proxy), initialvalue=default_proxy, parent=root_widget)
        if proxy_addr:
            proxy_env["HTTP_PROXY"] = proxy_addr
            proxy_env["HTTPS_PROXY"] = proxy_addr
            update_status_bar("proxy_configured_status", proxy_addr=proxy_addr)
        else:
            update_status_bar("proxy_skip_status")
    return proxy_env

def _launch_process_gui(cmd: List[str], service_name_key: str, env_vars: Optional[Dict[str, str]] = None,
                        use_new_console_on_win: bool = False, capture_output: bool = True, fully_detached: bool = False):
    global managed_process_info
    service_name = get_text(service_name_key)

    if is_any_service_known() and managed_process_info["popen"] and managed_process_info["popen"].poll() is None:
        current_service_name_key = managed_process_info.get('service_name_key', 'unknown_service_key')
        current_service_name = get_text(current_service_name_key if current_service_name_key in LANG_TEXTS else "service_name_headed_interactive") # Fallback
        messagebox.showerror(get_text("error_title"), get_text("service_already_running", service_name=current_service_name))
        return

    if managed_process_info.get("output_area"): # Clear previous output
        managed_process_info["output_area"].config(state=tk.NORMAL)
        managed_process_info["output_area"].delete('1.0', tk.END)
        managed_process_info["output_area"].config(state=tk.DISABLED)

    effective_env = os.environ.copy()
    if env_vars: effective_env.update(env_vars)
    effective_env['PYTHONIOENCODING'] = 'utf-8' # Ensure consistent encoding

    popen_kwargs: Dict[str, Any] = {"env": effective_env}
    if capture_output: # For headless/detached to get logs, or if new console isn't used
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE
        # text, encoding, errors are implicitly handled by reading bytes and decoding in enqueue_stream_output

    if platform.system() == "Windows":
        if fully_detached:
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        elif use_new_console_on_win:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        # else: default flags (e.g. if capture_output is True but not detached and no new console)
    elif fully_detached: # Linux/macOS for full detachment
        popen_kwargs["start_new_session"] = True
    # `detached` param for Popen is complex, start_new_session or DETACHED_PROCESS are more direct.
    
    try:
        # print(f"Launching: {' '.join(cmd)} with Popen kwargs: {popen_kwargs}", flush=True) # For debug
        popen = subprocess.Popen(cmd, **popen_kwargs)
        managed_process_info["popen"] = popen
        managed_process_info["service_name_key"] = service_name_key
        managed_process_info["fully_detached"] = fully_detached # Store detached status

        update_status_bar("service_starting_status", service_name=service_name, pid=popen.pid)
        if fully_detached: # Add note for detached services
            if managed_process_info.get("output_area"):
                 managed_process_info["output_area"].config(state=tk.NORMAL)
                 managed_process_info["output_area"].insert(tk.END, f"[INFO] {get_text('info_service_is_independent')}\n")
                 managed_process_info["output_area"].see(tk.END)
                 managed_process_info["output_area"].config(state=tk.DISABLED)
        
        if managed_process_info.get("monitor_thread") and managed_process_info["monitor_thread"].is_alive():
            # This case should ideally not happen if UI logic is correct (one service at a time)
            print("Warning: Previous monitor thread still alive.", file=sys.stderr)

        monitor_thread = threading.Thread(target=monitor_process_thread_target, daemon=True)
        managed_process_info["monitor_thread"] = monitor_thread
        monitor_thread.start()

        if root_widget: # Auto-refresh port list after launch
            root_widget.after(2500, query_port_and_display_pids_gui) # Slightly longer delay
            
    except FileNotFoundError:
        messagebox.showerror(get_text("error_title"), get_text("script_not_found_error_msgbox", cmd=' '.join(cmd)))
        update_status_bar("status_script_not_found", service_name=service_name)
    except Exception as e:
        messagebox.showerror(get_text("error_title"), f"{service_name} - {get_text('error_title')}: {e}")
        update_status_bar("status_error_starting", service_name=service_name)

def start_headed_interactive_gui():
    port = get_current_port_from_gui()
    proxy_env = _configure_proxy_env_vars()
    cmd = [PYTHON_EXECUTABLE, LAUNCH_CAMOUFOX_PY, '--debug', '--server-port', str(port)]
    update_status_bar("status_headed_launch")
    _launch_process_gui(cmd, "service_name_headed_interactive", env_vars=proxy_env,
                        use_new_console_on_win=True, capture_output=False, fully_detached=False)

def start_headless_independent_gui():
    port = get_current_port_from_gui()
    proxy_env = _configure_proxy_env_vars()
    cmd = [PYTHON_EXECUTABLE, LAUNCH_CAMOUFOX_PY, "--headless", "--server-port", str(port)]
    update_status_bar("status_headless_independent_launch")
    _launch_process_gui(cmd, "service_name_headless_independent", env_vars=proxy_env,
                        use_new_console_on_win=False, capture_output=True, fully_detached=True)

def stop_managed_service_gui():
    if not managed_process_info.get("popen") or managed_process_info["popen"].poll() is not None:
        messagebox.showinfo(get_text("info_title"), get_text("no_service_running_status"))
        update_status_bar("no_service_running_status")
        return

    if managed_process_info.get("fully_detached", False):
        messagebox.showwarning(get_text("warning_title"), get_text("warn_cannot_stop_independent_service"))
        return

    popen = managed_process_info["popen"]
    service_name_key = managed_process_info["service_name_key"]
    service_name = get_text(service_name_key if service_name_key in LANG_TEXTS else "service_name_headed_interactive") # Fallback
    pid = popen.pid if popen else "N/A"
    update_status_bar("status_stopping_service", service_name=service_name, pid=str(pid))
    try:
        popen.terminate()
        # Monitor thread will update final status. Optionally, can force kill after timeout here.
    except Exception as e:
        messagebox.showerror(get_text("error_title"), get_text("error_stopping_service_msgbox", service_name=service_name, pid=str(pid), e=e))
        update_status_bar("error_stopping_service_msgbox", service_name=service_name, pid=str(pid), e=e)

def query_port_and_display_pids_gui():
    port = get_current_port_from_gui()
    # Update the LabelFrame's text to include the queried port
    if root_widget: # Ensure GUI elements are available
        # Find the pid_list_lbl_frame widget. This assumes it's accessible.
        # A more robust way might be to pass it as an argument or store it globally if not already.
        # For now, we assume it's findable or this function is called when it's in scope.
        # Let's assume pid_list_lbl_frame is accessible via a global or a direct reference
        # (it's typically part of the build_gui scope and widgets_to_translate)

        # To update the LabelFrame's text, we need a reference to it.
        # We'll iterate through widgets_to_translate to find it by its key if it's registered there.
        # However, it's better to update it directly if we have a reference.
        # Let's assume 'pid_list_lbl_frame_widget_ref' is the actual widget if available
        # For now, we'll rely on update_all_ui_texts_gui to potentially refresh it,
        # but a direct update here is better.
        # We will find it by iterating widgets_to_translate for now
            
            pid_list_frame_widget = None
            for item in widgets_to_translate:
                if item.get("key") == "pids_on_port_label": # The key for the static label
                    pid_list_frame_widget = item["widget"]
                    break
            
            if pid_list_frame_widget and hasattr(pid_list_frame_widget, 'config'):
                pid_list_frame_widget.config(text=get_text("pids_on_port_label_dynamic", port=port))
    
    if pid_listbox_widget: pid_listbox_widget.delete(0, tk.END)
    processes_info = find_processes_on_port(port)
    if processes_info:
        for proc_info in processes_info:
            # 显示格式为"PID - 名称"
            display_text = f"{proc_info['pid']} - {proc_info['name']}"
            if pid_listbox_widget: pid_listbox_widget.insert(tk.END, display_text)
    else:
        if pid_listbox_widget: pid_listbox_widget.insert(tk.END, get_text("no_pids_found"))

def stop_selected_pid_from_list_gui():
    if not pid_listbox_widget: return
    selected_indices = pid_listbox_widget.curselection()
    if not selected_indices:
        messagebox.showwarning(get_text("warning_title"), get_text("pid_list_empty_for_stop_warn"), parent=root_widget)
        return
    selected_text = pid_listbox_widget.get(selected_indices[0])
    
    pid_to_stop = -1
    process_name_to_stop = get_text("unknown_process_name_placeholder")

    try:
        # 匹配"PID - Name"格式，例如："12345 - python.exe"
        match = re.match(r"(\d+)\s*-\s*(.*)", selected_text)
        if match:
            pid_to_stop = int(match.group(1))
            process_name_to_stop = match.group(2).strip()
        elif selected_text != get_text("no_pids_found") and selected_text.isdigit(): # 如果只有PID的备用方案
            pid_to_stop = int(selected_text)
            # process_name_to_stop 将保持默认的未知（已设置）
        else: # 无法解析
            if selected_text != get_text("no_pids_found"): # 避免"no pids found"的错误
                 messagebox.showerror(get_text("error_title"), get_text("error_parsing_pid", selection=selected_text), parent=root_widget)
            return
    except ValueError: # int转换失败
        messagebox.showerror(get_text("error_title"), get_text("error_parsing_pid", selection=selected_text), parent=root_widget)
        return
    
    if pid_to_stop == -1: # 应该被上面捕获，但作为安全措施
        if selected_text != get_text("no_pids_found"):
             messagebox.showerror(get_text("error_title"), get_text("error_parsing_pid", selection=selected_text), parent=root_widget)
        return

    if messagebox.askyesno(get_text("confirm_stop_pid_title"), get_text("confirm_stop_pid_message", pid=pid_to_stop, name=process_name_to_stop), parent=root_widget):
        if kill_process_pid(pid_to_stop):
            messagebox.showinfo(get_text("info_title"), get_text("terminate_request_sent", pid=pid_to_stop, name=process_name_to_stop), parent=root_widget)
        else:
            messagebox.showwarning(get_text("warning_title"), get_text("terminate_attempt_failed", pid=pid_to_stop, name=process_name_to_stop), parent=root_widget)
        query_port_and_display_pids_gui() # 刷新列表

def kill_custom_pid_gui():
    if not custom_pid_entry_var or not root_widget: return
    pid_str = custom_pid_entry_var.get()
    if not pid_str:
        messagebox.showwarning(get_text("warning_title"), get_text("pid_input_empty_warn"), parent=root_widget)
        return
    if not pid_str.isdigit():
        messagebox.showwarning(get_text("warning_title"), get_text("pid_input_invalid_warn"), parent=root_widget)
        return
    
    pid_to_kill = int(pid_str)
    process_name_to_kill = get_process_name_by_pid(pid_to_kill) # 尝试获取名称以进行确认
    
    confirm_msg = get_text("confirm_stop_pid_message", pid=pid_to_kill, name=process_name_to_kill)
    
    if messagebox.askyesno(get_text("confirm_kill_custom_pid_title"), confirm_msg, parent=root_widget):
        if kill_process_pid(pid_to_kill):
            messagebox.showinfo(get_text("info_title"), get_text("terminate_request_sent", pid=pid_to_kill, name=process_name_to_kill), parent=root_widget)
        else:
            messagebox.showwarning(get_text("warning_title"), get_text("terminate_attempt_failed", pid=pid_to_kill, name=process_name_to_kill), parent=root_widget)
        
        custom_pid_entry_var.set("") # 尝试后清除输入
        query_port_and_display_pids_gui() # 刷新列表

menu_bar_ref: Optional[tk.Menu] = None

def update_all_ui_texts_gui():
    if not root_widget: return
    root_widget.title(get_text("title"))
    # menu_bar_ref.entryconfigure("Language", label=get_text("menu_language_fixed")) # Assuming "Language" is key or index
    
    for item in widgets_to_translate:
        widget = item["widget"]
        key = item["key"]
        prop = item.get("property", "text") # Default to 'text'
        text_val = get_text(key, **item.get("kwargs", {}))
        if hasattr(widget, 'config'):
            try: widget.config(**{prop: text_val})
            except tk.TclError: pass # print(f"Warn: Could not config {prop} for {widget} (key: {key})") # Less verbose
    
    current_status_text = process_status_text_var.get() if process_status_text_var else ""
    # Check if current status is one of the "idle" messages in any language
    is_idle_status = any(current_status_text == LANG_TEXTS["status_idle"].get(lang_code, "") for lang_code in LANG_TEXTS["status_idle"])
    if is_idle_status: update_status_bar("status_idle") # Refresh to current language if idle

def switch_language_gui(lang_code: str):
    global current_language
    if lang_code in LANG_TEXTS["title"]: # Check if lang_code is valid key
        current_language = lang_code
        update_all_ui_texts_gui()
    # else: print(f"Warning: Language code '{lang_code}' not fully supported.") # Less verbose

def build_gui(root: tk.Tk):
    global process_status_text_var, port_entry_var, pid_listbox_widget, widgets_to_translate, managed_process_info, root_widget, menu_bar_ref, custom_pid_entry_var
    root_widget = root
    
    # --- 设置窗口属性 ---
    root.title(get_text("title"))
    # 设置最小窗口大小，防止组件挤压变形
    root.minsize(750, 500)
    
    # --- 样式配置 ---
    s = ttk.Style()
    # 不使用特殊主题，只调整默认样式
    s.configure('TButton', padding=3)
    s.configure('TLabelFrame.Label', font=('Default', 10, 'bold'))
    s.configure('TLabelFrame', padding=4)
    
    # 确保认证目录存在
    try:
        os.makedirs(ACTIVE_AUTH_DIR, exist_ok=True)
        os.makedirs(SAVED_AUTH_DIR, exist_ok=True)
    except OSError as e:
        messagebox.showerror(get_text("error_title"), f"无法创建认证目录: {e}")
        # Decide if this is fatal or just a warning

    process_status_text_var = tk.StringVar(value=get_text("status_idle"))
    port_entry_var = tk.StringVar(value=str(DEFAULT_FASTAPI_PORT))
    custom_pid_entry_var = tk.StringVar() # 初始化自定义PID输入变量

    menu_bar_ref = tk.Menu(root)
    lang_menu = tk.Menu(menu_bar_ref, tearoff=0)
    # Static labels for menu items, command changes language and calls update_all_ui_texts_gui
    lang_menu.add_command(label="中文 (Chinese)", command=lambda: switch_language_gui('zh'))
    lang_menu.add_command(label="English", command=lambda: switch_language_gui('en'))
    menu_bar_ref.add_cascade(label="Language", menu=lang_menu) # Fixed "Language" cascade label
    root.config(menu=menu_bar_ref)

    # 创建主框架并使其完全填充窗口
    main_frame = ttk.Frame(root, padding="10")
    main_frame.grid(row=0, column=0, sticky="nsew")
    
    # 设置根窗口的列和行权重，使主框架可扩展
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    
    # 将界面分为左右两栏，使用PanedWindow以允许用户调整分割
    paned_window = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
    paned_window.grid(row=0, column=0, sticky="nsew")
    main_frame.columnconfigure(0, weight=1)
    main_frame.rowconfigure(0, weight=1)
    
    # 左右两个框架作为PanedWindow的子项
    left_frame = ttk.Frame(paned_window, width=400)  # 增加左侧最小宽度
    right_frame = ttk.Frame(paned_window, width=200)  # 减小右侧初始宽度
    
    # 添加到PanedWindow
    paned_window.add(left_frame, weight=1)  # 左侧固定宽度优先
    paned_window.add(right_frame, weight=4)  # 右侧获得更大的伸缩空间
    
    # 确保框架可以正确扩展
    left_frame.columnconfigure(0, weight=1)
    right_frame.columnconfigure(0, weight=1)
    
    # === 左侧栏 ===
    left_current_row = 0
    
    # 端口管理部分
    port_section = ttk.LabelFrame(left_frame, text="")
    port_section.grid(row=left_current_row, column=0, padx=5, pady=5, sticky="ew")
    widgets_to_translate.append({"widget": port_section, "key": "port_section_label", "property": "text"})
    
    # 确保端口部分的内容可以水平扩展
    port_section.columnconfigure(1, weight=1)
    
    lbl_port = ttk.Label(port_section, text="")
    lbl_port.grid(row=0, column=0, padx=5, pady=5, sticky="w")
    widgets_to_translate.append({"widget": lbl_port, "key": "port_label"})
    
    entry_port = ttk.Entry(port_section, textvariable=port_entry_var, width=8)
    entry_port.grid(row=0, column=1, padx=5, pady=5, sticky="w")
    
    lbl_port_desc = ttk.Label(port_section, text="", wraplength=200)
    lbl_port_desc.grid(row=1, column=0, columnspan=2, padx=5, pady=(0,5), sticky="w")
    widgets_to_translate.append({"widget": lbl_port_desc, "key": "port_input_description_lbl"})
    
    # 自适应换行长度
    def update_port_desc_wraplength(event=None):
        if lbl_port_desc.winfo_exists():
            width = port_section.winfo_width() - 20
            if width > 100:
                lbl_port_desc.config(wraplength=width)
    
    port_section.bind("<Configure>", update_port_desc_wraplength)
    
    btn_query = ttk.Button(port_section, text="", command=query_port_and_display_pids_gui)
    btn_query.grid(row=0, column=2, rowspan=2, padx=5, pady=5, sticky="ne")
    widgets_to_translate.append({"widget": btn_query, "key": "query_pids_btn"})
    
    left_current_row += 1

    # PID管理部分 - 减少高度
    pid_list_lbl_frame = ttk.LabelFrame(left_frame, text=get_text("pids_on_port_label"))
    pid_list_lbl_frame.grid(row=left_current_row, column=0, sticky="nsew", padx=5, pady=5)
    widgets_to_translate.append({"widget": pid_list_lbl_frame, "key": "pids_on_port_label", "property": "text"})
    
    # 让列表框架可以在其容器中扩展
    pid_list_lbl_frame.columnconfigure(0, weight=1)
    pid_list_lbl_frame.rowconfigure(0, weight=1)
    
    pid_listbox_widget = tk.Listbox(pid_list_lbl_frame, height=3, exportselection=False)
    pid_listbox_widget.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
    
    # 添加滚动条
    scrollbar = ttk.Scrollbar(pid_list_lbl_frame, orient="vertical", command=pid_listbox_widget.yview)
    scrollbar.grid(row=0, column=1, sticky="ns", padx=(0,5), pady=5)
    pid_listbox_widget.config(yscrollcommand=scrollbar.set)
    
    left_current_row += 1
    
    # 进程控制按钮区域（垂直排列）
    pid_control_frame = ttk.Frame(left_frame)
    pid_control_frame.grid(row=left_current_row, column=0, padx=5, pady=5, sticky="ew")
    pid_control_frame.columnconfigure(0, weight=1)  # 让内部控件可以水平扩展
    
    # 停止选中进程按钮（第一行）
    btn_stop_pid = ttk.Button(pid_control_frame, text="", command=stop_selected_pid_from_list_gui)
    btn_stop_pid.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
    widgets_to_translate.append({"widget": btn_stop_pid, "key": "stop_selected_pid_btn"})
    
    # 终止指定PID输入框和按钮（第二行）
    kill_custom_frame = ttk.Frame(pid_control_frame)
    kill_custom_frame.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
    kill_custom_frame.columnconfigure(0, weight=0)  # 标签固定宽度
    kill_custom_frame.columnconfigure(1, weight=1)  # 输入框可扩展
    kill_custom_frame.columnconfigure(2, weight=0)  # 按钮固定宽度
    
    lbl_custom_pid = ttk.Label(kill_custom_frame, text="")
    lbl_custom_pid.grid(row=0, column=0, padx=(0,5), pady=2, sticky="w")
    widgets_to_translate.append({"widget": lbl_custom_pid, "key": "kill_custom_pid_label"})
    
    entry_custom_pid = ttk.Entry(kill_custom_frame, textvariable=custom_pid_entry_var, width=6)
    entry_custom_pid.grid(row=0, column=1, padx=2, pady=2, sticky="ew")
    
    btn_kill_custom_pid = ttk.Button(kill_custom_frame, text="", command=kill_custom_pid_gui, width=10)
    btn_kill_custom_pid.grid(row=0, column=2, padx=5, pady=2, sticky="e")
    widgets_to_translate.append({"widget": btn_kill_custom_pid, "key": "kill_custom_pid_btn"})
    
    left_current_row += 1
    
    # 移动启动选项部分到左侧底部
    launch_options_frame = ttk.LabelFrame(left_frame, text="")
    launch_options_frame.grid(row=left_current_row, column=0, padx=5, pady=5, sticky="ew")
    widgets_to_translate.append({"widget": launch_options_frame, "key": "launch_options_label", "property": "text"})
    
    lbl_launch_options_note = ttk.Label(launch_options_frame, text="", wraplength=300)
    lbl_launch_options_note.pack(fill=tk.X, padx=5, pady=(5, 8))
    widgets_to_translate.append({"widget": lbl_launch_options_note, "key": "launch_options_note_revised"})
    
    # 自适应换行长度
    def update_options_note_wraplength(event=None):
        if lbl_launch_options_note.winfo_exists():
            width = launch_options_frame.winfo_width() - 20
            if width > 100:
                lbl_launch_options_note.config(wraplength=width)
    
    launch_options_frame.bind("<Configure>", update_options_note_wraplength)
    
    # 水平排列启动按钮
    launch_buttons_frame = ttk.Frame(launch_options_frame)
    launch_buttons_frame.pack(fill=tk.X, padx=5, pady=3)
    launch_buttons_frame.columnconfigure(0, weight=1)
    launch_buttons_frame.columnconfigure(1, weight=1)
    
    btn_headed = ttk.Button(launch_buttons_frame, text="", command=start_headed_interactive_gui)
    btn_headed.grid(row=0, column=0, padx=5, pady=3, sticky="ew")
    widgets_to_translate.append({"widget": btn_headed, "key": "launch_headed_interactive_btn"})

    btn_headless_independent = ttk.Button(launch_buttons_frame, text="", command=start_headless_independent_gui)
    btn_headless_independent.grid(row=0, column=1, padx=5, pady=3, sticky="ew")
    widgets_to_translate.append({"widget": btn_headless_independent, "key": "launch_headless_independent_btn"})
    
    # 停止服务按钮
    btn_stop_service = ttk.Button(launch_options_frame, text="", command=stop_managed_service_gui)
    btn_stop_service.pack(fill=tk.X, padx=5, pady=3)
    widgets_to_translate.append({"widget": btn_stop_service, "key": "stop_gui_service_btn"})
    
    left_current_row += 1
    
    # 设置左侧栏中PID列表的权重较小
    left_frame.rowconfigure(1, weight=1)  # PID列表行权重保持为1
    
    # === 右侧栏 ===
    right_current_row = 0
    
    # 状态区域
    status_area_frame = ttk.LabelFrame(right_frame, text="")
    status_area_frame.grid(row=right_current_row, column=0, padx=5, pady=5, sticky="ew")
    widgets_to_translate.append({"widget": status_area_frame, "key": "status_label", "property": "text"})
    
    lbl_status_val = ttk.Label(status_area_frame, textvariable=process_status_text_var, wraplength=180)
    lbl_status_val.pack(fill=tk.X, padx=5, pady=5)
    
    # 动态调整状态标签的自动换行宽度
    def rewrap_status_label(event=None):
        if root_widget and lbl_status_val.winfo_exists():
            new_width = status_area_frame.winfo_width() - 20
            if new_width > 100:
                lbl_status_val.config(wraplength=new_width)
    
    status_area_frame.bind("<Configure>", rewrap_status_label)
    
    right_current_row += 1

    # 输出日志区域 - 占据右侧主要空间
    output_log_area_frame = ttk.LabelFrame(right_frame, text="")
    output_log_area_frame.grid(row=right_current_row, column=0, padx=5, pady=5, sticky="nsew")
    widgets_to_translate.append({"widget": output_log_area_frame, "key": "output_label", "property": "text"})
    
    # 让输出日志区域可以扩展
    output_log_area_frame.columnconfigure(0, weight=1)
    output_log_area_frame.rowconfigure(0, weight=1)
    
    output_scrolled_text = scrolledtext.ScrolledText(output_log_area_frame, height=15, width=20, wrap=tk.WORD, state=tk.DISABLED)
    output_scrolled_text.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
    managed_process_info["output_area"] = output_scrolled_text
    
    # 设置右侧输出区域权重较大，使其占据主要空间
    right_frame.rowconfigure(right_current_row, weight=5)

    update_all_ui_texts_gui() # 设置初始文本

    def on_app_close_main():
        if managed_process_info.get("popen") and managed_process_info["popen"].poll() is None:
            service_name_key = managed_process_info.get('service_name_key', "service_name_headed_interactive")
            service_name = get_text(service_name_key if service_name_key in LANG_TEXTS else "service_name_headed_interactive") # Fallback
            
            if managed_process_info.get("fully_detached", False):
                # For fully detached, just confirm quit, don't offer to stop.
                if messagebox.askyesno(get_text("confirm_quit_title"), get_text("confirm_quit_message_independent", service_name=service_name), parent=root):
                    root.destroy()
                else:
                    return # Don't quit
            else: # For managed services
                if messagebox.askyesno(get_text("confirm_quit_title"), get_text("confirm_quit_message", service_name=service_name), parent=root):
                    stop_managed_service_gui() # Attempt to stop managed service
                    # Give a moment for termination, though monitor_thread handles final state
                    # This is mainly for GUI responsiveness before destroy.
                    if managed_process_info.get("popen"): # If stop_managed_service_gui didn't clear it (e.g. error)
                         try:
                            managed_process_info["popen"].wait(timeout=0.5) # Brief wait
                         except subprocess.TimeoutExpired:
                            pass # Don't hang GUI close
                    root.destroy()
                else:
                    return # Don't quit
        else: # No service running or already terminated
            root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_app_close_main)

if __name__ == "__main__":
    if not os.path.exists(LAUNCH_CAMOUFOX_PY) or not os.path.exists(os.path.join(SCRIPT_DIR, SERVER_PY_FILENAME)):
        err_lang = current_language # Use global current_language
        err_title_key = "startup_error_title"
        err_msg_key = "startup_script_not_found_msgbox"
        
        err_title = LANG_TEXTS[err_title_key].get(err_lang, LANG_TEXTS[err_title_key]['en'])
        err_msg_template = LANG_TEXTS[err_msg_key].get(err_lang, LANG_TEXTS[err_msg_key]['en'])
        # Generic message as either could be missing
        err_msg = err_msg_template.format(script=f"{os.path.basename(LAUNCH_CAMOUFOX_PY)} or {SERVER_PY_FILENAME}")
        
        try: # Try to show GUI error box
            root_err = tk.Tk(); root_err.withdraw() # Hidden root for messagebox
            messagebox.showerror(err_title, err_msg, parent=None)
            root_err.destroy()
        except tk.TclError: # Fallback to console if GUI can't init
            print(f"ERROR: {err_msg}", file=sys.stderr)
        sys.exit(1)
    
    app_root = tk.Tk()
    build_gui(app_root)
    app_root.mainloop()