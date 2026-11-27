"""Optional authenticated, typed media bridge for DroidCommand's HTTP MCP client.

No arbitrary workflows, URLs, paths or command execution. Run separately from ComfyUI.
"""
import argparse
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import re
from pathlib import Path
import sqlite3
import ssl
from urllib.parse import urlencode
import uuid

MAX_BODY = 16384
MAX_REPLY = 2 * 1024 * 1024
MAX_IMAGE = 16 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ACTIVE = ("reserved", "pending", "in_progress", "unknown")
TERMINAL = ("completed", "failed", "cancelled")


def upstream_bytes(path, body=None, limit=MAX_REPLY):
    conn = http.client.HTTPConnection("127.0.0.1", 8188, timeout=5)
    try:
        conn.request("GET" if body is None else "POST", path,
                     None if body is None else json.dumps(body),
                     {} if body is None else {"Content-Type": "application/json"})
        response = conn.getresponse()
        data = response.read(limit + 1)
        if response.status != 200 or len(data) > limit:
            raise RuntimeError("upstream request not confirmed")
        return data
    finally:
        conn.close()


def upstream(path, body=None):
    value = json.loads(upstream_bytes(path, body))
    if not isinstance(value, dict):
        raise RuntimeError("invalid upstream response")
    return value


def schema(properties, required=()):
    return {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False}


UUID_FIELD = {"type": "string", "description": "Canonical UUID; retain this exact ID for status, cancellation and retries."}
TOOLS = [
    {"name": "generate_image", "description": "Queue one local image using the owner-selected SD1.5/SDXL checkpoint. Returns immediately with a job ID; use image_status. Retrying the same request ID never submits twice.",
     "inputSchema": schema({
         "request_id": UUID_FIELD, "prompt": {"type": "string", "maxLength": 2000},
         "negative": {"type": "string", "maxLength": 2000},
         "width": {"type": "integer", "minimum": 256, "maximum": 1024},
         "height": {"type": "integer", "minimum": 256, "maximum": 1024},
         "steps": {"type": "integer", "minimum": 1, "maximum": 60},
         "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295}},
         ("request_id", "prompt")),
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "image_status", "description": "Check one bridge-owned job; completed output can be downloaded through the authenticated artifact endpoint.",
     "inputSchema": schema({"request_id": UUID_FIELD}, ("request_id",)),
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "cancel_image", "description": "Request cancellation of one bridge-owned job. Does not interrupt other jobs.",
     "inputSchema": schema({"request_id": UUID_FIELD}, ("request_id",)),
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]


def job_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("request_id must be a canonical UUID")
    return value


def graph(checkpoint, args):
    if set(args) - set(TOOLS[0]["inputSchema"]["properties"]):
        raise ValueError("unknown generation argument")
    prompt, negative = args.get("prompt"), args.get("negative", "")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
        raise ValueError("prompt must contain 1–2000 characters")
    if not isinstance(negative, str) or len(negative) > 2000:
        raise ValueError("invalid negative prompt")
    width, height, steps, seed = (args.get(k, d) for k, d in
                                  (("width", 512), ("height", 512), ("steps", 20), ("seed", 0)))
    if any(type(x) is not int or not 256 <= x <= 1024 or x % 64 for x in (width, height)):
        raise ValueError("dimensions must be multiples of 64 between 256 and 1024")
    if type(steps) is not int or not 1 <= steps <= 60 or type(seed) is not int or not 0 <= seed <= 4294967295:
        raise ValueError("invalid steps or seed")
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0], "seed": seed, "steps": steps, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "Victorious-Fleet"}}
    }


