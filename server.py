from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from _project_bootstrap import activate_project_environment

# Support direct execution from checkout
PROJECT_ROOT = Path(__file__).resolve().parent
BOOTSTRAP = activate_project_environment(PROJECT_ROOT)
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

# Now import project modules
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from offline_rag.config import AppConfig  # noqa: E402
from offline_rag.runtime import RuntimeController  # noqa: E402

# Initialize controller and globals
controller = RuntimeController(AppConfig.from_env())
active_index_key = None
chat_messages = []


def choose_directories() -> list[str]:
    # Try AppleScript on macOS first as it supports multiple directory selection natively
    if sys.platform == "darwin":
        script = (
            'set theFolders to choose folder with prompt "Select folders to index" '
            'with multiple selections allowed\n'
            'set thePaths to {}\n'
            'repeat with aFolder in theFolders\n'
            '    copy POSIX path of aFolder to end of thePaths\n'
            'end repeat\n'
            'return thePaths'
        )
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=True,
            )
            output = proc.stdout.strip()
            if output:
                # Split by comma-space and strip POSIX trailing slashes
                return [p.strip().rstrip("/") for p in output.split(",") if p.strip()]
        except Exception:
            pass

    # Fallback to Tkinter (only supports single directory selection at a time)
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()  # Hide root window
        root.attributes("-topmost", True)  # Bring dialog to front
        directory = filedialog.askdirectory(title="Select Folder to Index")
        root.destroy()
        if directory:
            return [directory]
    except Exception:
        pass
    return []


def to_langchain_history(messages_list: list[dict[str, object]], max_turns: int):
    history = []
    for message in messages_list[-(max_turns * 2) :]:
        content = str(message["content"])
        if message["role"] == "user":
            history.append(HumanMessage(content=content))
        elif message["role"] == "assistant":
            history.append(AIMessage(content=content))
    return history


