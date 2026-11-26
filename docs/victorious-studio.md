# Victorious-ComfyUI Studio (first development milestone)

Studio is a lightweight, local-first image-generation page, served by the existing ComfyUI process. It does not replace the upstream frontend or require a separate Node.js build.

## Run

Use the repository's existing installation instructions and GPU-specific PyTorch installation. With a compatible SD 1.5 or SDXL all-in-one checkpoint installed in the configured checkpoints folder:

```sh
python main.py --offline
```

Open http://127.0.0.1:8188/victorious-studio/ in a modern browser. The original editor remains at http://127.0.0.1:8188/.

Choose a compatible checkpoint, enter a prompt, set resolution and generation settings, and select Generate image. Studio uses the existing prompt queue and saves images through SaveImage in the configured output directory. Click a result to view its full image. Cancellation is scoped to the Studio job ID.

Checkpoint discovery does not identify model architectures; use the advanced editor for split models, FLUX, video and other incompatible workflows. Start with batch size 1 and modest resolution on RAM-constrained machines. The hardware summary is informational, not a guarantee that a model fits.

## Character Studio

1. Select an installed SD 1.5 or SDXL all-in-one checkpoint.
2. Open Character and reference settings. Enter a profile name and reusable appearance description. The description is prepended to the scene prompt at generation time.
3. Optionally select an installed, compatible trained LoRA and choose its strength. Checkpoint/LoRA compatibility is not automatically inferred.
4. Optionally select an existing input image or explicitly upload a PNG/JPEG. Uploads are decoded, resized to at most 2048 pixels on the longest side, and re-encoded as PNG before transfer to the local ComfyUI server. Files above 10 MiB and images above 24 megapixels are rejected by the Studio.
5. Save the profile. Select it later to restore the description, LoRA, strength, reference filename and denoise settings. Choose No saved profile to start a new one.
6. Enter a scene prompt and generate. Reference mode uses LoadImage, ImageScale, VAEEncode and KSampler, requires batch size 1, and center-crops to the selected resolution. Lower denoise generally preserves more of the source image.

Profiles are stored as unencrypted browser local storage (up to 100 profiles), scoped to the server origin and browser profile. They do not sync between browsers or devices. Deleting a profile does not delete server input images; manage those separately. Generation outputs can contain prompts and filenames as workflow metadata. Use only references and trained models you are authorized to use.

LoRA support can reuse existing trained identities; it does not train new LoRAs or guarantee identity consistency. Image-to-image editing is not an identity embedding or face-conditioning pipeline. Dedicated identity conditioning and training remain future work.

### Additional smoke tests

- Save, reload, edit and delete a profile; confirm deletion leaves input files intact.
- Refresh with a missing LoRA/reference; confirm generation rejects the missing asset rather than silently dropping it.
- Upload a valid PNG/JPEG and reject an oversized or undecodable file.
- Generate using a known compatible LoRA, reference image, and both together; inspect the graph, output and VRAM use.
- Confirm text-only generation still works and reference mode rejects batches above one.

## Security and limitations

No new dependencies, cloud services, telemetry, or external network requests are introduced by the Studio page. The Studio inherits the existing server's network trust model; it does not add authentication or multi-user isolation. Keep ComfyUI bound to loopback unless a properly authenticated and authorized gateway is configured. Do not expose the raw server publicly.

This milestone tracks one job per browser page. Refreshing or closing the page loses the page's active job tracking, but does not cancel generation. Use the advanced editor to inspect its queue and history. On an ambiguous submission failure, inspect the queue before retrying.

## Smoke test

1. Start the server and confirm both the Studio and original node editor load.
2. Verify missing checkpoints disable generation and installed checkpoints are selectable.
3. Generate one image using a known compatible checkpoint, 512x512, batch 1 and fixed seed; confirm the output file and preview.
4. Submit a job and cancel it, separately testing queued and running states. Verify unrelated jobs are not interrupted.
5. Interrupt the connection while polling and restore it; confirm Studio retries status without resubmitting.
6. Verify errors are shown for incompatible checkpoints and failed execution.
7. Check keyboard navigation and narrow-screen layout.

## Next milestones

- Server-side character library, dedicated identity conditioning and LoRA training.
- Advanced image editing, pose and identity conditioning.
- Model-aware workflow presets, local model management and memory-aware guidance.
- Video workflows and optional training integrations.
- Authenticated fleet integration with VictorLinux, DroidCommand-AI, VictorBots and Victorious Mesh.

GPU/browser end-to-end smoke tests remain required before release.
