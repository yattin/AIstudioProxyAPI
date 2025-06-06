# 安装指南

本文档提供详细的安装步骤和环境配置说明。

## 先决条件

*   **Python**: 3.9 或更高版本 (强烈建议 3.10+ 或 3.11+)。
    *   **推荐版本**: Python 3.10+ 或 3.11+ 以获得最佳性能和兼容性
    *   **最低要求**: Python 3.9 (支持所有当前依赖版本)
    *   **完全支持**: Python 3.9, 3.10, 3.11, 3.12, 3.13
*   **pip**: Python 包管理器 (建议使用最新版本)。
*   **(可选但推荐) Git**: 用于克隆仓库。
*   **Google AI Studio 账号**: 并能正常访问和使用。
*   **`xvfb` (仅当在 Linux 上使用 `--virtual-display` 模式时需要)**: X 虚拟帧缓冲器。
    *   Debian/Ubuntu: `sudo apt-get update && sudo apt-get install -y xvfb`
    *   Fedora: `sudo dnf install -y xorg-x11-server-Xvfb`
    *   其他 Linux 发行版请参考其包管理器文档。

## 安装步骤

### 1. 克隆仓库

```bash
git clone https://github.com/CJackHwang/AIstudioProxyAPI
cd AIstudioProxyAPI
```

### 2. 创建并激活虚拟环境（推荐）

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\\Scripts\\activate  # Windows
```

*   说明: 第一行命令 `python -m venv venv` 会在当前目录下创建一个名为 `venv` 的子目录，里面包含了 Python 解释器和独立的包安装目录。第二行命令 `source venv/bin/activate` (macOS/Linux) 或 `venv\\Scripts\\activate` (Windows) 会激活这个环境，之后你的终端提示符可能会发生变化 (例如前面加上 `(venv)` )，表示你正处于虚拟环境中。后续的 `pip install` 命令会将库安装到这个 `venv` 目录内。

### 3. 安装 Camoufox 和依赖

```bash
# 安装 Camoufox 库 (推荐包含 geoip 数据，特别是使用代理时)
pip install -U camoufox[geoip]

