import hashlib
import http.client
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

spec = importlib.util.spec_from_file_location(
    "victorious_media_mcp", Path(__file__).resolve().parents[1] / "script_examples" / "victorious_media_mcp.py"
)
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "jobs.sqlite"
        self.calls = []
        self.status = "pending"
        self.fail = False
        self.bridge = media.Bridge(self.path, "owner.safetensors", self.fake)
        self.args = {"request_id": str(uuid.uuid4()), "prompt": "a landscape"}

    def tearDown(self):
        self.bridge.db.close()
        self.tmp.cleanup()

    def fake(self, path, body=None):
        self.calls.append((path, body))
        if path == "/prompt":
            if self.fail:
                raise OSError("connection lost")
            return {"prompt_id": body["prompt_id"]}
        if path.endswith("/cancel"):
            return {"cancelled": True}
        return {"status": self.status}

    def test_idempotency_survives_restart(self):
        result = self.bridge.tool("generate_image", self.args)
        self.assertEqual(result["status"], "pending")
        self.bridge.db.close()
        self.bridge = media.Bridge(self.path, "owner.safetensors", self.fake)
        result = self.bridge.tool("generate_image", self.args)
        self.assertTrue(result["duplicate"])
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ValueError):
            self.bridge.tool("generate_image", dict(self.args, prompt="different"))

    def test_job_scope_and_capacity(self):
        self.bridge.tool("generate_image", self.args)
        with self.assertRaises(ValueError):
            self.bridge.tool("cancel_image", {"request_id": str(uuid.uuid4())})
        with self.assertRaises(ValueError):
            self.bridge.tool("generate_image", dict(self.args, request_id=str(uuid.uuid4())))
        result = self.bridge.tool("cancel_image", {"request_id": self.args["request_id"]})
        self.assertTrue(result["cancel_requested"])
        self.assertEqual(self.calls[-1][0], "/api/jobs/" + self.calls[0][1]["prompt_id"] + "/cancel")
        self.status = "cancelled"
        self.assertEqual(self.bridge.tool("image_status", {"request_id": self.args["request_id"]})["status"], "cancelled")

    def test_uncertain_submission_is_not_repeated(self):
        self.fail = True
        self.assertEqual(self.bridge.tool("generate_image", self.args)["status"], "unknown")
        self.assertTrue(self.bridge.tool("generate_image", self.args)["duplicate"])
        self.assertEqual(len(self.calls), 1)

    def test_reject_untyped_or_unbounded_inputs(self):
        for patch in ({"checkpoint": "other"}, {"width": True}, {"height": 513},
                      {"steps": 0}, {"seed": -1}, {"prompt": " "}, {"negative": []}):
            with self.assertRaises(ValueError):
                self.bridge.tool("generate_image", dict(self.args, **patch))
        self.assertEqual(self.calls, [])

    def test_rpc_contract(self):
        result = media.rpc(self.bridge, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(result["result"]["protocolVersion"], "2025-06-18")
        result = media.rpc(self.bridge, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(result["result"]["tools"]), 3)
        self.assertFalse(result["result"]["tools"][0]["annotations"]["readOnlyHint"])
        self.assertIsNone(media.rpc(self.bridge, {"jsonrpc": "2.0", "method": "notifications/initialized"}))


    def test_completed_job_releases_capacity_automatically(self):
        self.bridge.tool("generate_image", self.args)
        self.status = "completed"
        next_id = str(uuid.uuid4())
        self.assertEqual(self.bridge.tool("generate_image", dict(self.args, request_id=next_id))["status"], "pending")
        self.assertEqual(self.bridge.tool("image_status", {"request_id": self.args["request_id"]})["status"], "completed")

    def test_artifact_only_from_completed_owned_job(self):
        self.bridge.tool("generate_image", self.args)
        ident = self.args["request_id"]
        with self.assertRaises(ValueError):
            self.bridge.artifact(ident)
        with self.assertRaises(KeyError):
            self.bridge.artifact(str(uuid.uuid4()))
        self.status = "completed"
        self.assertEqual(self.bridge.tool("image_status", {"request_id": ident})["artifact_path"], "/artifacts/" + ident)
        remote = self.calls[0][1]["prompt_id"]
        filename = "Victorious-Fleet-" + remote + "_00001_.png"
        self.assertEqual(self.calls[0][1]["prompt"]["7"]["inputs"]["filename_prefix"], "Victorious-Fleet-" + remote)
        descriptor = {"filename": filename, "type": "output", "subfolder": ""}
        self.bridge.call = lambda path: {remote: {"outputs": {"7": {"images": [descriptor]}}}}
        def fetch(path, limit):
            self.assertEqual(path, "/view?filename=" + filename + "&type=output")
            self.assertEqual(limit, media.MAX_IMAGE)
            return media.PNG_SIGNATURE + b"fixture"
        self.bridge.fetch = fetch
        self.assertEqual(self.bridge.artifact(ident), media.PNG_SIGNATURE + b"fixture")
        for bad in ({"filename": "../private.png"}, {"subfolder": "other"}, {"type": "input"},
                    {"filename": "Victorious-Fleet-" + str(uuid.uuid4()) + "_00001_.png"}):
            original = descriptor.copy()
            descriptor.update(bad)
            with self.assertRaises(RuntimeError):
                self.bridge.artifact(ident)
            descriptor.clear()
            descriptor.update(original)
        self.bridge.fetch = lambda *a, **kw: b"not png"
        with self.assertRaises(RuntimeError):
            self.bridge.artifact(ident)


    def test_legacy_ledger_does_not_export_unbound_output(self):
        self.bridge.db.close()
        self.path.unlink()
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, digest TEXT NOT NULL, upstream_id TEXT NOT NULL, state TEXT NOT NULL)")
            db.execute("INSERT INTO jobs VALUES (?,?,?,?)", (self.args["request_id"], "digest", str(uuid.uuid4()), "completed"))
        self.bridge = media.Bridge(self.path, "owner.safetensors", self.fake)
        result = self.bridge.tool("image_status", {"request_id": self.args["request_id"]})
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("artifact_path", result)
        with self.assertRaises(ValueError):
            self.bridge.artifact(self.args["request_id"])


class HttpArtifactTests(unittest.TestCase):
    def test_authenticated_artifact_response(self):
        ident = str(uuid.uuid4())
        payload = media.PNG_SIGNATURE + b"fixture"

        class FixtureBridge:
            def artifact(self, value):
                if value != ident:
                    raise KeyError(value)
                return payload

        server = media.HTTPServer(("127.0.0.1", 0), media.Handler)
        server.bridge = FixtureBridge()
        token = "Bearer " + "a" * 40
        server.auth_digest = hashlib.sha256(token.encode()).digest()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def get(path, headers):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                try:
                    conn.request("GET", path, headers=headers)
                    response = conn.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    conn.close()
            path = "/artifacts/" + ident
            self.assertEqual(get(path, {})[0], 401)
            self.assertEqual(get(path, {"Authorization": token, "Origin": "https://untrusted.invalid"})[0], 403)
            self.assertEqual(get(path + "?filename=private.png", {"Authorization": token})[0], 404)
            self.assertEqual(get("/artifacts/" + str(uuid.uuid4()), {"Authorization": token})[0], 404)
            code, headers, data = get(path, {"Authorization": token})
            self.assertEqual(code, 200)
            self.assertEqual(data, payload)
            self.assertEqual(headers["X-Content-SHA256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(headers["Cache-Control"], "no-store")
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