class RAGHTTPRequestHandler(BaseHTTPRequestHandler):
    # Quiet server logging to keep terminal clean
    def log_message(self, format_str, *args):
        pass

    def send_json(self, data: dict, status_code: int = 200) -> None:
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(500, f"JSON serialization failed: {e}")

    def do_GET(self) -> None:
        url_parts = urllib.parse.urlparse(self.path)
        path = url_parts.path

        # API Endpoints
        if path == "/api/status":
            self.handle_api_status()
            return

        # Static File Resolution
        if path == "/" or path == "":
            path = "/index.html"

        static_dir = PROJECT_ROOT / "static"
        file_path = (static_dir / path.lstrip("/")).resolve()

        # Security Check: Ensure file is contained inside static_dir
        if not file_path.is_relative_to(static_dir) or not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")
            return

        # Set mime-type
        content_type = "text/plain"
        if file_path.suffix == ".html":
            content_type = "text/html"
        elif file_path.suffix == ".css":
            content_type = "text/css"
        elif file_path.suffix == ".js":
            content_type = "text/javascript"

        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Server error: {e}".encode())

    def do_POST(self) -> None:
        url_parts = urllib.parse.urlparse(self.path)
        path = url_parts.path

        # Read JSON body
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

        try:
            body = json.loads(post_data) if post_data else {}
        except Exception:
            self.send_json({"detail": "Invalid JSON"}, 400)
            return

        if path == "/api/activate":
            self.handle_api_activate(body)
        elif path == "/api/select_directories":
            self.handle_api_select_directories()
        elif path == "/api/scan":
            self.handle_api_scan()
        elif path == "/api/clear":
            self.handle_api_clear()
        elif path == "/api/chat":
            self.handle_api_chat(body)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"API Route Not Found")

    # ==========================================================================
    # API HANDLERS
    # ==========================================================================
    def handle_api_status(self) -> None:
        snapshot = controller.snapshot()

        active_dirs = []
        active_dir_str = None
        if controller.active:
            active_dirs = [str(d) for d in controller.active.directories]
            active_dir_str = ", ".join(active_dirs)

        response = {
            "phase": snapshot.phase,
            "progress": snapshot.progress,
            "completed": snapshot.completed,
            "total": snapshot.total,
            "indexed_chunks": snapshot.indexed_chunks,
            "message": snapshot.message,
            "error": snapshot.error,
            "ready": snapshot.ready,
            "stats": snapshot.stats,
            "active_directory": active_dir_str,
            "active_directories": active_dirs,
        }
        self.send_json(response)

    def handle_api_activate(self, body: dict) -> None:
        global active_index_key, chat_messages
        directories = body.get("directories")
        if not directories:
            # Fallback to single directory if present
            directory = body.get("directory")
            if directory:
                directories = [directory]
            else:
                self.send_json({"detail": "directories paths are required"}, 400)
                return

        resolved_paths = []
        for directory in directories:
            user_path = os.path.expanduser(directory.strip())
            expanded_path = os.path.abspath(user_path)

            path_is_abs = os.path.isabs(expanded_path)
            path_exists = os.path.exists(expanded_path)
            path_is_dir = os.path.isdir(expanded_path)

            if not path_is_abs or not path_exists or not path_is_dir:
                self.send_json({"detail": f"Invalid directory path: {directory}"}, 400)
                return
            resolved_paths.append(expanded_path)

        try:
            index_key = controller.activate(resolved_paths)
            if active_index_key != index_key:
                chat_messages = []
                active_index_key = index_key
            self.send_json({"active_key": index_key, "status": "ok"})
        except Exception as e:
            self.send_json({"detail": f"Activation failed: {e}"}, 500)

    def handle_api_select_directories(self) -> None:
        try:
            dirs = choose_directories()
            self.send_json({"directories": dirs})
        except Exception as e:
            self.send_json({"detail": f"Folder chooser failed: {e}"}, 500)

    def handle_api_scan(self) -> None:
        if controller.active is None:
            self.send_json({"detail": "No active index"}, 400)
            return
        try:
            controller.request_full_scan()
            self.send_json({"status": "ok"})
        except Exception as e:
            self.send_json({"detail": f"Scan failed: {e}"}, 500)

    def handle_api_clear(self) -> None:
        global chat_messages
        chat_messages = []
        self.send_json({"status": "ok"})

    def handle_api_chat(self, body: dict) -> None:
        global chat_messages
        prompt = body.get("prompt")
        if not prompt:
            self.send_json({"detail": "prompt string is required"}, 400)
            return

        snapshot = controller.snapshot()
        if not snapshot.ready:
            self.send_json({"detail": "Document index is not ready"}, 400)
            return

        # Prepare LangChain history
        chat_history = to_langchain_history(chat_messages, controller.config.history_turns)

        # Append User Message Optimistically
        chat_messages.append({"role": "user", "content": prompt})

        try:
            result = controller.answer(prompt, chat_history)
            answer = result.answer
            citations = result.citations
        except Exception as exc:
            answer = (
                "The local RAG pipeline could not complete the request. "
                f"Details: {type(exc).__name__}: {exc}"
            )
            citations = []

        # Save Assistant Message
        chat_messages.append({"role": "assistant", "content": answer, "citations": citations})

        self.send_json({"answer": answer, "citations": citations})


def main() -> None:
    # Port configuration parsing matching Streamlit arguments (e.g. --server.port 8502)
    port = 8501
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if (arg == "--server.port" or arg == "--port") and i + 1 < len(args):
            with contextlib.suppress(ValueError):
                port = int(args[i + 1])

    # Start server
    server_address = ("0.0.0.0", port)
    httpd = HTTPServer(server_address, RAGHTTPRequestHandler)

    print("\n" + "=" * 70)
    print("🔒  AIR-GAPPED DOCUMENT ASSISTANT IS ONLINE")
    print(f"👉  Open your web browser at:  http://0.0.0.0:{port}")
    print("=" * 70 + "\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping RAG web server...")
        if controller.active:
            controller.active.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
