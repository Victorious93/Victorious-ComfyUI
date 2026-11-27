# Fleet media bridge (experimental)

This optional standalone Python service connects DroidCommand-AI's existing HTTP MCP helper-bot tools to local image generation. It does not modify the original ComfyUI server or expose its raw API. VictorBots' existing owner-configured preset runner is separate and unchanged.

## Setup

1. Run a compatible Victorious-ComfyUI checkout on the PC in its configured Python environment:
   `python main.py --offline --listen 127.0.0.1 --port 8188`
2. Install a compatible SD 1.5 or SDXL all-in-one checkpoint. The owner chooses one exact installed checkpoint filename; clients cannot select another model.
3. Create a private, persistent directory for the job ledger (mode 0700). Generate a strong random bearer token; keep it in a password manager or protected environment file and do not commit it. Supply it as the environment variable `VICTORIOUS_MEDIA_TOKEN` (32+ non-whitespace ASCII characters).
4. Start the bridge:
   `python script_examples/victorious_media_mcp.py --checkpoint YOUR_CHECKPOINT.safetensors --database /path/to/private/jobs.sqlite`
5. The default address is `http://127.0.0.1:8190/mcp`, for local testing only. For a phone, use a trusted HTTPS reverse proxy or explicit `--listen YOUR_PRIVATE_IP --cert /path/to/cert.pem --key /path/to/key.pem` with a certificate trusted by the phone and matching the hostname. Restrict access to authorized LAN/VPN clients with a firewall. Do not port-forward or expose this experimental bridge publicly.
6. In DroidCommand-AI enable Advanced → Extra AI tools → MCP, add the HTTPS MCP URL and bearer token, refresh, then explicitly select the three media tools in the intended helper bot. This uses its existing MCP approval and scope enforcement; no Android code change is needed for initial compatibility. Pilot and Forge do not currently offer MCP tools. Live Android testing is outstanding.

## Tools

- `generate_image`: canonical UUID request_id, prompt, optional negative, width, height, steps, seed. Queues one image (256–1024 in multiples of 64, up to 60 steps). Returns quickly with pending or unknown; no synchronous generation.
- `image_status`: same request_id; reports last confirmed state, or unknown if status cannot be confirmed. Completed images remain in the configured PC output directory.
- `cancel_image`: same request_id; requests scoped cancellation, then check status. The cancellation response alone does not prove completion.

The request UUID is distinct from the private upstream UUID. Retrying the same ID and settings does not resubmit, including after restarting the bridge. Changing settings under the same ID is rejected. The SQLite ledger reserves IDs before making a request. A timeout can leave the outcome unknown; never submit a fresh ID merely to retry. If the upstream loses the job history or a submission never reached it, the bridge fails closed and requires owner reconciliation. Stop the bridge and back up its ledger before any manual maintenance; verify the local queue has no unresolved job before resetting anything.

## Security and limitations

Single-owner service, one bearer token shared by explicitly authorized clients; all holders can access bridge jobs if they know their request IDs. No multi-user authorization, rate-limit service, artifact download, arbitrary graph execution, remote file paths, shell access, training or video. The serial server allows one unconfirmed/active job at a time and at most 10,000 ledger entries; owner maintenance is required once full. Prompts are not persisted in the bridge ledger, but ComfyUI may persist them in history and output metadata. The bridge doesn't contact a cloud provider or use environment proxy settings. No browser Origin requests accepted. TLS is required for direct non-loopback binding; keep the host firewall restricted. The bridge is not automatically started or installed.

## Tests

`python tests/test_victorious_media_mcp.py -v`

Fixture-based tests exercise argument validation, idempotency across restart, uncertain submission, scoped cancellation and the MCP message contract. Real TLS connectivity, Android client approval flow and GPU generation still need end-to-end testing.
