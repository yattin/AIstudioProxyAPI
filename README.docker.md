# Docker 部署指南

本文档提供了如何使用 Docker 部署 AI Studio Proxy API 的详细说明。

## 前提条件

- Docker 已安装在您的 Linux 服务器上
- Docker Compose 已安装（可选，但推荐）
- 已获取 AI Studio 的认证文件

## 快速开始

### 1. 准备认证文件

在使用 Docker 部署前，您需要先获取 AI Studio 的认证文件。请按照以下步骤操作：

1. 在您的开发机器上运行项目的调试模式获取认证文件：
   ```bash
   python launch_camoufox.py --debug
   ```

2. 完成认证后，将生成的认证文件（通常位于 `auth_profiles/active/` 目录下）复制到您的 Linux 服务器上。

### 2. 使用 Docker Compose 部署（推荐）

1. 将项目文件复制到您的 Linux 服务器。

2. 确保您已将认证文件放置在项目目录的 `auth_profiles/active/` 目录下。

3. 运行 Docker Compose：
   ```bash
   docker-compose up -d
   ```

4. 服务将在后台启动，并在端口 2048 上运行。您可以通过以下地址访问：
   - API 地址: `http://your-server-ip:2048/v1`
   - Web UI: `http://your-server-ip:2048/`

### 3. 使用 Docker 命令部署

如果您不想使用 Docker Compose，也可以直接使用 Docker 命令：

1. 构建 Docker 镜像：
   ```bash
   docker build -t aistudio-proxy .
   ```

2. 运行容器：
   ```bash
   docker run -d \
     --name aistudio-proxy \
     -p 2048:2048 \
     -v $(pwd)/auth_profiles:/app/auth_profiles \
     -v $(pwd)/logs:/app/logs \
     -v $(pwd)/errors_py:/app/errors_py \
     aistudio-proxy
   ```

## 配置选项

您可以通过环境变量自定义容器的行为：

| 环境变量 | 描述 | 默认值 |
|----------|------|--------|
| SERVER_LOG_LEVEL | 日志级别 (DEBUG, INFO, WARNING, ERROR) | INFO |
| LAUNCH_MODE | 启动模式 | direct_debug_no_browser |
| SERVER_REDIRECT_PRINT | 是否重定向打印输出 | false |

示例：
```bash
docker run -d \
  --name aistudio-proxy \
  -p 2048:2048 \
  -v $(pwd)/auth_profiles:/app/auth_profiles \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/errors_py:/app/errors_py \
  -e SERVER_LOG_LEVEL=DEBUG \
  aistudio-proxy
```

## 数据卷

容器使用以下数据卷：

- `/app/auth_profiles`: 存储认证文件
- `/app/logs`: 存储日志文件
- `/app/errors_py`: 存储错误截图和 HTML

## 故障排除

1. **容器启动失败，提示缺少认证文件**
   
   确保您已将有效的认证文件放置在 `auth_profiles/active/` 目录下，并且已正确挂载该目录。

2. **无法连接到服务**
   
   检查防火墙设置，确保端口 2048 已开放。

3. **查看容器日志**
   
   ```bash
   docker logs aistudio-proxy
   ```

4. **进入容器进行调试**
   
   ```bash
   docker exec -it aistudio-proxy bash
   ```

## 更新容器

当有新版本可用时，您可以按照以下步骤更新容器：

1. 拉取最新的代码
2. 重新构建镜像
3. 停止并删除旧容器
4. 启动新容器

使用 Docker Compose：
```bash
git pull
docker-compose down
docker-compose up -d --build
```

使用 Docker 命令：
```bash
git pull
docker build -t aistudio-proxy .
docker stop aistudio-proxy
docker rm aistudio-proxy
docker run -d \
  --name aistudio-proxy \
  -p 2048:2048 \
  -v $(pwd)/auth_profiles:/app/auth_profiles \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/errors_py:/app/errors_py \
  aistudio-proxy
```
