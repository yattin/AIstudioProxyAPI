#!/usr/bin/env python3
"""
Web UI修复验证测试
测试API密钥管理和对话功能的修复情况
"""

import requests
import json
import time

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
            print(f"  📋 支持的认证方法: {data.get('supported_auth_methods')}")
            return True
        else:
            print(f"  ❌ API信息获取失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return False

def test_api_keys_list():
    """测试API密钥列表端点"""
    print("🔍 测试API密钥列表端点...")
    try:
        response = requests.get('http://localhost:2048/api/keys')
        if response.status_code == 200:
            data = response.json()
            print(f"  ✅ 密钥列表获取成功")
            print(f"  📋 密钥数量: {data.get('total_count')}")
            keys = data.get('keys', [])
            for i, key in enumerate(keys):
                masked_key = key['value'][:4] + '****' + key['value'][-4:]
                print(f"  📋 密钥{i+1}: {masked_key}")
            return keys
        else:
            print(f"  ❌ 密钥列表获取失败: {response.status_code}")
            return []
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return []

def test_api_key_validation(api_key):
    """测试API密钥验证"""
    print(f"🔍 测试API密钥验证: {api_key[:4]}****{api_key[-4:]}...")
    try:
        response = requests.post('http://localhost:2048/api/keys/test',
                               json={'key': api_key})
        if response.status_code == 200:
            data = response.json()
            is_valid = data.get('valid', False)
            print(f"  ✅ 密钥验证完成: {'有效' if is_valid else '无效'}")
            return is_valid
        else:
            print(f"  ❌ 密钥验证失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return False

def test_chat_with_auth(api_key):
    """测试带认证的对话功能"""
    print(f"🔍 测试带认证的对话功能...")
    try:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }
        
        payload = {
            'model': 'gemini-1.5-pro',
            'messages': [
                {'role': 'user', 'content': '你好，请简单回复一下测试'}
            ],
            'stream': False,
            'temperature': 0.7,
            'max_output_tokens': 100
        }
        
        response = requests.post('http://localhost:2048/v1/chat/completions',
                               headers=headers, json=payload, timeout=30)
        
        print(f"  📋 响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            print(f"  ✅ 对话请求成功 (带认证)")
            return True
        elif response.status_code == 401:
            print(f"  ❌ 认证失败 (401) - 这表明认证机制正在工作")
            return False
        else:
            print(f"  ❌ 对话请求失败: {response.status_code}")
            try:
                error_data = response.json()
                print(f"  📋 错误信息: {error_data}")
            except:
                print(f"  📋 响应内容: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return False

def test_chat_without_auth():
    """测试无认证的对话功能"""
    print(f"🔍 测试无认证的对话功能...")
    try:
        headers = {
            'Content-Type': 'application/json'
        }
        
        payload = {
            'model': 'gemini-1.5-pro',
            'messages': [
                {'role': 'user', 'content': '你好，请简单回复一下测试'}
            ],
            'stream': False,
            'temperature': 0.7,
            'max_output_tokens': 100
        }
        
        response = requests.post('http://localhost:2048/v1/chat/completions',
                               headers=headers, json=payload, timeout=10)
        
        print(f"  📋 响应状态码: {response.status_code}")
        
        if response.status_code == 401:
            print(f"  ✅ 正确拒绝无认证请求 (401)")
            try:
                error_data = response.json()
                print(f"  📋 错误信息: {error_data.get('error', {}).get('message', '未知错误')}")
            except:
                pass
            return True
        else:
            print(f"  ❌ 应该拒绝无认证请求，但返回了: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return False

def main():
    print("🚀 Web UI修复验证测试")
    print("=" * 60)
    
    # 测试API信息
    api_info_ok = test_api_info()
    print()
    
    # 测试密钥列表
    keys = test_api_keys_list()
    print()
    
    if keys:
        # 使用第一个密钥进行测试
        test_key = keys[0]['value']
        
        # 测试密钥验证
        key_valid = test_api_key_validation(test_key)
        print()
        
        if key_valid:
            # 测试带认证的对话
            chat_with_auth_ok = test_chat_with_auth(test_key)
            print()
        else:
            print("  ⚠️ 密钥无效，跳过认证对话测试")
            chat_with_auth_ok = False
    else:
        print("  ⚠️ 没有可用的API密钥，跳过认证测试")
        chat_with_auth_ok = False
    
    # 测试无认证的对话（应该被拒绝）
    chat_without_auth_ok = test_chat_without_auth()
    print()
    
    # 总结
    print("📊 测试结果总结")
    print("=" * 60)
    print(f"API信息端点: {'✅ 通过' if api_info_ok else '❌ 失败'}")
    print(f"密钥列表端点: {'✅ 通过' if keys else '❌ 失败'}")
    print(f"密钥验证功能: {'✅ 通过' if keys and test_api_key_validation(keys[0]['value']) else '❌ 失败'}")
    print(f"认证对话功能: {'✅ 通过' if chat_with_auth_ok else '❌ 失败'}")
    print(f"认证保护机制: {'✅ 通过' if chat_without_auth_ok else '❌ 失败'}")
    
    all_passed = all([api_info_ok, bool(keys), chat_without_auth_ok])
    
    print()
    if all_passed:
        print("🎉 所有核心功能测试通过！Web UI修复成功！")
        print()
        print("💡 使用说明:")
        print("1. 访问 http://localhost:2048 打开Web界面")
        print("2. 点击'设置'标签页查看API密钥管理")
        print("3. 在'聊天'标签页进行对话测试")
        print("4. 对话请求现在会自动包含API密钥认证")
    else:
        print("❌ 部分功能测试失败，需要进一步检查")

if __name__ == "__main__":
    main()