class Bridge:
    def __init__(self, database, checkpoint, call=upstream, fetch=upstream_bytes):
        self.db = sqlite3.connect(database)
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, digest TEXT NOT NULL, upstream_id TEXT NOT NULL, state TEXT NOT NULL)")
        if "artifact_version" not in {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}:
            self.db.execute("ALTER TABLE jobs ADD COLUMN artifact_version INTEGER NOT NULL DEFAULT 0")
        self.db.commit()
        self.checkpoint, self.call, self.fetch = checkpoint, call, fetch

    def update(self, ident, state):
        with self.db:
            self.db.execute("UPDATE jobs SET state=? WHERE id=?", (state, ident))

    def refresh_state(self, ident, upstream_id, state):
        if state in TERMINAL:
            return state
        try:
            result = self.call("/api/jobs/" + upstream_id)
            state = result.get("status")
            if state not in ("pending", "in_progress") + TERMINAL:
                raise RuntimeError("unknown status")
        except Exception:
            state = "unknown"
        self.update(ident, state)
        return state

    def artifact(self, ident):
        ident = job_id(ident)
        row = self.db.execute("SELECT upstream_id, state, artifact_version FROM jobs WHERE id=?", (ident,)).fetchone()
        if not row:
            raise KeyError("unknown job")
        if row[1] != "completed":
            raise ValueError("job is not confirmed completed; check image_status")
        if row[2] != 1:
            raise ValueError("legacy output is available on the PC only")
        history = self.call("/history/" + row[0])
        images = history.get(row[0], {}).get("outputs", {}).get("7", {}).get("images", [])
        if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
            raise RuntimeError("expected exactly one saved output")
        image = images[0]
        filename = image.get("filename")
        # Only the SaveImage node of this fixed graph and its root output file.
        if (image.get("type") != "output" or image.get("subfolder", "") != ""
                or not isinstance(filename, str)
                or not re.fullmatch(re.escape("Victorious-Fleet-" + row[0]) + r"_[0-9]+_\.png", filename)):
            raise RuntimeError("invalid artifact descriptor")
        data = self.fetch("/view?" + urlencode({"filename": filename, "type": "output"}), limit=MAX_IMAGE)
        if len(data) > MAX_IMAGE or not data.startswith(PNG_SIGNATURE):
            raise RuntimeError("invalid or oversized PNG output")
        return data

    def tool(self, name, args):
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        ident = job_id(args.get("request_id"))
        row = self.db.execute("SELECT digest, upstream_id, state, artifact_version FROM jobs WHERE id=?", (ident,)).fetchone()
        if name == "generate_image":
            workflow = graph(self.checkpoint, args)
            digest = hashlib.sha256(json.dumps(workflow, sort_keys=True).encode()).hexdigest()
            if row:
                if row[0] != digest:
                    raise ValueError("request ID already used for different settings")
                return {"request_id": ident, "status": row[2], "duplicate": True}
            # Release a stale capacity slot only after a terminal upstream report.
            # The reservation may belong to another process still submitting, so never query reserved rows here.
            active = self.db.execute("SELECT id, upstream_id, state FROM jobs WHERE state IN ('pending','in_progress','unknown')").fetchall()
            for active_id, remote_id, state in active:
                self.refresh_state(active_id, remote_id, state)
            upstream_id = str(uuid.uuid4())
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                existing = self.db.execute("SELECT digest, state FROM jobs WHERE id=?", (ident,)).fetchone()
                if existing:
                    if existing[0] != digest:
                        raise ValueError("request ID already used for different settings")
                    return {"request_id": ident, "status": existing[1], "duplicate": True}
                if self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= 10000:
                    raise ValueError("job ledger full; owner maintenance required")
                if self.db.execute("SELECT 1 FROM jobs WHERE state IN ('reserved','pending','in_progress','unknown')").fetchone():
                    raise ValueError("another job is active or unconfirmed; check its status first")
                self.db.execute("INSERT INTO jobs (id,digest,upstream_id,state,artifact_version) VALUES (?,?,?,?,1)",
                                (ident, digest, upstream_id, "reserved"))
            workflow["7"]["inputs"]["filename_prefix"] = "Victorious-Fleet-" + upstream_id
            # Persist the reservation before sending. Never retry ambiguous submissions.
            try:
                reply = self.call("/prompt", {"prompt": workflow, "prompt_id": upstream_id})
                if reply.get("prompt_id") != upstream_id:
                    raise RuntimeError("unconfirmed submission")
                state = "pending"
            except Exception:
                state = "unknown"
            self.update(ident, state)
            return {"request_id": ident, "status": state,
                    "note": "Check status; do not submit a new ID after an uncertain outcome."}
        if set(args) != {"request_id"}:
            raise ValueError("unexpected argument")
        if not row:
            raise ValueError("unknown bridge job")
        upstream_id, state = row[1], row[2]
        if name == "image_status":
            state = self.refresh_state(ident, upstream_id, state)
            result = {"request_id": ident, "status": state}
            if state == "completed" and row[3] == 1:
                result["artifact_path"] = "/artifacts/" + ident
                result["note"] = "Use authenticated GET; output may expire if history or files are removed."
            elif state == "completed":
                result["note"] = "Legacy output is available on the PC only."
            return result
        if name == "cancel_image":
            if state in TERMINAL:
                return {"request_id": ident, "status": state, "cancel_requested": False}
            try:
                result = self.call("/api/jobs/" + upstream_id + "/cancel", {})
                accepted = result.get("cancelled") is True
            except Exception:
                accepted = False
            # Do not claim terminal cancellation until status confirms it.
            return {"request_id": ident, "cancel_requested": accepted,
                    "note": "Check image_status; cancellation is not a confirmed terminal state."}
        raise ValueError("unknown tool")


