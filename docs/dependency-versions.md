# 依赖版本说明

本文档详细说明了项目中各个依赖包的版本要求、兼容性和更新建议。

## Python 版本要求

### 推荐配置
- **生产环境**: Python 3.10+ 或 3.11+
- **开发环境**: Python 3.11+ (获得最佳开发体验)
- **最低要求**: Python 3.9

### 版本兼容性矩阵

| Python版本 | 支持状态 | 推荐程度 | 说明 |
|-----------|---------|---------|------|
| 3.8 | ⚠️ 部分支持 | 不推荐 | 部分依赖可能不支持最新特性 |
| 3.9 | ✅ 完全支持 | 可用 | 最低推荐版本，所有功能正常 |
| 3.10 | ✅ 完全支持 | 推荐 | Docker 默认版本，稳定可靠 |
| 3.11 | ✅ 完全支持 | 强烈推荐 | 性能优化，类型提示增强 |
| 3.12 | ✅ 完全支持 | 推荐 | 最新稳定特性支持 |
| 3.13 | ✅ 完全支持 | 可用 | 最新版本，开发环境推荐 |

## 核心依赖版本

### Web 框架相关
```
fastapi==0.115.12
pydantic>=2.7.1,<3.0.0
uvicorn==0.29.0
starlette (FastAPI 依赖)
```

**版本说明**:
- **FastAPI 0.115.12**: 最新稳定版本，包含性能优化和新功能
  - 新增 Query/Header/Cookie 参数模型支持
  - 改进的类型提示和验证
  - 更好的 OpenAPI 文档生成
- **Pydantic 2.7.1+**: 现代数据验证库，使用版本范围确保兼容性
- **Uvicorn 0.29.0**: 高性能 ASGI 服务器

### 浏览器自动化
```
playwright (最新版本)
camoufox[geoip] (最新版本)
```

**版本说明**:
- **Playwright**: 自动安装最新稳定版本
- **Camoufox**: 反指纹检测浏览器，包含地理位置数据

### 网络和安全
```
aiohttp~=3.9.5
requests==2.31.0
cryptography==42.0.5
pyjwt==2.8.0
websockets==12.0
```

**版本说明**:
- **aiohttp 3.9.5+**: 异步HTTP客户端，使用兼容版本范围
- **cryptography 42.0.5**: 加密库，安全性要求
- **websockets 12.0**: WebSocket 支持

### 系统工具
```
python-dotenv==1.0.1
httptools==0.6.1
uvloop (Linux/macOS)
```

**版本说明**:
- **uvloop**: 仅在 Linux/macOS 上安装，提升性能
- **httptools**: HTTP 解析优化

## 依赖更新策略

### 自动更新 (使用 ~ 版本范围)
- `aiohttp~=3.9.5` - 允许补丁版本更新
- `aiosocks~=0.2.6` - 允许补丁版本更新
- `python-socks~=2.7.1` - 允许补丁版本更新

### 固定版本 (使用 == 精确版本)
- 核心框架组件 (FastAPI, Pydantic, Uvicorn)
- 安全相关库 (cryptography, pyjwt)
- 稳定性要求高的组件

### 最新版本 (无版本限制)
- `playwright` - 浏览器自动化，需要最新功能
- `camoufox[geoip]` - 反指纹检测，持续更新

## 版本升级建议

### 已完成的依赖升级
1. **FastAPI**: 0.111.0 → 0.115.12 ✅
   - 新增 Query/Header/Cookie 参数模型功能
   - 改进的类型提示和验证机制
   - 更好的 OpenAPI 文档生成
   - 向后兼容，无破坏性变更

2. **Pydantic**: 固定版本 → 版本范围 ✅
   - 从 `pydantic==2.7.1` 更新为 `pydantic>=2.7.1,<3.0.0`
   - 确保与 FastAPI 0.115.12 的兼容性
   - 允许自动获取补丁版本更新

### 可选的次要依赖更新
- `charset-normalizer`: 3.4.1 → 3.4.2
- `click`: 8.1.8 → 8.2.1
- `frozenlist`: 1.6.0 → 1.6.2

### 升级注意事项
- 在测试环境中先验证兼容性
- 关注 FastAPI 版本更新的 breaking changes
- 定期检查安全漏洞更新

## 环境特定配置

### Docker 环境
- **基础镜像**: `python:3.10-slim-bookworm`
- **系统依赖**: 自动安装浏览器运行时依赖
- **Python版本**: 固定为 3.10 (容器内)

### 开发环境
- **推荐**: Python 3.11+ 
- **虚拟环境**: 强烈推荐使用 venv 或 conda
- **IDE支持**: 配置了 pyrightconfig.json (Python 3.13)

### 生产环境
- **推荐**: Python 3.10 或 3.11
- **稳定性**: 使用固定版本依赖
- **监控**: 定期检查依赖安全更新

## 故障排除

### 常见版本冲突
1. **Python 3.8 兼容性问题**
   - 升级到 Python 3.9+
   - 检查类型提示语法兼容性

2. **依赖版本冲突**
   - 使用虚拟环境隔离
   - 清理 pip 缓存: `pip cache purge`

3. **系统依赖缺失**
   - Linux: 安装 `xvfb` 用于虚拟显示
   - 运行 `playwright install-deps`

### 版本检查命令
```bash
# 检查 Python 版本
python --version

# 检查已安装包版本
pip list

# 检查过时的包
pip list --outdated

# 检查特定包信息
pip show fastapi
```

## 更新日志

### 2025-01-25
- **重要更新**: FastAPI 从 0.111.0 升级到 0.115.12
- **重要更新**: Pydantic 版本策略从固定版本改为版本范围 (>=2.7.1,<3.0.0)
- 更新 Python 版本要求说明 (推荐 3.9+，强烈建议 3.10+)
- 添加详细的依赖版本兼容性矩阵
- 完善 Docker 环境版本说明 (Python 3.10)
- 增加版本升级建议和故障排除指南
- 更新所有相关文档以反映新的依赖版本要求
