# API 使用指南

代理服务器默认监听在 `http://127.0.0.1:2048`。端口可以在 [`launch_camoufox.py`](../launch_camoufox.py) 的 `--server-port` 参数或 [`gui_launcher.py`](../gui_launcher.py) 中修改。

## API 端点

### 聊天接口

**端点**: `POST /v1/chat/completions`

*   请求体与 OpenAI API 兼容，需要 `messages` 数组。
*   `model` 字段现在用于指定目标模型，代理会尝试在 AI Studio 页面切换到该模型。如果为空或为代理的默认模型名，则使用 AI Studio 当前激活的模型。
*   `stream` 字段控制流式 (`true`) 或非流式 (`false`) 输出。
*   现在支持 `temperature`, `max_output_tokens`, `top_p`, `stop` 等参数，代理会尝试在 AI Studio 页面上应用它们。

#### 示例 (curl, 非流式, 带参数)

```bash
curl -X POST http://127.0.0.1:2048/v1/chat/completions \
-H "Content-Type: application/json" \
-d '{
  "model": "gemini-1.5-pro-latest",
  "messages": [
    {"role": "system", "content": "Be concise."},
    {"role": "user", "content": "What is the capital of France?"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_output_tokens": 150,
  "top_p": 0.9,
  "stop": ["\n\nUser:"]
}'
```

#### 示例 (curl, 流式, 带参数)

```bash
curl -X POST http://127.0.0.1:2048/v1/chat/completions \
-H "Content-Type: application/json" \
-d '{
  "model": "gemini-pro",
  "messages": [
    {"role": "user", "content": "Write a short story about a cat."}
  ],
  "stream": true,
  "temperature": 0.9,
  "top_p": 0.95,
  "stop": []
}' --no-buffer
```

#### 示例 (Python requests)

```python
import requests
import json

API_URL = "http://127.0.0.1:2048/v1/chat/completions"
headers = {"Content-Type": "application/json"}
data = {
    "model": "gemini-1.5-flash-latest",
    "messages": [
        {"role": "user", "content": "Translate 'hello' to Spanish."}
    ],
    "stream": False, # or True for streaming
    "temperature": 0.5,
    "max_output_tokens": 100,
    "top_p": 0.9,
    "stop": ["\n\nHuman:"]
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

### 模型列表

**端点**: `GET /v1/models`

*   返回 AI Studio 页面上检测到的可用模型列表，以及一个代理本身的默认模型条目。
*   现在会尝试从 AI Studio 动态获取模型列表。如果获取失败，会返回一个后备模型。
*   支持 [`excluded_models.txt`](../excluded_models.txt) 文件，用于从列表中排除特定的模型ID。

### API 信息

**端点**: `GET /api/info`

*   返回 API 配置信息，如基础 URL 和模型名称。

### 健康检查

**端点**: `GET /health`

*   返回服务器运行状态（Playwright, 浏览器连接, 页面状态, Worker 状态, 队列长度）。

### 队列状态

**端点**: `GET /v1/queue`

*   返回当前请求队列的详细信息。

### 取消请求

**端点**: `POST /v1/cancel/{req_id}`

*   尝试取消仍在队列中等待处理的请求。

## 配置客户端 (以 Open WebUI 为例)

1. 打开 Open WebUI。
2. 进入 "设置" -> "连接"。
3. 在 "模型" 部分，点击 "添加模型"。
4. **模型名称**: 输入你想要的名字，例如 `aistudio-gemini-py`。
5. **API 基础 URL**: 输入代理服务器的地址，例如 `http://127.0.0.1:2048/v1` (如果服务器在另一台机器，用其 IP 替换 `127.0.0.1`，并确保端口可访问)。
6. **API 密钥**: 留空或输入任意字符 (服务器不验证)。
7. 保存设置。
8. 现在，你应该可以在 Open WebUI 中选择你在第一步中配置的模型名称并开始聊天了。如果之前配置过，可能需要刷新或重新选择模型以应用新的 API 基地址。

## 重要提示

### 响应获取与参数控制

*   **响应获取优先级**: 项目现在采用多层响应获取机制：
    1. **集成的流式代理服务 (Stream Proxy)**: 默认通过 [`launch_camoufox.py`](../launch_camoufox.py) 启动时启用，监听在端口 `3120` (可通过 `--stream-port` 修改或设为 `0` 禁用)。此服务直接处理请求，提供最佳性能。
    2. **外部 Helper 服务**: 如果集成的流式代理被禁用，且通过 [`launch_camoufox.py`](../launch_camoufox.py) 的 `--helper <endpoint_url>` 参数提供了 Helper 服务端点，并且存在有效的认证文件 (`auth_profiles/active/*.json`，用于提取 `SAPISID` Cookie)，则会尝试使用此外部 Helper 服务。
    3. **Playwright 页面交互**: 如果以上两种方法均未启用或失败，则回退到传统的 Playwright 方式，通过模拟浏览器操作（编辑/复制按钮）获取响应。

*   API 请求中的 `model` 字段用于在 AI Studio 页面切换模型。请确保模型 ID 有效。

*   API 请求中的模型参数（如 `temperature`, `max_output_tokens`, `top_p`, `stop`）会被代理接收并尝试在 AI Studio 页面应用。这些参数的设置**仅在通过 Playwright 页面交互获取响应时生效**。当使用集成的流式代理或外部 Helper 服务时，这些参数的传递和应用方式取决于这些服务自身的实现，可能与 AI Studio 页面的设置不同步或不完全支持。

*   Web UI 的"模型设置"面板的参数配置也主要影响通过 Playwright 页面交互获取响应的场景。

*   项目根目录下的 [`excluded_models.txt`](../excluded_models.txt) 文件可用于从 `/v1/models` 端点返回的列表中排除特定的模型 ID。

### 客户端管理历史

**客户端管理历史，代理不支持 UI 内编辑**: 客户端负责维护完整的聊天记录并将其发送给代理。代理服务器本身不支持在 AI Studio 界面中对历史消息进行编辑或分叉操作；它总是处理客户端发送的完整消息列表，然后将其发送到 AI Studio 页面。

## 下一步

API 使用配置完成后，请参考：
- [Web UI 使用指南](webui-guide.md)
- [故障排除指南](troubleshooting.md)
- [日志控制指南](logging-control.md)
