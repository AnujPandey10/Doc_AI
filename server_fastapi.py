"""FastAPI server for the Air-Gapped RAG application.

Replaces the stdlib HTTPServer with full WebSocket streaming, JWT
authentication, role-based access, and PDF serving capabilities.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from _project_bootstrap import activate_project_environment

# Support direct execution from checkout
PROJECT_ROOT = Path(__file__).resolve().parent
BOOTSTRAP = activate_project_environment(PROJECT_ROOT)
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from offline_rag.auth import TokenPayload, UserStore  # noqa: E402
from offline_rag.auth_middleware import (  # noqa: E402
    get_current_user,
    get_ws_user,
    require_role,
    set_user_store,
)
from offline_rag.config import AppConfig  # noqa: E402
from offline_rag.runtime import RuntimeController  # noqa: E402

# ── Initialise core services ────────────────────────────────────────────

config = AppConfig.from_env()
controller = RuntimeController(config)
user_store = UserStore(config.data_root / "users.sqlite3")
set_user_store(user_store)

# Per-user chat histories keyed by user_id
chat_histories: dict[int, list[dict[str, object]]] = defaultdict(list)
active_index_key: str | None = None

# ── Pydantic request/response models ────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"

class RoleUpdateRequest(BaseModel):
    role: str

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

class ActivateRequest(BaseModel):
    directories: list[str] | None = None
    directory: str | None = None

class ChatRequest(BaseModel):
    prompt: str

# ── FastAPI app ──────────────────────────────────────────────────────────

app = FastAPI(
    title="Air-Gapped Document Assistant",
    docs_url=None,
    redoc_url=None,
)


# ── Auth routes (login is public, everything else requires a token) ──────

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    user = user_store.authenticate(req.username, req.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = user_store.create_token(user)
    return {"token": token, "user": user.as_dict()}


@app.post("/api/auth/register")
async def register(
    req: RegisterRequest,
    admin: TokenPayload = Depends(require_role("admin")),
):
    try:
        user = user_store.create_user(req.username, req.password, req.role)
        return {"user": user.as_dict()}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/auth/me")
async def get_me(user: TokenPayload = Depends(get_current_user)):
    record = user_store.get_user_by_id(user.user_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return {"user": record.as_dict()}


@app.get("/api/auth/users")
async def list_users(admin: TokenPayload = Depends(require_role("admin"))):
    users = user_store.list_users()
    return {"users": [u.as_dict() for u in users]}


@app.delete("/api/auth/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: TokenPayload = Depends(require_role("admin")),
):
    if admin.user_id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )
    if not user_store.delete_user(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return {"status": "ok"}


@app.put("/api/auth/users/{user_id}/role")
async def update_user_role(
    user_id: int,
    req: RoleUpdateRequest,
    admin: TokenPayload = Depends(require_role("admin")),
):
    try:
        updated = user_store.update_role(user_id, req.role)
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return {"user": updated.as_dict()}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.put("/api/auth/change-password")
async def change_password(
    req: PasswordChangeRequest,
    user: TokenPayload = Depends(get_current_user),
):
    record = user_store.authenticate(user.username, req.current_password)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    try:
        user_store.change_password(user.user_id, req.new_password)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ── RAG API routes ───────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status(user: TokenPayload = Depends(get_current_user)):
    snapshot = controller.snapshot()
    active_dirs: list[str] = []
    active_dir_str: str | None = None
    if controller.active:
        active_dirs = [str(d) for d in controller.active.directories]
        active_dir_str = ", ".join(active_dirs)

    return {
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


@app.post("/api/activate")
async def activate_index(
    req: ActivateRequest,
    user: TokenPayload = Depends(require_role("admin")),
):
    global active_index_key

    directories = req.directories
    if not directories:
        if req.directory:
            directories = [req.directory]
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="directories paths are required",
            )

    resolved_paths: list[str] = []
    for directory in directories:
        user_path = os.path.expanduser(directory.strip())
        expanded_path = os.path.abspath(user_path)
        if not os.path.isabs(expanded_path) or not os.path.isdir(expanded_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid directory path: {directory}",
            )
        resolved_paths.append(expanded_path)

    try:
        index_key = controller.activate(resolved_paths)
        if active_index_key != index_key:
            chat_histories.clear()
            active_index_key = index_key
        return {"active_key": index_key, "status": "ok"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Activation failed: {e}",
        )


def _choose_directories() -> list[str]:
    """Open a native OS folder picker dialog."""
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
                return [p.strip().rstrip("/") for p in output.split(",") if p.strip()]
        except Exception:
            pass

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        directory = filedialog.askdirectory(title="Select Folder to Index")
        root.destroy()
        if directory:
            return [directory]
    except Exception:
        pass
    return []


@app.post("/api/select_directories")
async def select_directories(
    user: TokenPayload = Depends(require_role("admin")),
):
    try:
        dirs = _choose_directories()
        return {"directories": dirs}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Folder chooser failed: {e}",
        )


@app.post("/api/scan")
async def trigger_scan(user: TokenPayload = Depends(require_role("admin"))):
    if controller.active is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active index",
        )
    try:
        controller.request_full_scan()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scan failed: {e}",
        )


@app.post("/api/clear")
async def clear_chat(user: TokenPayload = Depends(get_current_user)):
    chat_histories[user.user_id] = []
    return {"status": "ok"}


@app.post("/api/chat")
async def chat_sync(
    req: ChatRequest,
    user: TokenPayload = Depends(get_current_user),
):
    """Synchronous (non-streaming) chat endpoint — fallback for clients
    that don't support WebSocket."""
    snapshot = controller.snapshot()
    if not snapshot.ready:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document index is not ready",
        )

    messages = chat_histories[user.user_id]
    chat_history = _to_langchain_history(messages, controller.config.history_turns)
    messages.append({"role": "user", "content": req.prompt})

    try:
        result = controller.answer(req.prompt, chat_history)
        answer = result.answer
        citations = result.citations
    except Exception as exc:
        answer = (
            "The local RAG pipeline could not complete the request. "
            f"Details: {type(exc).__name__}: {exc}"
        )
        citations = []

    messages.append({"role": "assistant", "content": answer, "citations": citations})
    return {"answer": answer, "citations": citations}


