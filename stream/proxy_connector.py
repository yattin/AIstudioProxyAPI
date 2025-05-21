import asyncio
import base64
import urllib.parse
import aiosocks
from aiohttp import TCPConnector

class ProxyConnector:
    """
    Class to handle connections through different types of proxies
    """
    def __init__(self, proxy_url=None):
        self.proxy_url = proxy_url
        self.connector = None
        
        if proxy_url:
            self._setup_connector()
    
    def _setup_connector(self):
        """Set up the appropriate connector based on the proxy URL"""
        if not self.proxy_url:
            self.connector = TCPConnector()
            return
        
        # Parse the proxy URL
        parsed = urllib.parse.urlparse(self.proxy_url)
        proxy_type = parsed.scheme.lower()

        if proxy_type in ('http', 'https'):
            self.connector = TCPConnector()
        elif proxy_type in ('socks4', 'socks5'):
            self.connector = "SocksConnector"
        else:
            raise ValueError(f"Unsupported proxy type: {proxy_type}")
    
    async def create_connection(self, host, port, ssl=None):
        """Create a connection to the target host through the proxy"""
        if not self.connector:
            # Direct connection without proxy
            reader, writer = await asyncio.open_connection(host, port, ssl=ssl)
            return reader, writer
        
        if self.connector == "SocksConnector":
            # SOCKS proxy connection
            parsed = urllib.parse.urlparse(self.proxy_url)
            proxy_type = parsed.scheme.lower()
            if proxy_type == "socks4":
                socks4_addr = aiosocks.Socks4Addr(parsed.hostname, parsed.port)
                socks4_auth = aiosocks.Socks4Auth(parsed.username)
                reader, writer = await aiosocks.open_connection(proxy=socks4_addr, proxy_auth=socks4_auth, dst=(host, port), remote_resolve=True)
                return reader, writer
            elif proxy_type == "socks5":
                socks5_addr = aiosocks.Socks5Addr(parsed.hostname, parsed.port)
                socks5_auth = aiosocks.Socks5Auth(parsed.username, parsed.password)
                reader, writer = await aiosocks.open_connection(proxy=socks5_addr, proxy_auth=socks5_auth, dst=(host, port), remote_resolve=True)
                return reader, writer

        # HTTP/HTTPS proxy connection
        if not ssl:
            # For HTTP connections through HTTP proxy
            proxy_parsed = urllib.parse.urlparse(self.proxy_url)
            reader, writer = await asyncio.open_connection(
                proxy_parsed.hostname, 
                proxy_parsed.port
            )
            
            # Send CONNECT request
            auth_header = ""
            if proxy_parsed.username and proxy_parsed.password:
                auth = f"{proxy_parsed.username}:{proxy_parsed.password}"
                auth_b64 = base64.b64encode(auth.encode()).decode()
                auth_header = f"Proxy-Authorization: Basic {auth_b64}\r\n"
            
            connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\n"
            connect_req += f"Host: {host}:{port}\r\n"
            connect_req += auth_header
            connect_req += "Connection: keep-alive\r\n\r\n"
            
            writer.write(connect_req.encode())
            await writer.drain()
            
            # Read the response
            response = await reader.readuntil(b"\r\n\r\n")
            status_line = response.split(b"\r\n")[0].decode()
            
            if not status_line.startswith("HTTP/1.1 2"):
                writer.close()
                raise ConnectionError(f"Proxy connection failed: {status_line}")
            
            return reader, writer
        else:
            # For HTTPS connections, we need to use aiohttp's ClientSession
            # This is a bit more complex and would require a different approach
            # For simplicity, we'll use a direct connection for HTTPS
            reader, writer = await asyncio.open_connection(host, port, ssl=ssl)
            return reader, writer
