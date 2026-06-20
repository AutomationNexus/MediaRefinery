# User Guide

This guide follows the dashboard from first setup through daily review.

## Setup Wizard

When MediaRefinery starts on a fresh database, the dashboard opens the setup wizard.

1. Accept the setup terms.
2. Sign in with your Immich credentials.
3. Install a primary classifier model.
4. Save an Immich API key.
5. Start the first scan.

The first successful user after setup acceptance becomes the local MediaRefinery admin. Admins can install models and register adult subtype profiles.

## Signing In

MediaRefinery forwards your email/username and password to Immich. It does not store your password.

After Immich accepts the login, MediaRefinery stores an encrypted Immich session token server-side and sets signed browser cookies for the dashboard session.

## Models

Models are managed from the Models tab. A primary classifier is required before real scans can run.

Normal users can see installed models. Admin users can:

- install catalog models after accepting the license;
- uninstall installed models;
- register a local adult subtype ONNX profile.

See [models.md](models.md) for details.

## API Key Setup

Real scans require an Immich API key because background scans cannot use your password.

In the dashboard:

1. Open Settings or the setup wizard API key step.
2. Paste an Immich API key.
3. Leave validation enabled.
4. Save.

The key is encrypted at rest. If validation fails, check the Immich URL, key value, and permissions. See [../admin/configuration.md](../admin/configuration.md#immich-api-keys).

## Scans

Start scans from the dashboard. Each scan:

- fetches assets visible to the user from Immich;
- classifies previews with the active local model;
- optionally samples bounded video/GIF frames when enabled;
- optionally extracts OCR text when an OCR model is installed;
- records derived analysis and actions in SQLite;
- writes safe actions to Immich only when policy and mode allow it.

The first setup scan is dry-run oriented so you can review behavior before relying on actions.

## Runs

The Runs tab shows scan history.

Open a run to see:

- status;
- start and end times;
- action names;
- target asset IDs;
- success, dry-run, or error outcome.

Undo marks successful actions as reverted in the audit log. Locked Folder moves can be reverted through the Locked Folder PIN flow. Tag and album writes are not automatically removed at the Immich source.

## Assets

The Assets tab is the main review surface. It lists scanned assets and their latest derived signals.

You can filter by:

- review queue;
- media kind;
- event group;
- metadata/OCR search;
- Immich semantic search when Smart Search is available.

Asset cards show the latest action, category, analysis badges, preview, and details. Details can include safety score, people metadata, OCR text, document hints, quality flags, adult subtype result, and event membership.

You can override an asset category from the asset card. Overrides are local MediaRefinery state and do not change Immich metadata.

## Search

Metadata search checks stored analysis JSON, asset IDs, OCR text, people names, event titles, and category terms.

Semantic search calls Immich Smart Search with your current Immich session. Results are mapped back to assets already visible in your MediaRefinery scan history. If Immich Smart Search is unavailable, the dashboard shows a metadata fallback source.

MediaRefinery does not store search queries in SQLite.

## Events

Event groups are local review groups built from available metadata:

- date bucket;
- place metadata;
- Immich People names or IDs;
- album names;
- semantic terms.

You can:

- rename an event;
- merge events;
- split selected assets into a new event;
- remove an asset from an event;
- reset an event back to automatic grouping.

Event edits are local MediaRefinery state. They do not rename Immich albums, move assets, delete media, or change people metadata in Immich.

## Settings

Settings include:

- categories and match terms;
- advanced policies JSON;
- auto-scan schedule;
- Locked Folder revert flow;
- account deletion.

Policy actions are deliberately limited to safe review actions. Automatic delete and trash are not supported.

## Locked Folder

Forward moves to Immich Locked Folder use your stored Immich API key.

Reverting Locked Folder moves requires your Immich Locked Folder PIN. The PIN is sent to Immich for the single request and is not stored, logged, or returned.

## Audit

The Audit tab records important state changes and actions, including:

- login/session events;
- model installs and license acceptance;
- scan starts and finishes;
- action attempts;
- event edits;
- account purge activity.

Audit rows are scoped to the current user. When a user deletes their MediaRefinery account, that user's audit rows are anonymized.

## Safe Use Tips

- Start with dry-run style policies.
- Review a small scan before enabling Immich write actions.
- Keep `/data/state.db` and `/data/master.key` backed up together.
- Use HTTPS for real users.
- Treat OCR text and people names as sensitive metadata.
- Keep model licenses and model provenance under review.