def rpc(bridge, message):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    ident = message.get("id")
    method = message.get("method")
    params = message.get("params", {})
    if "id" not in message:
        return None
    result = None
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "victorious-media", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            if not isinstance(params, dict):
                raise ValueError("invalid parameters")
            output = bridge.tool(params.get("name"), params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(output)}]}
        except (ValueError, TypeError) as exc:
            result = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        except Exception:
            result = {"isError": True, "content": [{"type": "text", "text": "Bridge failure; check PC logs and job status."}]}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, *_):
        pass  # no prompts, tokens or job IDs in request logs

    def reply(self, status, value=None):
        data = b"" if value is None else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def authorized(self):
        if self.headers.get("Origin") is not None:
            self.reply(403)
            return False
        supplied = self.headers.get("Authorization", "")
        digest = hashlib.sha256(supplied.encode()).digest()
        if not hmac.compare_digest(digest, self.server.auth_digest):
            self.reply(401)
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        match = re.fullmatch(r"/artifacts/([0-9a-f-]{36})", self.path)
        if not match:
            return self.reply(404)
        try:
            data = self.server.bridge.artifact(match[1])
        except KeyError:
            return self.reply(404)
        except ValueError:
            return self.reply(409)
        except Exception:
            return self.reply(502, {"error": "Artifact unavailable; check PC output and history."})
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Disposition", 'attachment; filename="victorious-image.png"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Content-SHA256", hashlib.sha256(data).hexdigest())
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/mcp":
            return self.reply(404)
        if not self.authorized():
            return
        if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
            return self.reply(400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.reply(413)
            data = self.rfile.read(length)
            message = json.loads(data)
        except (ValueError, OSError):
            return self.reply(400)
        result = rpc(self.server.bridge, message)
        self.reply(202 if result is None else 200, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, help="Owner-selected installed checkpoint")
    parser.add_argument("--database", required=True, type=Path, help="Private persistent SQLite ledger")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8190)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    args = parser.parse_args()
    token = os.environ.get("VICTORIOUS_MEDIA_TOKEN", "")
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        parser.error("set a strong VICTORIOUS_MEDIA_TOKEN (at least 32 non-whitespace ASCII characters)")
    if bool(args.cert) != bool(args.key) or (args.listen != "127.0.0.1" and not args.cert):
        parser.error("non-loopback listening requires TLS certificate and key")
    os.umask(0o077)
    server = HTTPServer((args.listen, args.port), Handler)
    server.auth_digest = hashlib.sha256(("Bearer " + token).encode()).digest()
    server.bridge = Bridge(str(args.database), args.checkpoint)
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        server.bridge.db.close()


if __name__ == "__main__":
    main()
