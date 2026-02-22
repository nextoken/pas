#!/usr/bin/env python3
"""
@pas-executable
Start a simple testing server (HTTP or TCP) on a specified port.
"""

import sys
import argparse
import http.server
import socketserver
import socket
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

console = Console()

def run_http_server(port: int):
    # Simple HTML content
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>PAS Test Server</title>
        <style>
            body {{ font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; background-color: #f0f2f5; }}
            .container {{ padding: 2rem; background: white; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: center; }}
            h1 {{ color: #1a73e8; }}
            p {{ color: #5f6368; }}
            .port {{ font-weight: bold; color: #d93025; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>PAS HTTP Test Server</h1>
            <p>Serving at <span class="port">http://localhost:{port}</span></p>
            <p>Status: <span style="color: green;">Active</span></p>
        </div>
    </body>
    </html>
    """

    class TestHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(html_content.encode("utf-8"))

        def log_message(self, format, *args):
            sys.stderr.write("%s - - [%s] %s\n" %
                             (self.address_string(),
                              self.log_date_time_string(),
                              format % args))

    try:
        with socketserver.TCPServer(("", port), TestHandler) as httpd:
            print(f"Starting HTTP server at http://localhost:{port}")
            print("Press Ctrl+C to stop.")
            httpd.serve_forever()
    except OSError as e:
        print(f"Error: Could not start server on port {port}: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nShutting down HTTP server.")

def run_tcp_server(port: int):
    """Simple TCP echo-like server that prints received data."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", port))
            s.listen()
            print(f"Starting raw TCP listener on port {port}")
            print("Press Ctrl+C to stop.")
            
            while True:
                conn, addr = s.accept()
                with conn:
                    print(f"\n[Connection from {addr}]")
                    while True:
                        data = conn.recv(1024)
                        if not data:
                            break
                        print(f"Received: {data!r}")
                        # Optional: echo back
                        conn.sendall(b"PAS TCP Test: Data Received\n")
    except OSError as e:
        print(f"Error: Could not start TCP listener on port {port}: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nShutting down TCP listener.")

def main():
    parser = argparse.ArgumentParser(
        description="Start a simple testing server (HTTP or TCP) on a specified port.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  test-server 8080            # Start HTTP server on 8080\n"
               "  test-server 9000 --tcp      # Start raw TCP listener on 9000"
    )
    parser.add_argument("port", type=int, help="Port to listen on")
    parser.add_argument("--tcp", action="store_true", help="Start a raw TCP listener instead of HTTP")
    args = parser.parse_args()

    info_text = f"""
[bold]Simple Test Server[/bold]

Quickly validate connectivity and request handling:
- [cyan]HTTP Mode[/cyan]: Serves a simple landing page and logs all GET requests.
- [cyan]TCP Mode[/cyan]: Listens for raw socket data and echoes it back.
- [cyan]Live Logging[/cyan]: Displays connection attempts and received payloads in real-time.
"""
    console.print(Panel(info_text.strip(), title="test-server", border_style="blue"))
    console.print("\n")

    if args.tcp:
        run_tcp_server(args.port)
    else:
        run_http_server(args.port)

if __name__ == "__main__":
    main()
