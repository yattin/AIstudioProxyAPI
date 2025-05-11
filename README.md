# AI Studio Proxy Server (Python/Camoufox Version)

[![Star History Chart](https://api.star-history.com/svg?repos=CJackHwang/AIstudioProxyAPI&type=Date)](https://www.star-history.com/#CJackHwang/AIstudioProxyAPI&Date)

**这是当前维护的 Python 版本。不再维护的 Javascript 版本请参见 [`deprecated_javascript_version/README.md`](deprecated_javascript_version/README.md)。**

---

## 目录

1.  [项目概述](#项目概述)
2.  [免责声明](#免责声明)
3.  [核心特性](#核心特性-python-版本)
4.  [重要提示](#重要提示-python-版本)
5.  [快速开始 (推荐流程)](#快速开始-推荐流程)
6.  [详细步骤](#详细步骤)
    *   [1. 先决条件](#1-先决条件)
    *   [2. 安装](#2-安装)
    *   [3. 首次运行与认证 (关键!)](#3-首次运行与认证-关键)
    *   [4. 日常运行 (推荐: 使用 `start.py`)](#4-日常运行-推荐-使用-startpy)
    *   [5. API 使用](#5-api-使用)
    *   [6. Web UI (服务测试)](#6-web-ui-服务测试)
    *   [7. 配置客户端 (以 Open WebUI 为例)](#7-配置客户端-以-open-webui-为例)
    *   [8. (可选) 局域网域名访问 (mDNS)](#8-可选-局域网域名访问-mdns)
7.  [多平台指南](#多平台指南-python-版本)
8.  [故障排除](#故障排除-python-版本)
9.  [关于 Camoufox](#关于-camoufox)
10. [关于 `fetch_camoufox_data.py`](#关于-fetch_camoufox_datapy)
11. [控制日志输出](#控制日志输出-python-版本)
12. [未来计划 / Roadmap](#未来计划--roadmap)
13. [致谢与贡献者](#致谢与贡献者)
14. [贡献](#贡献)
15. [License](#license)

---

## 项目概述

这是一个基于 **Python + FastAPI + Playwright + Camoufox** 的代理服务器，旨在通过模拟 OpenAI API 的方式间接访问 Google AI Studio 网页版。

项目核心优势在于结合了：

*   **FastAPI**: 提供高性能、兼容 OpenAI 标准的 API 接口，现已支持模型参数传递和动态模型切换。
*   **Playwright**: 强大的浏览器自动化库，用于与 AI Studio 页面交互。
*   **Camoufox**: 一个经过修改和优化的 Firefox 浏览器，专注于**反指纹检测和反机器人探测**。它通过底层修改而非 JS 注入来伪装浏览器指纹，旨在模拟真实用户流量，提高自动化操作的隐蔽性和成功率。
*   **请求队列**: 保证请求按顺序处理，提高稳定性。

通过此代理，支持 OpenAI API 的各种客户端（如 Open WebUI, LobeChat, NextChat 等）可以连接并使用 Google AI Studio 的模型。

## 免责声明

使用本项目即表示您已完整阅读、理解并同意本免责声明的全部内容。
本项目通过自动化脚本（Playwright + Camoufox）与 Google AI Studio 网页版进行交互。这种自动化访问网页的方式可能违反 Google AI Studio 或相关 Google 服务的用户协议或服务条款（Terms of Service）。不当使用本项目可能导致您的 Google 账号受到警告、功能限制、暂时或永久封禁等处罚。项目作者及贡献者对此不承担任何责任。
由于本项目依赖于 Google AI Studio 网页的结构和前端代码，Google 随时可能更新或修改其网页，这可能导致本项目的功能失效、不稳定或出现未知错误。项目作者及贡献者无法保证本项目的持续可用性或稳定性。
本项目并非 Google 或 OpenAI 的官方项目或合作项目。它是一个完全独立的第三方工具。项目作者与 Google 和 OpenAI 没有任何关联。
本项目按"现状"（AS IS）提供，不提供任何明示或暗示的保证，包括但不限于适销性、特定用途的适用性及不侵权的保证。您理解并同意自行承担使用本项目可能带来的所有风险。
在任何情况下，项目作者或贡献者均不对因使用或无法使用本项目而产生的任何直接、间接、附带、特殊、惩罚性或后果性的损害承担责任。
使用本项目，即视为您已完全理解并接受本免责声明的全部条款。如果您不同意本声明的任何内容，请立即停止使用本项目。

## 核心特性 (Python 版本)

*   **OpenAI API 兼容**: 提供 `/v1/chat/completions`, `/v1/models`, `/api/info` 端点 (默认端口 `2048`)。现在支持在 `/v1/chat/completions` 请求中传递模型参数（如 `temperature`, `max_output_tokens`, `top_p`, `stop`），代理会尝试在 AI Studio 页面上应用这些参数。
*   **模型切换**: API 请求中的 `model` 字段现在用于在 AI Studio 页面动态切换模型。
*   **流式/非流式响应**: 支持 `stream=true` 和 `stream=false`。
*   **请求队列**: 使用 `asyncio.Queue` 顺序处理请求，提高稳定性。
*   **Camoufox 集成**: 通过 `launch_camoufox.py` 调用 `camoufox` 库启动修改版的 Firefox 实例，利用其反指纹和反检测能力。
*   **简化的启动脚本 (`start.py`)**:
    *   自动检查并尝试清理端口冲突 (默认 `2048`)。
    *   提供交互式代理设置 (可选)。
    *   以后台模式自动启动 `launch_camoufox.py --headless`，并自动处理内部 WebSocket 连接，无需用户手动操作。
*   **认证与调试模式 (`launch_camoufox.py --debug`)**:
    *   提供带界面的浏览器用于首次认证、调试和更新认证文件。
    *   支持保存和加载浏览器认证状态 (`auth_profiles` 目录) 实现免登录。
*   **系统提示词与历史记录**: 支持 `messages` 中的 `system` 角色和多轮对话历史。
*   **自动清空上下文 (条件性)**: 尝试在新对话开始时，如果当前不在 `/new_chat` 页面，则自动清空 AI Studio 页面的聊天记录。
*   **智能响应获取**: 优先尝试通过模拟点击"编辑"或"复制"按钮获取原生响应，提高响应内容的准确性。
*   **Web UI**: 提供 `/` 路径访问一个基于 `index.html` 的现代聊天界面，包含：
    *   聊天视图。
    *   服务器信息视图 (API 信息、健康检查状态，支持刷新)。
    *   模型参数设置面板 (可调系统提示词、温度、最大Token、Top-P、停止序列，并保存设置至浏览器本地存储)。
    *   实时系统日志侧边栏 (通过 WebSocket)。
    *   亮色/暗色主题切换与本地存储。
    *   响应式设计，适配不同屏幕尺寸。
    *   默认系统提示词示例 (Web UI 中，可配置)。
*   **服务端 (`server.py`)**: FastAPI 应用，处理 API 请求，通过 Playwright 控制 Camoufox 浏览器与 AI Studio 交互。
*   **启动器 (`launch_camoufox.py`)**: 负责协调启动 Camoufox 服务（通过内部调用自身）和 FastAPI 服务，并管理它们之间的连接。通常由 `start.py` 在后台调用。
*   **错误快照**: 出错时自动在 `errors_py/` 目录保存截图和 HTML。
*   **日志控制**: 可通过环境变量控制 `server.py` 的日志级别和 `print` 输出重定向行为。
*   **WebSocket 实时日志**: 提供 `/ws/logs` 端点，Web UI 通过此接口显示后端日志。
*   **辅助端点**: 提供 `/health`, `/v1/queue`, `/v1/cancel/{req_id}` 等端点用于监控和管理。

## 重要提示 (Python 版本)

*   **非官方项目**: 依赖 AI Studio Web 界面，可能因页面更新失效。
*   **认证文件是关键**: 无头模式 (通过 `start.py` 启动) **高度依赖**于 `auth_profiles/active/` 下有效的 `.json` 认证文件。**文件可能会过期**，需要定期通过 `launch_camoufox.py --debug` 模式手动运行、登录并保存新的认证文件来替换更新。
*   **模型与参数控制**:
    *   现在可以通过 `/v1/chat/completions` API 请求中的 `model` 字段指定模型，代理将尝试在 AI Studio 页面切换到该模型。请确保指定的模型 ID 是 AI Studio 支持的。
    *   API 请求中的模型参数（如 `temperature`, `max_output_tokens`, `top_p`, `stop`）会被代理接收，并尝试在 AI Studio 页面的对应设置区域进行配置。
    *   Web UI 的"模型设置"面板也提供了对这些参数的图形化配置和保存功能（保存在浏览器本地）。
    *   如果 API 未提供参数，或 Web UI 未设置，AI Studio 页面的当前设置或模型默认值将被使用。
    *   项目根目录下的 `excluded_models.txt` 文件可用于从 `/v1/models` 端点返回的列表中排除特定的模型 ID。每行一个模型 ID。
*   **CSS 选择器依赖**: 页面交互（如获取响应、清空聊天、设置参数等）依赖 `server.py` 中定义的 CSS 选择器。AI Studio 页面更新可能导致这些选择器失效，需要手动更新。
*   **Camoufox 特性**: 利用 Camoufox 增强反指纹能力。了解更多信息请参考 [Camoufox 官方文档](https://camoufox.com/)。
*   **稳定性**: 浏览器自动化本质上不如原生 API 稳定，长时间运行可能需要重启。
*   **AI Studio 限制**: 无法绕过 AI Studio 本身的速率、内容等限制。
*   **端口号**: 默认端口已更改为 `2048`。可在 `start.py` 的配置或 `launch_camoufox.py` 的 `--server-port` 参数中修改。
*   **客户端管理历史，代理不支持 UI 内编辑**: 客户端负责维护完整的聊天记录并将其发送给代理。代理服务器本身不支持在 AI Studio 界面中对历史消息进行编辑或分叉操作；它总是处理客户端发送的完整消息列表，然后将其发送到 AI Studio 页面。

## 快速开始 (推荐流程)

推荐使用 `start.py` 脚本进行日常运行，它简化了无头模式的启动流程。仅在首次设置或认证过期时才需要使用 `launch_camoufox.py --debug`。

1.  **安装依赖 (首次):**
    ```bash
    # 克隆仓库
    git clone https://github.com/CJackHwang/AIstudioProxyAPI && cd AIstudioProxyAPI
    # (推荐) 创建虚拟环境
    python -m venv venv && source venv/bin/activate  # Linux/macOS 或 venv\Scripts\activate on Windows
    # 安装 Camoufox 和依赖
    pip install -U camoufox[geoip] -r requirements.txt
    # 下载 Camoufox 浏览器
    camoufox fetch
    # 安装 Playwright 依赖
    playwright install-deps firefox
    ```

2.  **首次运行获取认证 (使用 Debug 模式):**
    ```bash
    python launch_camoufox.py --debug --server-port 2048
    ```
    *   **重要:** 加上 `--server-port 2048` (或其他你想用的端口) 来指定 FastAPI 监听端口。
    *   会启动一个**带界面的浏览器**。
    *   **关键交互:** **在弹出的浏览器窗口中完成 Google 登录**，直到看到 AI Studio 聊天界面。 (脚本会自动处理浏览器连接，无需用户手动操作)。
    *   回到终端，提示保存认证时输入 `y` 并回车 (文件名可默认)。文件会保存在 `auth_profiles/saved/`。
    *   **将 `auth_profiles/saved/` 下新生成的 `.json` 文件移动到 `auth_profiles/active/` 目录。** 确保 `active` 目录下只有一个 `.json` 文件。
    *   可以按 `Ctrl+C` 停止 `--debug` 模式的运行。

3.  **日常运行 (使用 `start.py` 无头模式):**
    ```bash
    python start.py
    ```
    *   **前提:** 确保 `auth_profiles/active/` 下有有效的 `.json` 文件。
    *   脚本会自动检查端口 `2048` 是否被占用，并尝试清理。
    *   会询问是否启用代理和代理地址 (可选)。
    *   设置完成后，脚本会在后台自动启动 `launch_camoufox.py` (无头模式)，并自动处理内部连接，**无需任何手动交互**。
    *   你可以关闭 `start.py` 的终端窗口，服务将在后台运行。

4.  **使用:**
    *   API 地址: `http://127.0.0.1:2048/v1` (或其他你配置的地址和端口)。
    *   浏览器访问: `http://127.0.0.1:2048/` 可使用 Web UI。
    *   将其配置到 Open WebUI 等客户端中即可使用。

**认证过期后，重复步骤 2 和 3（删除旧的 active 文件，重新 debug 获取并移动新的，然后用 `start.py` 启动）。**

## 使用图形界面启动器 (gui_launcher.py)

除了推荐的 `start.py` 脚本外，本项目还提供了一个基于 Tkinter 的图形用户界面 (GUI) 启动器：[`gui_launcher.py`](gui_launcher.py)。对于喜欢图形化操作的用户，这是一个方便的替代方案。

### 如何启动 GUI

在项目根目录下，确保您的 Python 虚拟环境已激活，然后运行：

```bash
python gui_launcher.py
```

### GUI 功能概览

*   **服务端口配置**: 您可以在 GUI 中指定 FastAPI 服务器监听的端口号 (默认为 2048)。
*   **端口进程管理**:
    *   查询指定端口上当前正在运行的进程。
    *   选择并尝试停止在指定端口上找到的进程。
*   **启动选项**: 提供两种主要的启动模式：
    1.  **启动有头模式 (Debug, 交互式)**:
        *   对应命令行 `python launch_camoufox.py --debug --server-port <端口号>`。
        *   此模式会启动一个带界面的 Camoufox 浏览器和一个新的控制台窗口。
        *   您需要在新的控制台中按照提示进行交互式认证 (例如选择认证文件，或在浏览器中登录 Google 账号)。
        *   启动前，GUI 会询问您是否为此模式配置 HTTP/HTTPS 代理。
        *   此服务由 GUI 管理，关闭 GUI 或点击"停止当前GUI管理的服务"按钮会尝试终止此服务。
    2.  **启动无头模式 (后台独立运行)**:
        *   对应命令行 `python launch_camoufox.py --headless --server-port <端口号>`。
        *   服务将在后台以无头模式独立运行，**关闭 GUI 后服务将继续运行**。
        *   此模式通常需要 `auth_profiles/active/` 目录下有预先保存且有效的 `.json` 认证文件。
        *   启动前，GUI 会询问您是否为此模式配置 HTTP/HTTPS 代理。
        *   由于服务独立运行，GUI 中的"停止当前GUI管理的服务"按钮对此模式无效。您需要通过系统工具 (如任务管理器或 `kill` 命令) 或通过查询端口进程后手动停止它。
*   **状态与日志**:
    *   GUI 界面会显示当前服务的状态。
    *   子进程 (如 `launch_camoufox.py`) 的标准输出和标准错误会显示在 GUI 的"输出日志"区域。
*   **多语言支持**: GUI 支持中文和英文切换。

### 使用建议

*   如果您是首次运行或需要更新认证文件，推荐使用 GUI 的"启动有头模式"。
*   对于日常后台运行，并且已确保 `auth_profiles/active/` 下有有效认证，可以使用"启动无头模式"。
*   `gui_launcher.py` 提供了与 `start.py` 和直接运行 `launch_camoufox.py` 类似的功能，但通过图形界面进行操作。
## 详细步骤

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
    # venv\\Scripts\\activate  # Windows
    ```

    *   说明: 第一行命令 `python -m venv venv` 会在当前目录下创建一个名为 `venv` 的子目录，里面包含了 Python 解释器和独立的包安装目录。第二行命令 `source venv/bin/activate` (macOS/Linux) 或 `venv\\Scripts\\activate` (Windows) 会激活这个环境，之后你的终端提示符可能会发生变化 (例如前面加上 `(venv)` )，表示你正处于虚拟环境中。后续的 `pip install` 命令会将库安装到这个 `venv` 目录内。

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
    如果此步骤因 SSL 证书等网络问题失败，可以尝试运行项目中的 `fetch_camoufox_data.py` 脚本 (详见[下方说明](#关于-fetch_camoufox_datapy))。

5.  **安装 Playwright 浏览器依赖 (如果需要)**:
    虽然 Camoufox 使用自己的 Firefox，但首次运行 Playwright 相关命令可能仍需要安装一些基础依赖。
    ```bash
    # 确保 Playwright 库能找到必要的系统依赖
    playwright install-deps firefox
    # 或者 playwright install-deps # 安装所有浏览器的依赖
    ```

### 3. 首次运行与认证 (关键!)

为了避免每次启动都手动登录 AI Studio，你需要先通过 `launch_camoufox.py --debug` 模式运行一次来生成认证文件。

1.  **运行 Debug 模式**:
    ```bash
    python launch_camoufox.py --debug --server-port 2048
    ```
    *   **重要:** 使用 `--server-port <端口号>` (例如 2048) 指定 FastAPI 服务器监听的端口，后续客户端连接需要使用此端口。
    *   脚本会启动 Camoufox（通过内部调用自身），并在终端输出启动信息。
    *   你会看到一个 **带界面的 Firefox 浏览器窗口** 弹出。
    *   **关键交互 1: 浏览器内登录**
        *   如果浏览器自动打开了 AI Studio 聊天界面 (`.../prompts/new_chat`)，说明可能已有登录状态或无需登录。
        *   如果浏览器打开了 Google 登录页面，**请在浏览器窗口中手动完成 Google 登录流程** (输入账号、密码、二次验证等)，直到你成功进入 AI Studio 的聊天界面。
    *   **关键交互 2: 保存认证状态**
        *   当 `server.py` （在 `launch_camoufox.py` 进程内运行）确认页面加载完成且用户已登录后，它会在**终端**提示：`是否要将当前的浏览器认证状态保存到文件？ (y/N):`
        *   输入 `y` 并按回车。
        *   会再次提示输入文件名，你可以直接回车使用默认名称（`auth_state_时间戳.json`）。
        *   认证文件将保存在项目目录下的 `auth_profiles/saved/` 文件夹中。
    *   此时，代理服务已经可以使用了。你可以继续让它运行，或者按 `Ctrl+C` 停止它。

2.  **激活认证文件**:
    *   进入 `auth_profiles/saved/` 目录，找到刚才保存的 `.json` 认证文件。
    *   将这个 `.json` 文件 **移动或复制** 到 `auth_profiles/active/` 目录下。
    *   **重要:** 确保 `auth_profiles/active/` 目录下 **有且仅有一个 `.json` 文件**。无头模式启动时会自动加载此目录下的第一个 `.json` 文件。

**认证文件会过期!** Google 的登录状态不是永久有效的。当无头模式启动失败并报告认证错误或重定向到登录页时，意味着 `active` 目录下的认证文件已失效。你需要：

1.  删除 `active` 目录下的旧文件。
2.  重新执行上面的 **【运行 Debug 模式】** 步骤，生成新的认证文件。
3.  将新生成的 `.json` 文件再次移动到 `active` 目录下。

### 4. 日常运行 (推荐: 使用 `start.py`)

完成首次认证设置后，强烈推荐使用 `start.py` 进行日常运行，它提供了更便捷的无头模式启动体验。

**启动器 (`launch_camoufox.py`) 说明:**

在熟悉 `start.py` 之前，或者当你需要进行配置、测试、调试或更新认证文件时，**推荐优先直接使用 `launch_camoufox.py` 脚本启动**。这是项目的基础启动方式，提供了更详细的控制和日志输出。

*   `launch_camoufox.py` 支持通过命令行参数 (`--headless` 或 `--debug`) 或交互式选择来启动有头（带界面）或无头模式。
*   使用 `launch_camoufox.py --debug` 是生成和更新认证文件的**唯一方式**。
*   通过直接运行 `launch_camoufox.py`，你可以更清晰地看到内部 Camoufox 启动、FastAPI 服务器的启动过程和日志，方便排查初始设置问题。

**只有当你确认使用 `launch_camoufox.py --debug` 一切运行正常（特别是浏览器内的登录和认证保存），并且 `auth_profiles/active/` 目录下有有效的认证文件后，才推荐使用下面的 `start.py` 作为日常后台运行的标准方式。**

**使用 `start.py` 启动 (便捷后台方式):**

```bash
python start.py
```

*   **前提**: `auth_profiles/active/` 目录下**必须**存在一个有效的 `.json` 认证文件。
*   **自动端口检查**: 脚本会检查默认端口 `2048` 是否被占用。如果被占用，会尝试识别占用进程并提示用户是否尝试自动终止它们。
*   **代理设置 (可选)**: 脚本会交互式地询问你是否需要为 Camoufox 浏览器设置 HTTP/HTTPS 代理，并允许你输入代理地址。这对需要通过代理访问 Google 服务的用户很有用。
*   **后台启动**: 设置完成后，脚本会在**后台**自动启动 `launch_camoufox.py` (强制使用 `--headless` 模式)，并自动处理内部连接和进程分离。**无需任何手动交互。**
*   **退出启动器**: `start.py` 成功启动后台服务后，会提示你可以安全地关闭该终端窗口。主服务 (`launch_camoufox.py` 及其管理的 `server.py`) 会在后台继续运行。

**如果你需要查看详细日志或进行调试，或者需要手动控制启动过程（例如更新认证），仍然可以使用:**
```bash
# 运行 Debug 模式 (浏览器内交互，脚本自动处理内部连接)
python launch_camoufox.py --debug --server-port 2048
```

### 5. API 使用

代理服务器默认监听在 `http://127.0.0.1:2048`。端口可以在 `start.py` 脚本内部的配置或 `launch_camoufox.py` 的 `--server-port` 参数中修改。

*   **聊天接口**: `POST /v1/chat/completions`
    *   请求体与 OpenAI API 兼容，需要 `messages` 数组。
    *   `model` 字段现在用于指定目标模型，代理会尝试在 AI Studio 页面切换到该模型。如果为空或为代理的默认模型名，则使用 AI Studio 当前激活的模型。
    *   `stream` 字段控制流式 (`true`) 或非流式 (`false`) 输出。
    *   现在支持 `temperature`, `max_output_tokens` (在 `server.py` 中可能被命名为 `max_tokens` 或类似，具体需核实 `ChatCompletionRequest` 定义), `top_p`, `stop` 等参数，代理会尝试在 AI Studio 页面上应用它们。
    *   **示例 (curl, 非流式, 带参数)**:
        ```bash
        curl -X POST http://127.0.0.1:2048/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gemini-1.5-pro-latest", # 尝试切换到指定模型
          "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "What is the capital of France?"}
          ],
          "stream": false,
          "temperature": 0.7,
          "max_output_tokens": 150
        }'
        ```
    *   **示例 (curl, 流式, 带参数)**:
        ```bash
        curl -X POST http://127.0.0.1:2048/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gemini-pro", # 尝试切换到指定模型
          "messages": [
            {"role": "user", "content": "Write a short story about a cat."}
          ],
          "stream": true,
          "temperature": 0.9,
          "top_p": 0.95
        }' --no-buffer
        ```
    *   **示例 (Python `requests`)**:
        ```python
        import requests
        import json

        API_URL = "http://127.0.0.1:2048/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        data = {
            "model": "gemini-1.5-flash-latest", # 尝试切换到指定模型
            "messages": [
                {"role": "user", "content": "Translate 'hello' to Spanish."}
            ],
            "stream": False, # or True for streaming
            "temperature": 0.5,
            # "max_output_tokens": 100, # 确保字段名与 server.py 中 ChatCompletionRequest 一致
            # "top_p": 0.9,
            # "stop": ["\n\nHuman:"]
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
                    elif decoded_line.startswith('data: {'): # Handle potential error JSON
                        try:
                            error_data = json.loads(decoded_line[len('data: '):])
                            if 'error' in error_data:
                                print(f"\nError from server: {error_data['error']}")
                                break
                        except json.JSONDecodeError:
                             print(f"\nError decoding error JSON: {decoded_line}")
        else:
            if response.status_code == 200:
                print(json.dumps(response.json(), indent=2))
            else:
                print(f"Error: {response.status_code}\n{response.text}")
        ```
*   **模型列表**: `GET /v1/models`
    *   返回 AI Studio 页面上检测到的可用模型列表，以及一个代理本身的默认模型条目。
    *   现在会尝试从 AI Studio 动态获取模型列表。如果获取失败，会返回一个后备模型。
    *   支持 `excluded_models.txt` 文件，用于从列表中排除特定的模型ID。
*   **API 信息**: `GET /api/info`
    *   返回 API 配置信息，如基础 URL 和模型名称。
*   **健康检查**: `GET /health`
    *   返回服务器运行状态（Playwright, 浏览器连接, 页面状态, Worker 状态, 队列长度）。
*   **队列状态**: `GET /v1/queue`
    *   返回当前请求队列的详细信息。
*   **取消请求**: `POST /v1/cancel/{req_id}`
    *   尝试取消仍在队列中等待处理的请求。

### 6. Web UI (服务测试)

本项目提供了一个简单的 Web 用户界面 (`index.html`)，用于快速测试代理的基本功能和查看状态。

*   **访问**: 在浏览器中打开服务器的根地址，默认为 `http://127.0.0.1:2048/`。
*   **功能**:
    *   **聊天界面**: 一个基本的聊天窗口，可以发送消息并接收来自 AI Studio 的回复。支持 Markdown 格式化和代码块高亮。Web UI 默认使用一个特定的角色扮演系统提示词（关于"丁真"），用户可以在"模型设置"中查看和修改此提示词。
    *   **服务器信息**: 切换到 "服务器信息" 标签页可以查看：
        *   API 调用信息（如 Base URL、模型名称）。
        *   服务健康检查 (`/health` 端点) 的详细状态。
        *   提供 "刷新" 按钮手动更新此信息。
    *   **模型设置**: 新增的 "模型设置" 标签页允许用户配置并保存（至浏览器本地存储）以下参数：
        *   **系统提示词 (System Prompt)**: 自定义指导模型的行为和角色。
        *   **温度 (Temperature)**: 控制生成文本的随机性。
        *   **最大输出Token (Max Output Tokens)**: 限制模型单次回复的长度。
        *   **Top-P**: 控制核心采样的概率阈值。
        *   **停止序列 (Stop Sequences)**: 指定一个或多个序列，当模型生成这些序列时将停止输出。
        *   提供"保存设置"和"重置为默认值"按钮。
    *   **模型选择器**: 在主聊天界面可以选择希望使用的模型，选择后会尝试在 AI Studio 后端进行切换。
    *   **系统日志**: 右侧有一个可展开/收起的侧边栏，通过 WebSocket (`/ws/logs`) 实时显示 `server.py` 的后端日志（需要日志系统配置正确）。包含日志级别、时间戳和消息内容，以及一个清理日志的按钮。
    *   **主题切换**: 右上角提供 "浅色"/"深色" 按钮，用于切换界面主题，偏好设置会保存在浏览器本地存储中。
    *   **响应式设计**: 界面会根据屏幕大小自动调整布局。

**用途**: 这个 Web UI 主要用于简单聊天、开发调试、快速验证代理是否正常工作、监控服务器状态以及方便地调整和测试模型参数。

### 7. 配置客户端 (以 Open WebUI 为例)

1.  打开 Open WebUI。
2.  进入 "设置" -> "连接"。
3.  在 "模型" 部分，点击 "添加模型"。
4.  **模型名称**: 输入你想要的名字，例如 `aistudio-gemini-py`。
5.  **API 基础 URL**: 输入代理服务器的地址，例如 `http://127.0.0.1:2048/v1` (如果服务器在另一台机器，用其 IP 替换 `127.0.0.1`，并确保端口可访问)。
6.  **API 密钥**: 留空或输入任意字符 (服务器不验证)。
7.  保存设置。
8.  现在，你应该可以在 Open WebUI 中选择你在第一步中配置的模型名称并开始聊天了。如果之前配置过，可能需要刷新或重新选择模型以应用新的 API 基地址。

### 8. (可选) 局域网域名访问 (mDNS)

项目包含一个辅助脚本 `mdns_publisher.py`，它使用 mDNS (Bonjour/ZeroConf) 在你的局域网内广播此代理服务。这允许你和其他局域网内的设备通过一个更友好的 `.local` 域名（例如 `http://chatui.local:2048`）来访问服务，而无需记住或查找服务器的 IP 地址。但此脚本未经验证是否可以正常使用。

**用途:**

*   当你希望在手机、平板或其他电脑上方便地访问运行在 Mac/PC 上的代理服务时。
*   避免因 IP 地址变化而需要更新客户端配置。

**如何使用:**

1.  **安装依赖:** 此脚本需要额外的库。在你的虚拟环境中运行：
    ```bash
    pip install zeroconf netifaces
    ```
2.  **运行脚本:** 你需要**同时运行**主代理服务 (通过 `start.py` 或 `launch_camoufox.py` 启动，确保 `server.py` 监听在 `0.0.0.0` 和指定端口，如 2048) 和 `mdns_publisher.py`。
    在**另一个终端**窗口，激活虚拟环境后，运行：
    ```bash
    python mdns_publisher.py
    ```
    *   默认广播的域名是 `chatui.local`，广播的端口是脚本内 `PORT` 变量定义的端口 (当前为 2048)。
    *   你可以使用 `--name yourname` 参数来修改广播的域名前缀，例如 `python mdns_publisher.py --name mychat` 将广播 `mychat.local`。
    *   此脚本**不需要** `sudo` 权限运行。
3.  **访问服务:** 在局域网内的其他支持 mDNS 的设备上，通过浏览器或客户端配置访问 `http://<你设置的域名>.local:<端口>`，例如 `http://chatui.local:2048` 或 API 地址 `http://chatui.local:2048/v1`。

**注意:**

*   确保你的防火墙允许 UDP 端口 5353 (mDNS) 的通信。
*   客户端设备需要支持 mDNS 才能解析 `.local` 域名 (大多数现代操作系统默认支持)。
*   此脚本广播的是 `server.py` 实际监听的端口 (由 `mdns_publisher.py` 中的 `PORT` 变量决定)。确保这个端口与 `server.py` 实际使用的端口一致。

## 多平台指南 (Python 版本)

*   **macOS / Linux**:
    *   通常安装过程比较顺利。确保 Python 和 pip 已正确安装并配置在系统 PATH 中。
    *   使用 `source venv/bin/activate` 激活虚拟环境。
    *   `playwright install-deps firefox` 可能需要系统包管理器（如 `apt` for Debian/Ubuntu, `yum`/`dnf` for Fedora/CentOS, `brew` for macOS）安装一些依赖库。如果命令失败，请仔细阅读错误输出，根据提示安装缺失的系统包。有时可能需要 `sudo` 权限执行 `playwright install-deps`。
    *   防火墙通常不会阻止本地访问，但如果从其他机器访问，需要确保端口（默认 2048）是开放的。

*   **Windows**:
    *   **原生 Windows**:
        *   确保在安装 Python 时勾选了 "Add Python to PATH" 选项。
        *   使用 `venv\\Scripts\\activate` 激活虚拟环境。
        *   Windows 防火墙可能会阻止 Uvicorn/FastAPI 监听端口。如果遇到连接问题（特别是从其他设备访问时），请检查 Windows 防火墙设置，允许 Python 或特定端口的入站连接。
        *   `playwright install-deps` 命令在原生 Windows 上作用有限（主要用于 Linux），但运行 `camoufox fetch` (内部会调用 Playwright) 会确保下载正确的浏览器。
        *   **推荐使用 `start.py` 启动**，它会自动处理后台进程。如果直接运行 `launch_camoufox.py`，终端窗口需要保持打开。
    *   **WSL (Windows Subsystem for Linux)**:
        *   **推荐**: 对于习惯 Linux 环境的用户，WSL (特别是 WSL2) 提供了更好的体验。
        *   在 WSL 环境内，按照 **macOS / Linux** 的步骤进行安装和依赖处理 (通常使用 `apt` 命令)。
        *   需要注意的是网络访问：
            *   从 Windows 访问 WSL 中运行的服务：通常可以通过 `localhost` 或 WSL 分配的 IP 地址访问。
            *   从局域网其他设备访问 WSL 中运行的服务：可能需要配置 Windows 防火墙以及 WSL 的网络设置（WSL2 的网络通常更容易从外部访问）。
        *   所有命令（`git clone`, `pip install`, `camoufox fetch`, `python start.py` 或 `python launch_camoufox.py` 等）都应在 WSL 终端内执行。
        *   在 WSL 中运行 `--debug` 模式：`launch_camoufox.py --debug` 会尝试启动 Camoufox。如果你的 WSL 配置了 GUI 应用支持（如 WSLg 或第三方 X Server），可以看到浏览器界面。否则，它可能无法显示界面，但服务本身仍会尝试启动。无头模式 (`start.py`) 不受影响。

## 故障排除 (Python 版本)

*   **`pip install camoufox[geoip]` 失败**:
    *   可能是网络问题或缺少编译环境。尝试不带 `[geoip]` 安装 (`pip install camoufox`)。
*   **`camoufox fetch` 失败**:
    *   常见原因是网络问题或 SSL 证书验证失败。
    *   可以尝试运行 `python fetch_camoufox_data.py` 脚本，它会尝试禁用 SSL 验证来下载 (有安全风险，仅在确认网络环境可信时使用)。
*   **`playwright install-deps` 失败**:
    *   通常是 Linux 系统缺少必要的库。仔细阅读错误信息，根据提示安装缺失的系统包 (如 `libgbm-dev`, `libnss3` 等)。
*   **`launch_camoufox.py` 启动报错**:
    *   检查 Camoufox 是否已通过 `camoufox fetch` 正确下载。
    *   查看终端输出，是否有来自 Camoufox 库 (`launch_server` 调用) 或内部 Camoufox 进程的具体错误信息。
    *   确保没有其他 Camoufox 或 Playwright 进程冲突。
*   **`server.py` 启动时提示端口 (`2048`) 被占用**:
    *   如果使用 `start.py` 启动，它会尝试自动检测并提示终止占用进程。
    *   如果自动终止失败或未使用 `start.py`，请使用系统工具 (如 `netstat -ano | findstr 2048` on Windows, `lsof -i :2048` on Linux/macOS) 查找并结束占用该端口的进程，或修改 `start.py` 的配置或 `launch_camoufox.py` 的 `--server-port` 参数。
    *   Web UI 中的模型参数设置（如温度、系统提示词等）未生效或行为异常：
        *   这可能是由于 AI Studio 页面的 `localStorage` 中的 `isAdvancedOpen` 未正确设置为 `true`，或者 `areToolsOpen` 干扰了参数面板。
        *   代理服务在启动时会尝试自动修正这些 `localStorage` 设置并重新加载页面。如果问题依旧，可以尝试清除浏览器缓存和 `localStorage` 后重启代理服务和浏览器，或在AI Studio页面手动打开高级设置面板再尝试。
*   **认证失败 (特别是无头模式)**:
    *   **最常见**: `auth_profiles/active/` 下的 `.json` 文件已过期或无效。
    *   **解决**: 删除 `active` 下的文件，重新运行 `python launch_camoufox.py --debug --server-port 2048` 生成新的认证文件，并将其移动到 `active` 目录。
    *   确认 `active` 目录下只有一个 `.json` 文件。
    *   检查 `server.py` 日志（可以通过 Web UI 的日志侧边栏查看，或 `logs/app.log`），看是否明确提到登录重定向。
*   **客户端 (如 Open WebUI) 无法连接**:
    *   确认 API 基础 URL 配置正确 (`http://<服务器IP或localhost>:端口/v1`，默认端口 2048)。
    *   检查 `server.py` 日志是否有错误（Web UI 可看，或 `logs/app.log`）。
*   **API 请求返回 5xx / 499 错误**:
    *   **503 Service Unavailable**: `server.py` 未完全就绪 (例如正在初始化，或 Worker 未运行)。
    *   **504 Gateway Timeout**: AI Studio 响应慢或处理超时。
    *   **502 Bad Gateway**: AI Studio 页面返回错误。检查 `errors_py/` 快照。
    *   **500 Internal Server Error**: `server.py` 内部错误。检查日志和 `errors_py/` 快照。
    *   **499 Client Closed Request**: 客户端提前断开连接。
*   **AI 回复不完整/格式错误**:
    *   AI Studio Web UI 输出不稳定。检查 `errors_py/` 快照。
*   **自动清空上下文失败**:
    *   检查 `server.py` 日志中的警告。
    *   很可能是 AI Studio 页面更新导致 `server.py` 中的 CSS 选择器失效。检查 `errors_py/` 快照，对比实际页面元素更新 `server.py` 中的选择器常量。
    *   也可能是网络慢导致验证超时。
*   **AI Studio 页面更新导致功能失效**:
    *   如果 AI Studio 更新了网页结构或 CSS 类名，依赖这些元素的交互（如清空聊天、获取响应）可能会失败。
    *   检查 `server.py` 日志中的警告或错误。
    *   检查 `errors_py/` 目录下的错误快照 (截图和 HTML)，对比实际页面元素，更新 `server.py` 中对应的 CSS 选择器常量。
*   **`start.py` 启动后服务未运行或立即退出**:
    *   检查 `auth_profiles/active/` 是否有有效的认证文件。这是最常见的原因。
    *   尝试直接运行 `python launch_camoufox.py --headless --server-port 2048` 查看详细的启动错误日志。
    *   查看项目根目录下的 `logs/launch_app.log` (由 `launch_camoufox.py` 生成) 和 `logs/app.log` (由 `server.py` 生成) 获取详细错误信息。
*   **Web UI 无法显示日志或服务器信息**:
    *   检查浏览器开发者工具 (F12) 的控制台和网络选项卡是否有错误。
    *   确认 WebSocket 连接 (`/ws/logs`) 是否成功建立。
    *   确认 `/health` 和 `/api/info` 端点是否能正常访问并返回数据。

## 关于 Camoufox

本项目使用 [Camoufox](https://camoufox.com/) 来提供具有增强反指纹检测能力的浏览器实例。

*   **核心目标**: 模拟真实用户流量，避免被网站识别为自动化脚本或机器人。
*   **实现方式**: Camoufox 基于 Firefox，通过修改浏览器底层 C++ 实现来伪装设备指纹（如屏幕、操作系统、WebGL、字体等），而不是通过容易被检测到的 JavaScript 注入。
*   **Playwright 兼容**: Camoufox 提供了与 Playwright 兼容的接口。
*   **Python 接口**: Camoufox 提供了 Python 包，可以通过 `camoufox.server.launch_server()` (如 `launch_camoufox.py` 中所用，通过其 `--internal-launch` 模式间接调用) 启动其服务，并通过 WebSocket 连接进行控制。

使用 Camoufox 的主要目的是提高与 AI Studio 网页交互时的隐蔽性，减少被检测或限制的可能性。但请注意，没有任何反指纹技术是绝对完美的。

## 关于 `fetch_camoufox_data.py`

项目根目录下包含一个名为 `fetch_camoufox_data.py` 的辅助脚本。

*   **用途**: 此脚本的唯一目的是在运行 `camoufox fetch` 命令失败时，尝试**禁用 SSL 证书验证**来强制下载 Camoufox 所需的浏览器文件和数据。这有时可以解决因本地网络环境或代理服务器的 SSL 证书问题导致的下载失败。
*   **风险**: **禁用 SSL 验证会带来安全风险！** 它意味着你的网络连接不再验证服务器的身份，可能使你受到中间人攻击。**请仅在完全了解风险并确认你的网络环境可信的情况下，才考虑运行此脚本。**
*   **用法**: 如果 `camoufox fetch` 失败，可以尝试在项目根目录运行 `python fetch_camoufox_data.py`。脚本执行完毕后，SSL 验证将在下次正常运行 Python 时恢复。

## 控制日志输出 (Python 版本)

可以通过多种方式控制日志的详细程度和行为：

1.  **`launch_camoufox.py` 的日志**:
    *   此脚本负责启动和协调，其日志记录在 `logs/launch_app.log`。
    *   它的日志级别在脚本内部通过 `setup_launcher_logging(log_level=logging.INFO)` 设置，通常为 `INFO`。
    *   它也会捕获并记录其内部启动的 Camoufox 进程（`--internal-launch` 模式）的 `stdout` 和 `stderr`。

2.  **`server.py` (FastAPI 应用) 的日志**:
    *   `server.py` 拥有自己独立的日志系统，记录在 `logs/app.log`。
    *   其行为主要通过**环境变量**控制，这些环境变量由 `launch_camoufox.py` 在启动 `server.py` 之前设置：
        *   **`SERVER_LOG_LEVEL`**: 控制 `server.py` 的主日志记录器 (`AIStudioProxyServer`) 的级别。默认为 `INFO`。可以设置为 `DEBUG`, `WARNING`, `ERROR` 等。
            *   例如，在运行 `start.py` 或 `launch_camoufox.py` **之前** 设置:
                ```bash
                # Linux/macOS
                export SERVER_LOG_LEVEL=DEBUG
                python start.py # 或 python launch_camoufox.py ...

                # Windows (cmd)
                set SERVER_LOG_LEVEL=DEBUG
                python start.py

                # Windows (PowerShell)
                $env:SERVER_LOG_LEVEL="DEBUG"
                python start.py
                ```
        *   **`SERVER_REDIRECT_PRINT`**: 控制 `server.py` 内部的 `print()` 和 `input()` 行为。
            *   如果设置为 `'true'` (默认，当通过 `start.py` 启动，或 `launch_camoufox.py` 以无头模式启动时)，`print()` 输出会被重定向到 `server.py` 的日志系统（文件、WebSocket 和控制台），`input()` 调用可能会出问题或无响应（因此只在无头模式推荐）。
            *   如果设置为 `'false'` (当 `launch_camoufox.py` 以调试模式启动时)，`print()` 会输出到 `launch_camoufox.py` 所在的原始终端，`input()` 也会在该终端等待用户输入。
        *   **`DEBUG_LOGS_ENABLED`**: (布尔值，`true` 或 `false`) 控制 `server.py` 内部一些非常详细的、用于特定功能调试的日志点是否激活。即使 `SERVER_LOG_LEVEL` 不是 `DEBUG`，这些日志点如果被激活且其消息级别达到 `SERVER_LOG_LEVEL`，也会输出。默认为 `false`。
        *   **`TRACE_LOGS_ENABLED`**: (布尔值，`true` 或 `false`) 类似 `DEBUG_LOGS_ENABLED`，用于更深层次的跟踪日志。默认为 `false`。

3.  **环境变量 (`DEBUG_LOGS_ENABLED`, `TRACE_LOGS_ENABLED`) (影响 `server.py` 内部细节日志)**:
    这些环境变量控制 `server.py` 内部某些特定代码块是否输出更详细的调试信息，独立于 `SERVER_LOG_LEVEL`。
    ```bash
    # Linux/macOS
    export DEBUG_LOGS_ENABLED=true
    # export TRACE_LOGS_ENABLED=true # 通常不需要，除非深度调试
    python start.py # 或者 python launch_camoufox.py ...

    # Windows (cmd)
    set DEBUG_LOGS_ENABLED=true
    python start.py

    # Windows (PowerShell)
    $env:DEBUG_LOGS_ENABLED="true"
    python start.py
    ```

4.  **日志文件**:
    *   `logs/app.log`: FastAPI 服务器 (`server.py`) 的详细日志。
    *   `logs/launch_app.log`: 启动器 (`launch_camoufox.py`) 的日志。
    *   文件日志通常包含比终端或 Web UI 更详细的信息。

5.  **Web UI 日志**:
    *   Web UI 右侧边栏实时显示来自 `server.py` 的 `INFO` 及以上级别的日志（通过 WebSocket）。

通过组合使用这些方法，可以根据需要调整日志的详细程度和输出位置。对于日常运行，默认的日志级别通常足够；在排查问题时，可以查看日志文件或按需设置环境变量获取更详细的信息。

## 未来计划 / Roadmap

以下是一些计划中的改进方向：

*   **Docker支持**: 提供官方的 `Dockerfile` 以及 Docker Compose 配置，简化容器化部署流程。
*   **云服务器部署指南**: 提供更详细的在主流云平台（如 AWS, GCP, Azure）上部署和管理服务的指南。
*   **认证更新流程优化**: 探索更便捷的认证文件更新机制，减少手动操作。
*   **MCP兼容性支持**: 增加健壮性提高对MCP的兼容性。

---

## 致谢与贡献者

本项目的诞生与发展离不开以下开发者和贡献者的努力与智慧：

*   **项目发起与主要开发**: @CJackHwang ([https://github.com/CJackHwang](https://github.com/CJackHwang))
*   **重要贡献与功能完善、调试**: @ayuayue ([https://github.com/ayuayue](https://github.com/ayuayue))

同时，感谢所有通过提交 Issue、提供建议、分享使用体验等方式为本项目作出贡献的社区成员！

---

## 贡献

欢迎提交 Issue 和 Pull Request！

## License

[AGPLv3](LICENSE)
