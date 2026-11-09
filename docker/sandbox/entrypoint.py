import json
import sys
import os
import http.server
import socketserver
import subprocess

PORT = int(os.environ.get("SANDBOX_PORT", "8080"))

class SandboxHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"status": "ok", "sandbox": "healthy", "pid": os.getpid()}
            self.wfile.write(json.dumps(response).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/validate":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body) if body else {}
                code = data.get("code", "")
                filename = data.get("filename", "test_script.py")

                # Perform quick compilation check inside container
                tmp_path = os.path.join("/tmp", filename)
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(code)

                # Run Python compile check
                result = subprocess.run(
                    [sys.executable, "-m", "py_compile", tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

                response = {
                    "success": result.returncode == 0,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode("utf-8"))

            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence default standard log output to stderr
        pass

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), SandboxHandler) as httpd:
        httpd.serve_forever()
