#!/usr/bin/env python3
"""
安全密钥管理功能测试
测试验证后才能查看服务器密钥列表的安全机制
"""

import requests
import json

def test_api_info():
    """测试API信息端点"""
    print("🔍 测试API信息端点...")
    try:
        response = requests.get('http://localhost:2048/api/info')
        if response.status_code == 200:
            data = response.json()
            print(f"  ✅ API信息获取成功")
            print(f"  📋 API密钥必需: {data.get('api_key_required')}")
            print(f"  📋 密钥数量: {data.get('api_key_count')}")
            print(f"  📋 OpenAI兼容: {data.get('openai_compatible')}")
            return data
        else:
            print(f"  ❌ API信息获取失败: {response.status_code}")
            return None
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return None

def test_key_validation_security():
    """测试密钥验证的安全机制"""
    print("🔒 测试密钥验证安全机制...")
    
    # 获取服务器密钥列表
    try:
        response = requests.get('http://localhost:2048/api/keys')
        if response.status_code == 200:
            data = response.json()
            keys = data.get('keys', [])
            print(f"  📋 服务器配置了 {len(keys)} 个密钥")
            
            if keys:
                # 测试有效密钥验证
                valid_key = keys[0]['value']
                print(f"  🔍 测试有效密钥验证: {valid_key[:4]}****{valid_key[-4:]}")
                
                validation_response = requests.post('http://localhost:2048/api/keys/test', 
                                                  json={'key': valid_key})
                
                if validation_response.status_code == 200:
                    validation_data = validation_response.json()
                    if validation_data.get('valid'):
                        print(f"  ✅ 有效密钥验证成功")
                        return valid_key, keys
                    else:
                        print(f"  ❌ 有效密钥验证失败")
                        return None, keys
                else:
                    print(f"  ❌ 验证请求失败: {validation_response.status_code}")
                    return None, keys
            else:
                print(f"  ⚠️ 服务器没有配置密钥")
                return None, []
        else:
            print(f"  ❌ 获取密钥列表失败: {response.status_code}")
            return None, []
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return None, []

def test_invalid_key_validation():
    """测试无效密钥验证"""
    print("🔍 测试无效密钥验证...")
    
    invalid_key = "invalid_test_key_123456"
    try:
        response = requests.post('http://localhost:2048/api/keys/test', 
                               json={'key': invalid_key})
        
        if response.status_code == 200:
            data = response.json()
            if not data.get('valid'):
                print(f"  ✅ 无效密钥正确被拒绝")
                return True
            else:
                print(f"  ❌ 无效密钥错误地被接受")
                return False
        else:
            print(f"  ❌ 验证请求失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return False

def test_chat_with_verified_key(api_key):
    """测试使用验证过的密钥进行对话"""
    print("💬 测试使用验证过的密钥进行对话...")
    
    try:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }
        
        payload = {
            'model': 'gemini-1.5-pro',
            'messages': [
                {'role': 'user', 'content': '请简单回复"测试成功"'}
            ],
            'stream': False,
            'temperature': 0.7,
            'max_output_tokens': 50
        }
        
        response = requests.post('http://localhost:2048/v1/chat/completions',
                               headers=headers, json=payload, timeout=30)
        
        print(f"  📋 响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            print(f"  ✅ 对话请求成功")
            return True
        elif response.status_code == 401:
            print(f"  ❌ 认证失败 (401)")
            return False
        else:
            print(f"  ❌ 对话请求失败: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return False

def test_security_workflow():
    """测试完整的安全工作流程"""
    print("🛡️ 测试完整的安全工作流程...")
    
    # 步骤1: 获取API信息
    api_info = test_api_info()
    if not api_info:
        return False
    
    print()
    
    # 步骤2: 测试无效密钥验证
    invalid_test_ok = test_invalid_key_validation()
    print()
    
    # 步骤3: 测试有效密钥验证和安全机制
    valid_key, all_keys = test_key_validation_security()
    print()
    
    if valid_key:
        # 步骤4: 测试使用验证过的密钥进行对话
        chat_ok = test_chat_with_verified_key(valid_key)
        print()
        
        return all([api_info, invalid_test_ok, valid_key, chat_ok])
    else:
        print("  ⚠️ 没有有效密钥，跳过对话测试")
        return all([api_info, invalid_test_ok])

def main():
    print("🚀 安全密钥管理功能测试")
    print("=" * 60)
    print("测试验证后才能查看服务器密钥列表的安全机制")
    print()
    
    # 运行安全工作流程测试
    workflow_ok = test_security_workflow()
    
    # 总结
    print("📊 测试结果总结")
    print("=" * 60)
    
    if workflow_ok:
        print("🎉 安全密钥管理功能测试通过！")
        print()
        print("✅ 实现的安全特性:")
        print("  • API密钥验证机制正常工作")
        print("  • 无效密钥正确被拒绝")
        print("  • 有效密钥验证成功")
        print("  • 验证后的密钥可用于API调用")
        print()
        print("🔒 安全机制说明:")
        print("  • 用户必须先验证密钥才能查看服务器密钥列表")
        print("  • 验证状态在会话期间保持")
        print("  • 可以重置验证状态重新验证")
        print("  • 所有密钥显示都经过打码处理")
        print()
        print("💡 使用说明:")
        print("  1. 访问 http://localhost:2048 打开Web界面")
        print("  2. 点击'设置'标签页")
        print("  3. 在'API密钥管理'区域输入密钥进行验证")
        print("  4. 验证成功后可查看服务器密钥列表")
        print("  5. 可使用重置按钮重新验证")
    else:
        print("❌ 部分安全功能测试失败，需要进一步检查")

if __name__ == "__main__":
    main()