# 安装项目所需的其他 Python 库
pip install -r requirements.txt
```
`requirements.txt` 主要包含 `fastapi`, `uvicorn[standard]`, `playwright`, `pydantic` 等现代化依赖包。

**依赖版本说明**:
- **FastAPI**: 使用 0.115.12 版本，最新稳定版本，支持 Python 3.8+
  - 包含新的参数模型功能和性能优化
  - 改进的类型提示和 OpenAPI 文档生成
- **Pydantic**: 使用 2.7.1+ 版本范围，提供强大的数据验证
- **Uvicorn**: 使用 0.29.0 版本，高性能 ASGI 服务器
- **Playwright**: 最新版本，用于浏览器自动化
- **Camoufox**: 反指纹检测浏览器，包含 geoip 数据

### 4. 下载 Camoufox 浏览器

```bash
# Camoufox 需要下载其修改版的 Firefox
camoufox fetch
```
如果此步骤因 SSL 证书等网络问题失败，可以尝试运行项目中的 [`fetch_camoufox_data.py`](../fetch_camoufox_data.py) 脚本 (详见[故障排除指南](troubleshooting.md))。

### 5. 安装 Playwright 浏览器依赖（如果需要）

虽然 Camoufox 使用自己的 Firefox，但首次运行 Playwright 相关命令可能仍需要安装一些基础依赖。

```bash
# 确保 Playwright 库能找到必要的系统依赖
playwright install-deps firefox
# 或者 playwright install-deps # 安装所有浏览器的依赖
```

## 多平台指南

### macOS / Linux

*   通常安装过程比较顺利。确保 Python 和 pip 已正确安装并配置在系统 PATH 中。
*   使用 `source venv/bin/activate` 激活虚拟环境。
*   `playwright install-deps firefox` 可能需要系统包管理器（如 `apt` for Debian/Ubuntu, `yum`/`dnf` for Fedora/CentOS, `brew` for macOS）安装一些依赖库。如果命令失败，请仔细阅读错误输出，根据提示安装缺失的系统包。有时可能需要 `sudo` 权限执行 `playwright install-deps`。
*   防火墙通常不会阻止本地访问，但如果从其他机器访问，需要确保端口（默认 2048）是开放的。
*   对于Linux 用户，可以考虑使用 `--virtual-display` 标志启动 (需要预先安装 `xvfb`)，它会利用 Xvfb 创建一个虚拟显示环境来运行浏览器，这可能有助于进一步降低被检测的风险和保证网页正常对话。

### Windows

#### 原生 Windows

*   确保在安装 Python 时勾选了 "Add Python to PATH" 选项。
*   使用 `venv\\Scripts\\activate` 激活虚拟环境。
*   Windows 防火墙可能会阻止 Uvicorn/FastAPI 监听端口。如果遇到连接问题（特别是从其他设备访问时），请检查 Windows 防火墙设置，允许 Python 或特定端口的入站连接。
*   `playwright install-deps` 命令在原生 Windows 上作用有限（主要用于 Linux），但运行 `camoufox fetch` (内部会调用 Playwright) 会确保下载正确的浏览器。
*   **推荐使用 [`gui_launcher.py`](../gui_launcher.py) 启动**，它们会自动处理后台进程和用户交互。如果直接运行 [`launch_camoufox.py`](../launch_camoufox.py)，终端窗口需要保持打开。

#### WSL (Windows Subsystem for Linux)

*   **推荐**: 对于习惯 Linux 环境的用户，WSL (特别是 WSL2) 提供了更好的体验。
*   在 WSL 环境内，按照 **macOS / Linux** 的步骤进行安装和依赖处理 (通常使用 `apt` 命令)。
*   需要注意的是网络访问：
    *   从 Windows 访问 WSL 中运行的服务：通常可以通过 `localhost` 或 WSL 分配的 IP 地址访问。
    *   从局域网其他设备访问 WSL 中运行的服务：可能需要配置 Windows 防火墙以及 WSL 的网络设置（WSL2 的网络通常更容易从外部访问）。
*   所有命令（`git clone`, `pip install`, `camoufox fetch`, `python launch_camoufox.py` 等）都应在 WSL 终端内执行。
*   在 WSL 中运行 `--debug` 模式：[`launch_camoufox.py --debug`](../launch_camoufox.py) 会尝试启动 Camoufox。如果你的 WSL 配置了 GUI 应用支持（如 WSLg 或第三方 X Server），可以看到浏览器界面。否则，它可能无法显示界面，但服务本身仍会尝试启动。无头模式 (通过 [`gui_launcher.py`](../gui_launcher.py) 启动) 不受影响。

## 可选：配置API密钥

安装完成后，您可以选择配置API密钥来保护您的服务：

### 创建密钥文件

在项目根目录创建 `key.txt` 文件：

```bash
# 创建密钥文件
touch key.txt

# 添加密钥（每行一个）
echo "your-first-api-key" >> key.txt
echo "your-second-api-key" >> key.txt
```

### 密钥格式要求

- 每行一个密钥
- 至少8个字符
- 支持空行和注释行（以 `#` 开头）
- 使用 UTF-8 编码

### 示例密钥文件

```
# API密钥配置文件
# 每行一个密钥

sk-1234567890abcdef
my-secure-api-key-2024
admin-key-for-testing

# 这是注释行，会被忽略
```

### 安全说明

- **无密钥文件**: 服务不需要认证，任何人都可以访问API
- **有密钥文件**: 所有API请求都需要提供有效的密钥
- **密钥保护**: 请妥善保管密钥文件，不要提交到版本控制系统

## 下一步

安装完成后，请参考：
- [首次运行与认证指南](authentication-setup.md)
- [日常运行指南](daily-usage.md)
- [API使用指南](api-usage.md) - 包含详细的密钥管理说明
- [故障排除指南](troubleshooting.md)
