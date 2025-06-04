# API密钥管理功能说明

## 🎉 功能概述

本项目现已完全支持OpenAI兼容的API密钥管理功能，包括：

- ✅ **100% OpenAI API兼容**
- ✅ **标准认证方式支持**：`Authorization: Bearer <token>`
- ✅ **向后兼容支持**：`X-API-Key: <token>`
- ✅ **Web UI密钥管理界面**
- ✅ **密钥安全存储和管理**
- ✅ **中文界面和错误提示**

## 🔐 认证方式

### 标准OpenAI认证（推荐）
```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### 自定义认证（向后兼容）
```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo", 
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## 🎨 Web UI使用说明

### 1. 访问管理界面
1. 启动服务器
2. 在浏览器中打开 `http://localhost:8000`
3. 点击顶部导航栏的 **"设置"** 标签页
4. 找到 **"API 密钥管理"** 区域

### 2. 添加API密钥
1. 在 **"添加新的API密钥"** 输入框中输入密钥
2. 点击眼睛图标可以显示/隐藏密钥内容
3. 点击 **"添加密钥"** 按钮保存
4. 系统会自动验证密钥格式（至少8个字符）

### 3. 测试API密钥
1. 在输入框中输入要测试的密钥
2. 点击 **"测试密钥"** 按钮
3. 系统会验证密钥是否有效并显示结果

### 4. 管理现有密钥
- **查看密钥**：密钥会以脱敏形式显示（如：`abcd****5678`）
- **测试密钥**：点击密钥右侧的 ✓ 图标
- **删除密钥**：点击密钥右侧的 🗑️ 图标（需要确认）

## 🔧 API端点

### 获取API信息
```
GET /api/info
```
返回当前API配置信息，包括是否需要密钥、支持的认证方式等。

### 获取密钥列表
```
GET /api/keys
```
返回当前配置的所有API密钥（脱敏显示）。

### 添加API密钥
```
POST /api/keys
Content-Type: application/json

{
  "key": "your_new_api_key"
}
```

### 测试API密钥
```
POST /api/keys/test
Content-Type: application/json

{
  "key": "key_to_test"
}
```

### 删除API密钥
```
DELETE /api/keys
Content-Type: application/json

{
  "key": "key_to_delete"
}
```

## 📁 文件存储

API密钥存储在项目根目录的 `key.txt` 文件中：
- 每行一个密钥
- 支持空行和注释（以#开头）
- 文件会自动创建和更新
- 删除密钥时会自动重写文件

## 🛡️ 安全特性

1. **密钥脱敏显示**：Web界面中密钥以 `****` 形式显示
2. **输入验证**：密钥长度至少8个字符
3. **重复检查**：防止添加重复的密钥
4. **实时验证**：添加和测试密钥时进行有效性检查
5. **安全存储**：密钥存储在服务器本地文件中

## 🔄 兼容性

### OpenAI客户端库
```python
import openai

client = openai.OpenAI(
    api_key="your_api_key_here",
    base_url="http://localhost:8000/v1"
)

response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

### 其他HTTP客户端
任何支持标准HTTP请求的客户端都可以使用，只需要：
1. 设置正确的base URL：`http://localhost:8000/v1`
2. 在请求头中包含认证信息
3. 使用标准的OpenAI API格式

## 🚨 注意事项

1. **密钥安全**：请妥善保管API密钥，不要在公共场所暴露
2. **文件权限**：确保 `key.txt` 文件有适当的读写权限
3. **备份建议**：定期备份密钥文件
4. **删除确认**：删除密钥操作不可撤销，请谨慎操作
5. **服务重启**：修改密钥后无需重启服务，会自动生效

## 🐛 故障排除

### 密钥无效
- 检查密钥格式是否正确
- 确认密钥已正确添加到系统中
- 使用测试功能验证密钥有效性

### 认证失败
- 确认使用了正确的认证头格式
- 检查密钥是否包含额外的空格或字符
- 尝试使用不同的认证方式（Bearer vs X-API-Key）

### Web界面问题
- 刷新浏览器页面
- 检查浏览器控制台是否有错误信息
- 确认服务器正常运行

## 📞 技术支持

如果遇到问题，请：
1. 查看服务器日志获取详细错误信息
2. 使用Web界面的系统日志功能
3. 运行测试脚本验证功能状态：`python3 test_simple_api_key.py`

---

**祝您使用愉快！** 🎉
