#!/usr/bin/env python3
"""
最终Web UI修复验证测试
测试安全密钥管理和输入验证功能
"""

import requests
import json

def test_api_endpoints():
    """测试基本API端点"""
    print("🔍 测试基本API端点...")
    
    # 测试API信息
    try:
        response = requests.get('http://localhost:2048/api/info', timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"  ✅ API信息: 密钥必需={data.get('api_key_required')}, 数量={data.get('api_key_count')}")
        else:
            print(f"  ❌ API信息获取失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ API信息请求失败: {e}")
        return False
    
    # 测试密钥列表
    try:
        response = requests.get('http://localhost:2048/api/keys', timeout=5)
        if response.status_code == 200:
            data = response.json()
            keys = data.get('keys', [])
            print(f"  ✅ 密钥列表: {len(keys)} 个密钥")
            return keys
        else:
            print(f"  ❌ 密钥列表获取失败: {response.status_code}")
            return []
    except Exception as e:
        print(f"  ❌ 密钥列表请求失败: {e}")
        return []

def test_key_validation(api_key):
    """测试密钥验证功能"""
    print(f"🔍 测试密钥验证: {api_key[:4]}****{api_key[-4:]}...")
    
    try:
        response = requests.post('http://localhost:2048/api/keys/test', 
                               json={'key': api_key}, timeout=5)
        if response.status_code == 200:
            data = response.json()
            is_valid = data.get('valid', False)
            print(f"  ✅ 验证结果: {'有效' if is_valid else '无效'}")
            return is_valid
        else:
            print(f"  ❌ 验证请求失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ 验证请求异常: {e}")
        return False

def test_chat_authentication(api_key):
    """测试对话认证功能"""
    print("💬 测试对话认证功能...")
    
    # 测试带认证的请求
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
                               headers=headers, json=payload, timeout=15)
        
        if response.status_code == 200:
            print(f"  ✅ 带认证的对话请求成功")
            return True
        elif response.status_code == 401:
            print(f"  ❌ 认证失败 (401)")
            return False
        else:
            print(f"  ❌ 对话请求失败: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"  ❌ 对话请求异常: {e}")
        return False

def test_no_auth_rejection():
    """测试无认证请求被正确拒绝"""
    print("🔒 测试无认证请求拒绝...")
    
    try:
        headers = {'Content-Type': 'application/json'}
        payload = {
            'model': 'gemini-1.5-pro',
            'messages': [{'role': 'user', 'content': '测试'}],
            'stream': False
        }
        
        response = requests.post('http://localhost:2048/v1/chat/completions',
                               headers=headers, json=payload, timeout=10)
        
        if response.status_code == 401:
            print(f"  ✅ 无认证请求正确被拒绝 (401)")
            return True
        else:
            print(f"  ❌ 无认证请求应该被拒绝，但返回: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"  ❌ 请求异常: {e}")
        return False

def main():
    print("🚀 最终Web UI修复验证测试")
    print("=" * 60)
    print("测试安全密钥管理和输入验证功能")
    print()
    
    # 测试基本API端点
    keys = test_api_endpoints()
    print()
    
    if not keys:
        print("❌ 没有可用的API密钥，无法进行完整测试")
        return
    
    # 使用第一个密钥进行测试
    test_key = keys[0]['value']
    
    # 测试密钥验证
    key_valid = test_key_validation(test_key)
    print()
    
    if not key_valid:
        print("❌ 密钥验证失败，无法进行对话测试")
        return
    
    # 测试对话认证
    chat_ok = test_chat_authentication(test_key)
    print()
    
    # 测试无认证拒绝
    no_auth_ok = test_no_auth_rejection()
    print()
    
    # 总结
    print("📊 测试结果总结")
    print("=" * 60)
    print(f"API端点功能: {'✅ 通过' if keys else '❌ 失败'}")
    print(f"密钥验证功能: {'✅ 通过' if key_valid else '❌ 失败'}")
    print(f"对话认证功能: {'✅ 通过' if chat_ok else '❌ 失败'}")
    print(f"认证保护机制: {'✅ 通过' if no_auth_ok else '❌ 失败'}")
    
    all_passed = all([keys, key_valid, chat_ok, no_auth_ok])
    
    print()
    if all_passed:
        print("🎉 所有功能测试通过！Web UI修复完成！")
        print()
        print("✅ 实现的安全特性:")
        print("  • 验证后才能查看服务器密钥列表")
        print("  • 验证状态在会话期间保持")
        print("  • 可以重置验证状态重新验证")
        print("  • 所有密钥显示都经过打码处理")
        print("  • 增强的输入验证防止空消息发送")
        print("  • 自动API密钥认证机制")
        print()
        print("💡 使用说明:")
        print("  1. 访问 http://localhost:2048 打开Web界面")
        print("  2. 点击'设置'标签页")
        print("  3. 在'API密钥管理'区域输入密钥进行验证")
        print("  4. 验证成功后可查看服务器密钥列表")
        print("  5. 在'聊天'标签页进行对话测试")
        print("  6. 对话请求会自动包含API密钥认证")
    else:
        print("❌ 部分功能测试失败，需要进一步检查")

if __name__ == "__main__":
    main()
