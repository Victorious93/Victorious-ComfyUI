import importlib.util
from pathlib import Path
import tempfile
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


if __name__ == "__main__":
    unittest.main()
