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

## Photo Studio

### Masked edits

Select the reference image, choose Image-to-image editing, and select a matching black-and-white mask from the server input directory. The mask must share the original reference dimensions and framing; Studio does not automatically validate that correspondence. White areas are regenerated, black areas are composited back from the resized original. Both images are center-cropped to the output dimensions. Start at denoise 1 for a replacement, or experiment with lower values. The workflow uses VAEEncodeForInpaint and ImageCompositeMasked; it targets compatible SD 1.5/SDXL checkpoint workflows and not all specialist inpaint checkpoints.

Masks may be uploaded using the existing reference upload control, then selected as masks after restoring the original reference selection. No automatic segmentation, brush editor or outpainting is provided yet. Optional postprocessing affects the full composited image.

### PhotoMaker identity (experimental)

Install compatible PhotoMaker v1 weights in the configured photomaker model directory, select a compatible SDXL checkpoint and a single clear authorized portrait, choose PhotoMaker identity, and select the installed weights. Use a short prompt and batch size 1 without a mask. Studio adds the required photomaker token and uses the native PhotoMakerLoader and PhotoMakerEncode nodes on the positive conditioning path. This creates an empty latent rather than editing the reference. PhotoMaker's encoder has its own token and architecture limitations: the 180-character UI check is only a conservative length guard, not a tokenizer guarantee. No dedicated identity-strength slider or identity similarity guarantee is provided. Do not assume PhotoMaker v2 or SD 1.5 compatibility.

### Enhancement

Choose 2× Lanczos resize for conventional interpolation or select an installed AI upscaler from the discovered upscale_models list. AI upscalers use the selected model's native scale. Enhancement is applied after decoding and any mask compositing, and only the final enhanced image is saved by the Studio workflow. Large images and multiple models can exhaust system RAM or VRAM; begin with a small image and a batch of one.

The page discovers the needed nodes and model lists through object_info and rejects unavailable selections at submission time. It does not install dependencies or download weights automatically. Photo Studio selections are currently session settings, not saved in character profiles.

### Regression and runtime testing

With Node.js 18 or newer available for development, run:

```sh
node --test tests/victorious_studio_graph.test.cjs
```

This checks graph wiring and validation without needing GPU models. The runtime browser and model checks still require a configured ComfyUI installation: compare masked edits with the resized source; verify valid PhotoMaker conditioning on SDXL; check missing weights and incompatible settings; compare interpolated and learned-upscale output dimensions; and re-run ordinary text/image generation. Use a small batch to test memory behavior.

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

- Server-side character library, validated identity workflows and LoRA training.
- Interactive mask editing, outpainting, pose control and background segmentation.
- Model-aware workflow presets, local model management and memory-aware guidance.
- Video workflows and optional training integrations.
- Authenticated fleet integration with VictorLinux, DroidCommand-AI, VictorBots and Victorious Mesh.

GPU/browser end-to-end smoke tests remain required before release.
