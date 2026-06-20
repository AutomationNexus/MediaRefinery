# Model Catalog

MediaRefinery uses a curated model catalog at [catalog.json](catalog.json). The repository and Docker image do not include model weights.

## Catalog Fields

Each catalog entry describes:

- `id`: stable model identifier;
- `name`: display name;
- `kind` and `task`: runtime slot and purpose;
- `status`: `verified`, `unavailable`, or `pending`;
- `url`: HTTPS model download URL when installable;
- `sha256`: expected model hash;
- `size_bytes`: expected download size;
- `license` and `license_url`;
- `license_text_path`: bundled license text when available;
- `presets`: suggested use labels for the dashboard.

The service refuses to install entries that are not marked installable.

## Trust Rules

Installable catalog entries must:

- use HTTPS download URLs;
- include a reviewed SHA256;
- include license metadata;
- pass size and hash checks before activation;
- keep unavailable or unsuitable upstream models marked unavailable instead of silently substituting another artifact.

## Bundled License Texts

License text files in [licenses/](licenses/) are included so users can review model license terms before installation.

## Updating The Catalog

Catalog changes should include:

- source URL and provenance review;
- exact SHA256;
- exact size;
- license review;
- runtime task and input expectations;
- tests or manual install verification.

Do not commit model weights to the repository.
