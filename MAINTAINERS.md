# Maintainers

MediaRefinery is maintained by repository collaborators with commit and release access.

## Responsibilities

Maintainers are expected to:

- keep user-facing docs current with behavior changes;
- review privacy and security impact for auth, logging, scans, model downloads, and Immich write actions;
- require tests or clear manual verification for behavior changes;
- keep release notes accurate;
- avoid committing secrets, user media, model weights, local databases, or generated dashboard assets;
- use the release checklist before publishing stable tags.

## Release Ownership

A release owner should:

1. run or verify the checks in [docs/releases/release-checklist.md](docs/releases/release-checklist.md);
2. confirm docs and changelog entries are current;
3. open a release pull request;
4. wait for CI to pass;
5. merge through the normal branch protection path;
6. tag the release with a semantic-version tag such as `2.1.0`;
7. verify the GitHub Release and GHCR image tags.

## Security Reports

Security reports should be handled through GitHub private vulnerability reporting. Coordinate fixes and advisories before public disclosure.
