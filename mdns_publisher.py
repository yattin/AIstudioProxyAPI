import socket
import time
import argparse
import signal
import sys
from zeroconf import ServiceInfo, Zeroconf
import netifaces # Used for more reliable IP address discovery

# --- Configuration ---
DEFAULT_SERVICE_NAME = "AI Studio Chat UI" # User-friendly name shown in Bonjour browsers
DEFAULT_DOMAIN_NAME = "chatui" # The base name, .local will be appended
SERVICE_TYPE = "_http._tcp.local." # Standard for HTTP services
PORT = 2048 # Port where server.py is running

# --- Global Variables ---
zeroconf = None
info = None

def get_lan_ip():
    """
    Attempts to find the primary LAN IP address of the machine.
    Prioritizes the interface associated with the default gateway.
    """
    try:
        # Find the default gateway
        gateways = netifaces.gateways()
        default_gateway_info = gateways.get('default', {}).get(netifaces.AF_INET)

        if default_gateway_info:
            interface = default_gateway_info[1]
            addresses = netifaces.ifaddresses(interface)
            ipv4_info = addresses.get(netifaces.AF_INET)
            if ipv4_info:
                ip_address = ipv4_info[0]['addr']
                print(f"   发现默认网关接口 '{interface}' 的 IP: {ip_address}") # 中文
                return ip_address
        else:
            print("   警告: 未找到默认网关信息。") # 中文

        # Fallback: Iterate through all interfaces if default gateway method fails
        print("   尝试遍历所有接口...") # 中文
        for interface in netifaces.interfaces():
            addresses = netifaces.ifaddresses(interface)
            ipv4_info = addresses.get(netifaces.AF_INET)
            if ipv4_info:
                ip_address = ipv4_info[0]['addr']
                # Avoid loopback and obviously wrong addresses
                if not ip_address.startswith("127.") and ip_address != '0.0.0.0':
                    print(f"   使用接口 '{interface}' 的 IP: {ip_address}") # 中文
                    return ip_address

    except Exception as e:
        print(f"   获取 IP 地址时出错 (netifaces): {e}") # 中文

    # Final fallback: Use standard socket method (less reliable)
    try:
        print("   回退到标准 socket 方法获取 IP...") # 中文
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        if not ip_address.startswith("127."):
             print(f"   使用 socket.gethostbyname 获取的 IP: {ip_address}") # 中文
             return ip_address
        # Try getting IP associated with default route socket connection
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        try:
            # Doesn't have to be reachable
            s.connect(('10.254.254.254', 1))
            ip_address = s.getsockname()[0]
        except Exception:
            ip_address = '127.0.0.1' # Default if connect fails
        finally:
            s.close()
        if not ip_address.startswith("127."):
             print(f"   使用 socket 连接获取的 IP: {ip_address}") # 中文
             return ip_address

    except socket.gaierror as e:
        print(f"   获取 IP 地址时出错 (socket): {e}") # 中文

    print("❌ 错误: 无法自动检测有效的局域网 IP 地址。") # 中文
    return None

def register_service(name: str):
    global zeroconf, info
    
    ip_address = get_lan_ip()
    if not ip_address:
        print("无法启动 mDNS 服务，因为未能确定 IP 地址。") # 中文
        sys.exit(1)

    hostname = f"{name}.local." # Fully qualified domain name for mDNS

    print(f"\n--- 正在注册 mDNS 服务 ---") # 中文
    print(f"   服务类型: {SERVICE_TYPE}") # 中文
    print(f"   服务名称: {DEFAULT_SERVICE_NAME}") # 中文
    print(f"   域名: {hostname.rstrip('.')}") # 中文
    print(f"   IP 地址: {ip_address}") # 中文
    print(f"   端口: {PORT}") # 中文
    
    # Construct the ServiceInfo object
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{DEFAULT_SERVICE_NAME}.{SERVICE_TYPE}", # Unique instance name
        addresses=[socket.inet_aton(ip_address)],
        port=PORT,
        properties={}, # No specific properties needed for basic HTTP
        server=hostname, # This links the service to the desired hostname
    )

    zeroconf = Zeroconf()
    print("   正在广播服务...") # 中文
    zeroconf.register_service(info)
    print(f"✅ 服务已注册。现在可以在局域网内尝试访问 http://{hostname.rstrip('.')}:{PORT}") # 中文
    print("   (按 Ctrl+C 停止广播)") # 中文

def unregister_service(signum, frame):
    global zeroconf, info
    print("\n--- 收到停止信号，正在注销 mDNS 服务 ---") # 中文
    if zeroconf and info:
        try:
            zeroconf.unregister_service(info)
            zeroconf.close()
            print("✅ 服务已注销。") # 中文
        except Exception as e:
            print(f"   注销服务时出错: {e}") # 中文
    sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="使用 mDNS/Bonjour 在局域网内广播 AI Studio Proxy 服务。") # 中文
    parser.add_argument(
        "--name",
        type=str,
        default=DEFAULT_DOMAIN_NAME,
        help=f"要在局域网内广播的域名基础部分 (将附加 '.local')。默认为: '{DEFAULT_DOMAIN_NAME}'" # 中文
    )
    args = parser.parse_args()

    # Setup signal handling for graceful shutdown
    signal.signal(signal.SIGINT, unregister_service) # Handle Ctrl+C
    signal.signal(signal.SIGTERM, unregister_service) # Handle termination signal

    try:
        # Check dependencies
        try:
             import netifaces
             import zeroconf
        except ImportError as e:
             print(f"❌ 错误: 缺少依赖库 '{e.name}'。") # 中文
             print("   请先运行安装命令: pip install zeroconf netifaces") # 中文
             sys.exit(1)

        register_service(args.name)
        # Keep the script alive while zeroconf runs in background threads
        while True:
            time.sleep(1)
    except Exception as e:
        print(f"\n❌ 脚本运行时发生意外错误: {e}") # 中文
        # Ensure cleanup happens even on unexpected errors before exit
        unregister_service(None, None) 