# ── PDF serving ──────────────────────────────────────────────────────────

@app.get("/api/documents/pdf")
async def serve_pdf(
    path: str,
    user: TokenPayload = Depends(get_ws_user),
):
    """Serve a PDF file from the indexed directories for the citation viewer.

    Only serves files from currently active indexed directories.
    """
    file_path = Path(path).resolve()

    if not file_path.is_file() or file_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not found")

    # Security: only serve files from active indexed directories
    if controller.active is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active index",
        )

    is_within_indexed = any(
        file_path.is_relative_to(d) for d in controller.active.directories
    )
    if not is_within_indexed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="File is not within an indexed directory",
        )

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        content_disposition_type="inline",
        filename=file_path.name,
    )


# ── WebSocket streaming chat ─────────────────────────────────────────────

@app.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket,
    token: str = "",
):
    """Stream chat responses token-by-token over WebSocket.

    The client connects with ?token=<jwt> and sends JSON messages:
        {"prompt": "user question"}

    The server responds with a stream of JSON frames:
        {"type": "token", "content": "..."}
        {"type": "done", "citations": [...]}
    """
    # Validate JWT
    payload = user_store.verify_token(token)
    if payload is None:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            prompt = data.get("prompt", "").strip()
            if not prompt:
                await websocket.send_json({"type": "error", "detail": "Empty prompt"})
                continue

            snapshot = controller.snapshot()
            if not snapshot.ready:
                await websocket.send_json({
                    "type": "error",
                    "detail": "Document index is not ready",
                })
                continue

            messages = chat_histories[payload.user_id]
            chat_history = _to_langchain_history(messages, controller.config.history_turns)
            messages.append({"role": "user", "content": prompt})

            full_answer = ""

            # Get the runtime and stream
            runtime = controller.active
            if runtime is None or runtime.retrieval is None:
                await websocket.send_json({
                    "type": "error",
                    "detail": "Retrieval service not available",
                })
                continue

            try:
                async for chunk in runtime.retrieval.answer_streaming(prompt, chat_history):
                    if chunk.done:
                        await websocket.send_json({
                            "type": "done",
                            "citations": chunk.citations or [],
                        })
                        messages.append({
                            "role": "assistant",
                            "content": full_answer,
                            "citations": chunk.citations or [],
                        })
                    else:
                        full_answer += chunk.token
                        await websocket.send_json({
                            "type": "token",
                            "content": chunk.token,
                        })
            except Exception as exc:
                error_msg = (
                    "The local RAG pipeline could not complete the request. "
                    f"Details: {type(exc).__name__}: {exc}"
                )
                await websocket.send_json({
                    "type": "token",
                    "content": error_msg,
                })
                await websocket.send_json({
                    "type": "done",
                    "citations": [],
                })
                messages.append({
                    "role": "assistant",
                    "content": error_msg,
                    "citations": [],
                })

    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(Exception):
            await websocket.close()


# ── Helpers ──────────────────────────────────────────────────────────────

def _to_langchain_history(messages_list: list[dict[str, object]], max_turns: int):
    history = []
    for message in messages_list[-(max_turns * 2):]:
        content = str(message["content"])
        if message["role"] == "user":
            history.append(HumanMessage(content=content))
        elif message["role"] == "assistant":
            history.append(AIMessage(content=content))
    return history


# ── Static files (must be mounted last to avoid catching API routes) ─────

app.mount("/", StaticFiles(directory=str(PROJECT_ROOT / "static"), html=True), name="static")


# ── Entry point ──────────────────────────────────────────────────────────

def main() -> None:
    import uvicorn

    port = 8501
    host = "0.0.0.0"
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if (arg == "--server.port" or arg == "--port") and i + 1 < len(args):
            with contextlib.suppress(ValueError):
                port = int(args[i + 1])
        if (arg == "--host") and i + 1 < len(args):
            host = args[i + 1]

    print("\n" + "=" * 70)
    print("🔒  AIR-GAPPED DOCUMENT ASSISTANT IS ONLINE")
    print(f"👉  Open your web browser at:  http://{host}:{port}")
    print(f"🔑  Default credentials:  admin / changeme")
    print("=" * 70 + "\n")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
