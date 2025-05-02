# AI Studio Proxy Server (Python/Camoufox Version)

[![Star History Chart](https://api.star-history.com/svg?repos=CJackHwang/AIstudioProxyAPI&type=Date)](https://www.star-history.com/#CJackHwang/AIstudioProxyAPI&Date)

**这是当前维护的 Python 版本。旧的、不再维护的 Javascript 版本请参见 [`deprecated_javascript_version/README.md`](deprecated_javascript_version/README.md)。**

---

## 📝 目录

*   [项目概述](#项目概述)
*   [免责声明](#免责声明)
*   [核心特性](#核心特性-python-版本)
*   [重要提示](#️重要提示-python-版本)
*   [开始使用](#-开始使用-python-版本)
    *   [1. 先决条件](#1-先决条件)
    *   [2. 安装](#2-安装)
    *   [3. 认证设置 (关键步骤!)](#3-认证设置-关键步骤)
    *   [4. 运行代理](#4-运行代理)
    *   [5. API 使用](#5-api-使用)
    *   [6. 配置客户端 (以 Open WebUI 为例)](#6-配置客户端-以-open-webui-为例)
    *   [7. (可选) 局域网域名访问 (mDNS)](#7-可选-局域网域名访问-mdns)
*   [多平台指南](#多平台指南-python-版本)
*   [故障排除](#故障排除-python-版本)
*   [关于 Camoufox](#关于-camoufox)
*   [关于 `fetch_camoufox_data.py`](#关于-fetch_camoufox_datapy)
*   [贡献](#贡献)
*   [License](#license)
*   [未来计划 / Roadmap](#未来计划--roadmap)
*   [控制日志输出](#控制日志输出-python-版本)

---

## 项目概述

这是一个基于 **Python + FastAPI + Playwright + Camoufox** 的代理服务器，旨在通过模拟 OpenAI API 的方式间接访问 Google AI Studio 网页版。

项目核心优势在于结合了：

*   **FastAPI**: 提供高性能、兼容 OpenAI 标准的 API 接口。
*   **Playwright**: 强大的浏览器自动化库，用于与 AI Studio 页面交互。
*   **Camoufox**: 一个经过修改和优化的 Firefox 浏览器，专注于**反指纹检测和反机器人探测**。它通过底层修改而非 JS 注入来伪装浏览器指纹，旨在模拟真实用户流量，提高自动化操作的隐蔽性和成功率。
*   **请求队列**: 保证请求按顺序处理，提高稳定性。

通过此代理，支持 OpenAI API 的各种客户端（如 Open WebUI, LobeChat, NextChat 等）可以连接并使用 Google AI Studio 的模型。

## ✋ 免责声明

使用本项目即表示您已完整阅读、理解并同意本免责声明的全部内容。
本项目通过自动化脚本（Playwright + Camoufox）与 Google AI Studio 网页版进行交互。这种自动化访问网页的方式可能违反 Google AI Studio 或相关 Google 服务的用户协议或服务条款（Terms of Service）。不当使用本项目可能导致您的 Google 账号受到警告、功能限制、暂时或永久封禁等处罚。项目作者及贡献者对此不承担任何责任。
由于本项目依赖于 Google AI Studio 网页的结构和前端代码，Google 随时可能更新或修改其网页，这可能导致本项目的功能失效、不稳定或出现未知错误。项目作者及贡献者无法保证本项目的持续可用性或稳定性。
本项目并非 Google 或 OpenAI 的官方项目或合作项目。它是一个完全独立的第三方工具。项目作者与 Google 和 OpenAI 没有任何关联。
本项目按"现状"（AS IS）提供，不提供任何明示或暗示的保证，包括但不限于适销性、特定用途的适用性及不侵权的保证。您理解并同意自行承担使用本项目可能带来的所有风险。
在任何情况下，项目作者或贡献者均不对因使用或无法使用本项目而产生的任何直接、间接、附带、特殊、惩罚性或后果性的损害承担责任。
使用本项目，即视为您已完全理解并接受本免责声明的全部条款。如果您不同意本声明的任何内容，请立即停止使用本项目。

## ✨ 核心特性 (Python 版本)

*   **OpenAI API 兼容**: 提供 `/v1/chat/completions` 和 `/v1/models` 端点。
*   **流式/非流式响应**: 支持 `stream=true` 和 `stream=false`。
*   **请求队列**: 使用 `asyncio.Queue` 顺序处理请求，提高稳定性。
*   **Camoufox 集成**: 通过 `launch_camoufox.py` 调用 `camoufox` 库启动修改版的 Firefox 实例，利用其反指纹和反检测能力。
*   **两种启动模式**: 
    *   `debug` (有界面): 便于首次认证、调试和查看浏览器状态。
    *   `headless` (无头，默认): 适合后台自动运行。
*   **系统提示词**: 支持 `messages` 中的 `system` 角色。
*   **内部 Prompt 优化**: 自动包装用户输入以引导 AI Studio 输出特定格式，便于解析。
*   **认证状态持久化**: 通过保存和加载浏览器认证状态 (`auth_profiles` 目录) 实现免登录。
*   **自动清空上下文**: 尝试在新对话开始时自动清空 AI Studio 页面的聊天记录。
*   **服务端 (`server.py`)**: FastAPI 应用，处理 API 请求，通过 Playwright 控制 Camoufox 浏览器与 AI Studio 交互。
*   **启动器 (`launch_camoufox.py`)**: 负责启动 Camoufox 服务和 FastAPI 服务，并建立两者之间的连接。
*   **错误快照**: 出错时自动在 `errors_py/` 目录保存截图和 HTML。
*   **日志控制**: 可通过参数或环境变量调整日志级别和频率。
*   **辅助端点**: 提供 `/health`, `/v1/queue`, `/v1/cancel/{req_id}` 等端点。
*   **Web UI**: 提供 `/` 路径访问一个简单的 HTML 聊天界面 (`index.html`) 用于测试。

## ⚠️ 重要提示 (Python 版本)

*   **非官方项目**: 依赖 AI Studio Web 界面，可能因页面更新失效。
*   **认证文件是关键**: `headless` 模式**高度依赖**于 `auth_profiles/active/` 下有效的 `.json` 认证文件。**文件可能会过期**，需要定期通过 `debug` 模式手动保存文件替换更新。
*   **CSS 选择器依赖**: 自动清空上下文、页面交互等功能依赖 `server.py` 中定义的 CSS 选择器。AI Studio 页面更新可能导致这些选择器失效，需要手动更新。
*   **不支持历史编辑/分叉**: 代理无法支持客户端的历史编辑功能。
*   **Camoufox 特性**: 本项目利用 Camoufox 提供的浏览器实例进行交互。Camoufox 通过修改底层实现来增强反指纹能力，旨在模拟真实用户。了解更多信息请参考 [Camoufox 官方文档](https://camoufox.com/)。
*   **稳定性**: 浏览器自动化本质上不如原生 API 稳定，长时间运行可能需要重启。
*   **AI Studio 限制**: 无法绕过 AI Studio 本身的速率、内容等限制。
*   **模型参数**: 温度、Top-K/P、最大输出长度等模型参数**需要在 AI Studio Web UI 中设置**，本代理未制作自动化设置故暂时不转发 API 请求中的这些参数。

## 🚀 开始使用 (Python 版本)

### 1. 先决条件

*   **Python**: 3.8 或更高版本 (建议 3.9+)。
*   **pip**: Python 包管理器。
*   **(可选但推荐) Git**: 用于克隆仓库。
*   **Google AI Studio 账号**: 并能正常访问和使用。

### 2. 安装

1.  **克隆仓库**: 
    ```bash
    git clone https://github.com/CJackHwang/AIstudioProxyAPI
    cd AIstudioProxyAPI
    ```

2.  **(推荐) 创建并激活虚拟环境**:
    ```bash
    python -m venv venv
    source venv/bin/activate  # Linux/macOS
    # venv\Scripts\activate  # Windows
    ```

3.  **安装 Camoufox 和依赖**: 
    ```bash
    # 安装 Camoufox 库 (推荐包含 geoip 数据，特别是使用代理时)
    pip install -U camoufox[geoip]
    
    # 安装项目所需的其他 Python 库
    pip install -r requirements.txt
    ```
    `requirements.txt` 主要包含 `fastapi`, `uvicorn[standard]`, `playwright`, `pydantic`。

4.  **下载 Camoufox 浏览器**: 
    ```bash
    # Camoufox 需要下载其修改版的 Firefox
    camoufox fetch
    ```
    如果此步骤因 SSL 证书等网络问题失败，可以尝试运行项目中的 `fetch_camoufox_data.py` 脚本 (详见[下方说明](#-关于-fetch_camoufox_datapy))。

5.  **安装 Playwright 浏览器依赖 (如果需要)**:
    虽然 Camoufox 使用自己的 Firefox，但首次运行 Playwright 相关命令（如此处的安装）可能仍需要安装一些基础依赖。
```bash
    # 确保 Playwright 库能找到必要的系统依赖
    playwright install-deps firefox 
    # 或者 playwright install-deps # 安装所有浏览器的依赖
```

### 3. 认证设置 (关键步骤!)

为了避免每次启动都手动登录 AI Studio，本项目通过保存和加载浏览器的认证状态 (`.json` 文件) 来实现持久化登录。**你必须先通过 `debug` 模式运行一次来生成这个认证文件。**

1.  **首次运行 (Debug 模式) - 生成认证文件**:
    ```bash
    python launch_camoufox.py --debug
    ```
    *   `launch_camoufox.py` 会调用 Camoufox 启动一个**有界面的**浏览器窗口。
    *   终端会输出 Camoufox 服务器的 WebSocket 端点，格式类似 `ws://127.0.0.1:xxxxx/xxxxxxxx`。**复制这个完整的地址**。
    *   将复制的地址粘贴回终端并按回车。
    *   `server.py` (FastAPI 应用) 会尝试连接到这个 Camoufox 浏览器。
    *   **关键交互:** 观察终端日志和弹出的浏览器窗口：
        *   如果浏览器直接打开了 AI Studio 聊天界面，说明可能已有登录状态或无需登录。
        *   如果浏览器打开了 Google 登录页面，**请在浏览器窗口中手动完成登录流程** (输入账号、密码、二次验证等)，直到成功进入 AI Studio (`.../prompts/new_chat`)。
    *   当 `server.py` 确认页面加载完成且用户已登录后，它会在终端提示：**"是否要将当前的浏览器认证状态保存到文件？ (y/N):"**
    *   输入 `y` 并回车。
    *   可以选择输入一个文件名（如 `my_google_auth.json`），或直接回车使用默认名称 (`auth_state_时间戳.json`)。
    *   认证文件将保存在项目目录下的 `auth_profiles/saved/` 文件夹中。
    *   此时，代理服务已经可以使用了。你可以继续让它运行，或者按 `Ctrl+C` 停止它。

2.  **配置 Headless 模式 - 激活认证文件**:
    *   进入 `auth_profiles/saved/` 目录，找到刚才保存的 `.json` 认证文件。
    *   将这个 `.json` 文件**移动或复制**到 `auth_profiles/active/` 目录下。
    *   **重要:** 确保 `auth_profiles/active/` 目录下**有且仅有一个 `.json` 文件**。`headless` 模式启动时会自动加载此目录下的第一个 `.json` 文件作为认证状态。

**认证文件会过期!** Google 的登录状态不是永久有效的。当 `headless` 模式启动失败并报告认证错误或重定向到登录页时，意味着 `active` 目录下的认证文件已失效。你需要：

1.  删除 `active` 目录下的旧文件。
2.  重新执行【首次运行 (Debug 模式)】步骤，生成新的认证文件。
3.  将新生成的 `.json` 文件再次移动到 `active` 目录下。

### 4. 运行代理

完成认证设置后，可以根据需要选择模式运行：

*   **无头模式 (Headless - 推荐用于后台运行)**:
    *   **前提**: `auth_profiles/active/` 目录下**必须**存在一个有效的 `.json` 认证文件。
    *   **启动命令**: 
        ```bash
        python launch_camoufox.py
        ```
    *   脚本会自动使用 `active` 目录下的认证文件启动 Camoufox (无界面)。
    *   终端会输出 Camoufox 的 WebSocket 端点。**复制并粘贴回终端**。
    *   `server.py` 连接成功后，服务将在后台运行，你可以关闭终端窗口（如果使用 `nohup` 或类似工具启动）。

*   **调试模式 (Debug - 用于生成/更新认证文件或调试)**:
    *   **启动命令**: 
        ```bash
        python launch_camoufox.py --debug
        ```
    *   会启动**有界面的** Camoufox 浏览器。
    *   需要用户交互：
        *   如果 `auth_profiles/active` 和 `auth_profiles/saved` 目录下有多个 `.json` 文件，会提示选择加载哪个或不加载。
        *   需要复制粘贴 Camoufox 的 WebSocket 端点。
        *   可能需要在浏览器窗口中手动登录 (如果选择不加载认证文件或文件无效)。
        *   登录后会提示是否保存认证状态。

**启动流程简述:**

1.  运行 `python launch_camoufox.py` (带或不带 `--debug`)。
2.  `launch_camoufox.py` 调用 `camoufox.server.launch_server()` 启动 Camoufox 服务 (一个修改版的 Firefox 实例和一个 WebSocket 服务器)。
3.  Camoufox 服务在终端输出其 WebSocket 地址 (`ws://...`)。
4.  用户复制此地址并粘贴给 `launch_camoufox.py`。
5.  `launch_camoufox.py` 将此地址作为环境变量 (`CAMOUFOX_WS_ENDPOINT`) 传递给子进程，并启动 `python server.py`。
6.  `server.py` (FastAPI 应用) 启动，读取环境变量，使用 Playwright 通过 WebSocket 地址连接到正在运行的 Camoufox 浏览器实例。
7.  连接成功后，`server.py` 初始化页面逻辑（包括加载认证状态或处理登录），然后开始监听 API 请求。

**命令行参数:**

*   **`launch_camoufox.py`**: 
    *   `--debug`: 启用调试模式 (有界面)。
*   **`server.py`** (通常由 `launch_camoufox.py` 启动并传递配置，但也可以独立运行):
    *   `--port PORT`: FastAPI 监听端口 (默认 2048)。
    *   `--host HOST`: FastAPI 监听地址 (默认 127.0.0.1)。
    *   `--debug-logs`: 启用详细调试日志。
    *   `--trace-logs`: 启用更详细的跟踪日志。
    *   `--log-interval COUNT`: 流式日志按次数输出的间隔 (默认 20)。
    *   `--log-time-interval SECONDS`: 流式日志按时间输出的间隔 (默认 3.0)。

### 5. API 使用

代理服务器监听在 `http://<host>:<port>` (默认为 `http://127.0.0.1:2048`)。

*   **聊天接口**: `POST /v1/chat/completions`
    *   请求体与 OpenAI API 兼容，需要 `messages` 数组。
    *   `model` 字段会被接收但当前被忽略 (模型需在 AI Studio 页面设置)。
    *   `stream` 字段控制流式 (`true`) 或非流式 (`false`) 输出。
    *   **示例 (curl, 非流式)**:
        ```bash
        curl -X POST http://127.0.0.1:2048/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d '{
          "model": "aistudio-proxy", 
          "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "What is the capital of France?"}
          ],
          "stream": false
        }'
        ```
    *   **示例 (curl, 流式)**:
        ```bash
        curl -X POST http://127.0.0.1:2048/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d '{
          "model": "aistudio-proxy", 
          "messages": [
            {"role": "user", "content": "Write a short story about a cat."}
          ],
          "stream": true
        }' --no-buffer
        ```
    *   **示例 (Python `requests`)**: 
        ```python
        import requests
        import json
        
        API_URL = "http://127.0.0.1:2048/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        data = {
            "model": "aistudio-proxy", 
            "messages": [
                {"role": "user", "content": "Translate \'hello\' to Spanish."}
            ],
            "stream": False # or True for streaming
        }
        
        response = requests.post(API_URL, headers=headers, json=data, stream=data["stream"])
        
        if data["stream"]:
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        content = decoded_line[len('data: '):]
                        if content.strip() == '[DONE]':
                            print("\nStream finished.")
                            break
                        try:
                            chunk = json.loads(content)
                            delta = chunk.get('choices', [{}])[0].get('delta', {})
                            print(delta.get('content', ''), end='', flush=True)
                        except json.JSONDecodeError:
                            print(f"\nError decoding JSON: {content}")
        else:
            if response.status_code == 200:
                print(json.dumps(response.json(), indent=2))
            else:
                print(f"Error: {response.status_code}\n{response.text}")
        ```
*   **模型列表**: `GET /v1/models`
    *   返回一个固定的模型信息，名称在 `server.py` 中定义 (`MODEL_NAME`)。
*   **健康检查**: `GET /health`
    *   返回服务器运行状态（Playwright, 浏览器连接, 页面状态, Worker 状态, 队列长度）。
*   **队列状态**: `GET /v1/queue`
    *   返回当前请求队列的详细信息。
*   **取消请求**: `POST /v1/cancel/{req_id}`
    *   尝试取消仍在队列中等待处理的请求。
*   **Web UI (简单测试)**: `GET /`
    *   在浏览器中访问服务器根路径 (例如 `http://127.0.0.1:2048/`) 会显示一个基于 `index.html` 的简单聊天界面，可用于快速测试代理的基本功能。

### 6. 配置客户端 (以 Open WebUI 为例)

1.  打开 Open WebUI。
2.  进入 "设置" -> "连接"。
3.  在 "模型" 部分，点击 "添加模型"。
4.  **模型名称**: 输入你想要的名字，例如 `aistudio-gemini-py`。
5.  **API 基础 URL**: 输入代理服务器的地址，例如 `http://127.0.0.1:2048/v1` (如果服务器在另一台机器，用其 IP 替换 `127.0.0.1`，并确保端口可访问)。
6.  **API 密钥**: 留空或输入任意字符 (服务器不验证)。
7.  保存设置。
8.  现在，你应该可以在 Open WebUI 中选择 `aistudio-gemini-py` 模型并开始聊天了。

### 7. (可选) 局域网域名访问 (mDNS)

项目包含一个辅助脚本 `mdns_publisher.py`，它使用 mDNS (Bonjour/ZeroConf) 在你的局域网内广播此代理服务。这允许你和其他局域网内的设备通过一个更友好的 `.local` 域名（例如 `http://chatui.local:2048`）来访问服务，而无需记住或查找服务器的 IP 地址。

**用途:**

*   当你希望在手机、平板或其他电脑上方便地访问运行在 Mac/PC 上的代理服务时。
*   避免因 IP 地址变化而需要更新客户端配置。

**如何使用:**

1.  **安装依赖:** 此脚本需要额外的库。在你的虚拟环境中运行：
    ```bash
    pip install zeroconf netifaces
    ```
2.  **运行脚本:** 你需要**同时运行** `server.py` (监听在 `0.0.0.0` 和指定端口，如 2048) 和 `mdns_publisher.py`。
    在**另一个终端**窗口，运行：
    ```bash
    python mdns_publisher.py
    ```
    *   默认广播的域名是 `chatui.local`，广播的端口是脚本内 `PORT` 变量定义的端口 (当前为 2048)。
    *   你可以使用 `--name yourname` 参数来修改广播的域名前缀，例如 `python mdns_publisher.py --name mychat` 将广播 `mychat.local`。
    *   此脚本**不需要** `sudo` 权限运行。
3.  **访问服务:** 在局域网内的其他支持 mDNS 的设备上，通过浏览器访问 `http://<你设置的域名>.local:<端口>`，例如 `http://chatui.local:2048`。

**注意:**

*   确保你的防火墙允许 UDP 端口 5353 (mDNS) 的通信。
*   客户端设备需要支持 mDNS 才能解析 `.local` 域名。
*   此脚本广播的是 `server.py` 实际监听的端口 (由 `mdns_publisher.py` 中的 `PORT` 变量决定)。

## 💻 多平台指南 (Python 版本)

*   **macOS / Linux**: 通常开箱即用。确保 Python, pip 已安装。按照安装步骤安装 Camoufox 和 Playwright 依赖。
*   **Windows**:
    *   WSL (Windows Subsystem for Linux) 是推荐环境，体验更接近 Linux。
    *   直接在 Windows 上运行也可以，确保 Python, pip 已添加到 PATH。
    *   防火墙可能需要允许 Python/Uvicorn 监听端口。

## 🔧 故障排除 (Python 版本)

*   **`pip install camoufox[geoip]` 失败**: 
    *   可能是网络问题或缺少编译环境 (如果需要编译某些依赖)。尝试不带 `[geoip]` 安装 (`pip install camoufox`)。
*   **`camoufox fetch` 失败**: 
    *   常见原因是网络问题或 SSL 证书验证失败。
    *   可以尝试运行 `python fetch_camoufox_data.py` 脚本，它会尝试禁用 SSL 验证来下载 (有安全风险，仅在确认网络环境可信时使用)。
*   **`playwright install-deps` 失败**: 
    *   通常是 Linux 系统缺少必要的库。仔细阅读错误信息，根据提示安装缺失的系统包 (如 `libgbm-dev`, `libnss3` 等)。
*   **`launch_camoufox.py` 启动报错**: 
    *   检查 Camoufox 是否已通过 `camoufox fetch` 正确下载。
    *   查看终端输出，是否有来自 Camoufox 库 (`launch_server` 调用) 的具体错误信息，如 "Server process terminated unexpectedly"。
    *   确保没有其他 Camoufox 或 Playwright 进程冲突。
*   **粘贴 WebSocket 端点后 `server.py` 连接失败**: 
    *   确认粘贴的 `ws://...` 地址完整且正确。
    *   确认 Camoufox 服务仍在运行 (检查 `launch_camoufox.py` 启动的终端)。
    *   检查防火墙是否阻止了本地 WebSocket 连接。
*   **`server.py` 启动时提示端口 (`2048`) 被占用**: 
    *   使用系统工具 (如 `netstat`, `lsof`) 查找并结束占用该端口的进程，或使用 `--port` 参数指定其他端口启动。
*   **认证失败 (特别是 `headless` 模式)**:
    *   **最常见**: `auth_profiles/active/` 下的 `.json` 文件已过期或无效。
    *   **解决**: 删除 `active` 下的文件，重新运行 `python launch_camoufox.py --debug` 生成新的认证文件，并将其移动到 `active` 目录。
    *   确认 `active` 目录下只有一个 `.json` 文件。
    *   检查 `server.py` 日志，看是否明确提到登录重定向。
*   **客户端 (如 Open WebUI) 无法连接**: 
    *   确认 API 基础 URL 配置正确 (`http://<服务器IP或localhost>:端口/v1`)。
    *   检查 `server.py` 日志是否有错误。
    *   确保防火墙允许从客户端访问服务器的端口。
*   **API 请求返回 5xx / 499 错误**: 
    *   **503 Service Unavailable**: `server.py` 未完全就绪。
    *   **504 Gateway Timeout**: AI Studio 响应慢或处理超时。
    *   **502 Bad Gateway**: AI Studio 页面返回错误。检查 `errors_py/` 快照。
    *   **500 Internal Server Error**: `server.py` 内部错误。检查日志和 `errors_py/` 快照。
    *   **499 Client Closed Request**: 客户端提前断开连接。
*   **AI 回复不完整/格式错误**: 
    *   AI Studio Web UI 输出不稳定。检查 `errors_py/` 快照。
*   **自动清空上下文失败**:
    *   检查 `server.py` 日志中的警告。
    *   很可能是 AI Studio 页面更新导致 `server.py` 中的 CSS 选择器失效。检查 `errors_py/` 快照，对比实际页面元素更新 `server.py` 中的选择器常量。
    *   也可能是网络慢导致验证超时，可尝试在 `server.py` 中增加 `CLEAR_CHAT_VERIFY_TIMEOUT_MS` 的值。

## 🦊 关于 Camoufox

本项目使用 [Camoufox](https://camoufox.com/) 来提供具有增强反指纹检测能力的浏览器实例。

*   **核心目标**: 模拟真实用户流量，避免被网站识别为自动化脚本或机器人。
*   **实现方式**: Camoufox 基于 Firefox，通过修改浏览器底层 C++ 实现来伪装设备指纹（如屏幕、操作系统、WebGL、字体等），而不是通过容易被检测到的 JavaScript 注入。
*   **Playwright 兼容**: Camoufox 提供了与 Playwright 兼容的接口，使得现有的 Playwright 代码（如本项目）可以相对容易地迁移过来。
*   **Python 接口**: Camoufox 提供了 Python 包，可以通过 `camoufox.server.launch_server()` (如 `launch_camoufox.py` 中所用) 启动其服务，并通过 WebSocket 连接进行控制。
*   **指纹轮换**: Camoufox 利用 BrowserForge 等库来模拟真实世界的设备特征分布。

使用 Camoufox 的主要目的是提高与 AI Studio 网页交互时的隐蔽性，减少被检测或限制的可能性。但请注意，没有任何反指纹技术是绝对完美的。

## 📄 关于 `fetch_camoufox_data.py`

项目根目录下包含一个名为 `fetch_camoufox_data.py` 的辅助脚本。

*   **用途**: 此脚本的唯一目的是在运行 `camoufox fetch` 命令失败时，尝试**禁用 SSL 证书验证**来强制下载 Camoufox 所需的浏览器文件和数据。这有时可以解决因本地网络环境或代理服务器的 SSL 证书问题导致的下载失败。
*   **风险**: **禁用 SSL 验证会带来安全风险！** 它意味着你的网络连接不再验证服务器的身份，可能使你受到中间人攻击。**请仅在完全了解风险并确认你的网络环境可信的情况下，才考虑运行此脚本。**
*   **用法**: 如果 `camoufox fetch` 失败，可以尝试在项目根目录运行 `python fetch_camoufox_data.py`。脚本执行完毕后，SSL 验证将在下次正常运行 Python 时恢复。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 License

[MIT](LICENSE)

## 🚀 未来计划 / Roadmap

以下是一些计划中的改进方向：

*   **Docker 支持**: 提供官方的 `Dockerfile` 以及 Docker Compose 配置，简化容器化部署流程（一定会的）。
*   **云服务器部署指南**: 提供更详细的在主流云平台（如 AWS, GCP, Azure）上部署和管理服务的指南，包括使用 `systemd` 或 `supervisor` 进行进程管理（实用性存疑，待考究）。
*   **参数自动化 (探索性)**: 研究通过 Playwright 自动化操作 AI Studio 页面的参数设置区域（如模型选择、温度、Top-K/P 等）的可行性，尝试允许用户通过 API 请求来动态配置这些参数。这依赖于 AI Studio 页面的稳定性和可自动化程度。
*   **认证更新流程优化**: 探索更便捷的认证文件更新机制，减少手动操作（一定会的）。

## 控制日志输出 (Python 版本)

服务器支持通过命令行参数或环境变量控制日志输出级别。这对于调试和减少日志噪音非常有用。

### 命令行参数 (传递给 `server.py`)

```bash
# 通过 launch_camoufox.py 传递给 server.py (注意 '--' 的使用可能需要，取决于参数解析库)
# 示例：启动时设置端口和启用调试日志 (具体传递方式需查阅 launch_camoufox.py)
# 或者直接运行 server.py 时使用：
python server.py --port 3000 --debug-logs
python server.py --trace-logs
python server.py --log-interval 50 --log-time-interval 5.0
python server.py --host 0.0.0.0
```

### 环境变量

也可以通过环境变量控制日志：

```bash
# Linux/macOS
export DEBUG_LOGS_ENABLED=true
export TRACE_LOGS_ENABLED=true
export LOG_INTERVAL=50
export LOG_TIME_INTERVAL=5.0
python launch_camoufox.py # server.py 会读取这些变量

# Windows (cmd)
set DEBUG_LOGS_ENABLED=true
set TRACE_LOGS_ENABLED=true
set LOG_INTERVAL=50
set LOG_TIME_INTERVAL=5.0
python launch_camoufox.py

# Windows (PowerShell)
$env:DEBUG_LOGS_ENABLED="true"
$env:TRACE_LOGS_ENABLED="true"
$env:LOG_INTERVAL="50"
$env:LOG_TIME_INTERVAL="5.0"
python launch_camoufox.py
```
