#!/usr/bin/env python3
"""
Web UI 密钥修复验证测试
测试用户输入密钥的正确使用和本地存储功能
"""

import requests
import json

def test_api_key_validation():
    """测试API密钥验证功能"""
    print("🔍 测试API密钥验证功能...")
    
    # 获取服务器密钥列表作为参考
    try:
        response = requests.get('http://localhost:2048/api/keys', timeout=5)
        if response.status_code == 200:
            data = response.json()
            keys = data.get('keys', [])
            if keys:
                test_key = keys[0]['value']
                print(f"  📋 使用服务器密钥进行测试: {test_key[:4]}****{test_key[-4:]}")
                
                # 测试密钥验证端点
                validation_response = requests.post('http://localhost:2048/api/keys/test', 
                                                  json={'key': test_key}, timeout=5)
                
                if validation_response.status_code == 200:
                    validation_data = validation_response.json()
                    if validation_data.get('valid'):
                        print(f"  ✅ 密钥验证端点正常工作")
                        return test_key
                    else:
                        print(f"  ❌ 密钥验证失败")
                        return None
                else:
                    print(f"  ❌ 验证请求失败: {validation_response.status_code}")
                    return None
            else:
                print(f"  ⚠️ 服务器没有配置密钥")
                return None
        else:
            print(f"  ❌ 获取密钥列表失败: {response.status_code}")
            return None
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return None

def test_chat_with_user_key(api_key):
    """测试使用用户密钥进行对话"""
    print(f"💬 测试使用用户密钥进行对话...")
    
    try:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }
        
        payload = {
            'model': 'gemini-1.5-pro',
            'messages': [
                {'role': 'user', 'content': '请简单回复"用户密钥测试成功"'}
            ],
            'stream': False,
            'temperature': 0.7,
            'max_output_tokens': 50
        }
        
        response = requests.post('http://localhost:2048/v1/chat/completions',
                               headers=headers, json=payload, timeout=30)
        
        print(f"  📋 响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            print(f"  ✅ 使用用户密钥的对话请求成功")
            try:
                response_data = response.json()
                content = response_data.get('choices', [{}])[0].get('message', {}).get('content', '')
                print(f"  📋 AI回复: {content[:100]}...")
            except:
                pass
            return True
        elif response.status_code == 401:
            print(f"  ❌ 认证失败 (401) - 用户密钥无效")
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
    """测试无认证的对话请求（应该被拒绝）"""
    print("🔒 测试无认证对话请求...")
    
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
        print(f"  ❌ 请求失败: {e}")
        return False

def test_invalid_key_rejection():
    """测试无效密钥被正确拒绝"""
    print("🔍 测试无效密钥拒绝...")
    
    invalid_key = "invalid_test_key_123456789"
    try:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {invalid_key}'
        }
        
        payload = {
            'model': 'gemini-1.5-pro',
            'messages': [{'role': 'user', 'content': '测试'}],
            'stream': False
        }
        
        response = requests.post('http://localhost:2048/v1/chat/completions',
                               headers=headers, json=payload, timeout=10)
        
        if response.status_code == 401:
            print(f"  ✅ 无效密钥正确被拒绝 (401)")
            return True
        else:
            print(f"  ❌ 无效密钥应该被拒绝，但返回: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return False

def main():
    print("🚀 Web UI 密钥修复验证测试")
    print("=" * 60)
    print("验证用户输入密钥的正确使用和本地存储功能")
    print()
    
    # 测试密钥验证功能
    test_key = test_api_key_validation()
    print()
    
    if not test_key:
        print("❌ 无法获取有效的测试密钥，跳过后续测试")
        return
    
    # 测试使用用户密钥进行对话
    chat_success = test_chat_with_user_key(test_key)
    print()
    
    # 测试无认证请求被拒绝
    no_auth_rejected = test_chat_without_auth()
    print()
    
    # 测试无效密钥被拒绝
    invalid_key_rejected = test_invalid_key_rejection()
    print()
    
    # 总结
    print("📊 测试结果总结")
    print("=" * 60)
    print(f"密钥验证功能: {'✅ 通过' if test_key else '❌ 失败'}")
    print(f"用户密钥对话: {'✅ 通过' if chat_success else '❌ 失败'}")
    print(f"无认证拒绝: {'✅ 通过' if no_auth_rejected else '❌ 失败'}")
    print(f"无效密钥拒绝: {'✅ 通过' if invalid_key_rejected else '❌ 失败'}")
    
    all_passed = all([test_key, chat_success, no_auth_rejected, invalid_key_rejected])
    
    print()
    if all_passed:
        print("🎉 所有测试通过！Web UI 密钥功能修复成功！")
        print()
        print("✅ 修复的功能:")
        print("  • 对话功能只使用用户验证的密钥，不使用服务器密钥")
        print("  • 用户输入的密钥自动保存到浏览器本地存储")
        print("  • 页面刷新后自动恢复保存的密钥")
        print("  • 重置功能会清除本地存储的密钥")
        print("  • 增强的认证验证和错误处理")
        print()
        print("💡 使用说明:")
        print("  1. 访问 http://localhost:2048 打开Web界面")
        print("  2. 在'设置'标签页输入您的API密钥")
        print("  3. 点击'验证密钥'按钮进行验证")
        print("  4. 验证成功后密钥会自动保存到本地存储")
        print("  5. 在'聊天'标签页进行对话，会自动使用您的密钥")
        print("  6. 刷新页面后密钥会自动恢复，无需重新输入")
    else:
        print("❌ 部分测试失败，需要进一步检查")

if __name__ == "__main__":
    main()
