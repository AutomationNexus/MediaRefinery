# Model Management

MediaRefinery does not ship model weights. Models are installed on demand by an admin through the dashboard.

## Model Slots

| Slot | Purpose |
|------|---------|
| `primary_safety` | Main classifier used during scans. A primary classifier is required for real scans. |
| `ocr` | Optional local OCR bundle. Extracted text is stored as derived metadata. |
| `adult_subtype` | Optional admin-supplied ONNX profile for review-only subtype queues. |
| `semantic_embedding` | Reserved for future local embedding models. |

Older registry rows marked `classifier` are still read for compatibility, but the dashboard uses task-specific slots.

## Catalog Models

The catalog lives at [../models/catalog.json](../models/catalog.json). Each installable entry includes:

- model ID and display name;
- task/kind;
- HTTPS download URL;
- SHA256;
- expected size;
- license metadata;
- optional license text path.

When an admin installs a catalog model, MediaRefinery:

1. checks that the entry is installable;
2. requires license acceptance;
3. downloads from the pinned HTTPS URL;
4. enforces size checks;
5. verifies SHA256;
6. stores the model file under `/data/models`;
7. writes a model registry and audit entry;
8. activates the appropriate model slot.

Partial downloads are cleaned up on failure.

## Primary Classifier

The primary classifier is used for image previews and, when enabled, sampled video/GIF frames.

The curated AdamCodd NSFW entries are binary SFW/NSFW models. They do not provide detailed adult subtype labels. Do not infer detailed labels from a binary model.

## OCR

OCR runs locally when:

- an OCR model bundle is installed from the catalog;
- `system.ocr.enabled` is true in `config.db`;
- a scan has image previews or sampled frames to analyze.

OCR stores derived text, confidence, source frame indexes, model hash, and runtime metadata. It does not store OCR crops, media originals, thumbnails, or extracted frames.

OCR text can be searched from the Assets tab.

## Video And GIF Sampling

By default, videos and animated GIFs use Immich preview fallback. To sample originals, set `system.media_sampling.enabled` to true in `config.db` (dashboard admin or API).

Sampling requires:

- ffmpeg in the container or host;
- an Immich API key with original download permission;
- configured size, duration, frame, and timeout limits.

MediaRefinery downloads originals only to a bounded temp file, extracts a small number of frames, analyzes those frames, and deletes temp files after analysis. Oversized, too-long, missing-ffmpeg, or extraction failures fall back to preview analysis and record a safe warning in asset analysis.

## Adult Subtype Profiles

Adult subtype classification is optional and admin supplied. It is meant for review queues only.

An admin profile must include:

- a local server path to an ONNX file;
- explicit output labels;
- thresholds for labels that need custom thresholds;
- preprocessing details when defaults are not suitable;
- admin acknowledgement.

Example payload shape:

```json
{
  "model_id": "local-subtypes",
  "name": "Local Subtypes",
  "model_path": "/data/imports/subtypes.onnx",
  "output_labels": ["label_one", "label_two"],
  "thresholds": {
    "label_one": 0.7,
    "label_two": 0.8
  },
  "admin_acknowledgement": true,
  "input_size": 224,
  "input_mean": [0.0, 0.0, 0.0],
  "input_std": [1.0, 1.0, 1.0],
  "input_name": null,
  "output_name": null
}
```

Registration copies the model into managed storage, computes SHA256, and activates the `adult_subtype` slot. Unknown outputs fail closed. Low-confidence top labels enter `review_needed`.

Subtype labels do not trigger automatic policy actions. They are review signals attached to assets that the primary classifier has already marked sensitive.

## Uninstalling

Uninstalling removes the model file from disk and clears the active slot if the removed model was active. Registry and audit history remain.

You can reinstall catalog models later if the catalog entry is still available.

## Model Trust Checklist

Before installing or registering a model:

- read the license;
- understand what the labels mean;
- verify the intended task and expected input size;
- keep a copy of the model source URL or provenance;
- test on a small scan;
- treat results as review signals, not perfect labels.
