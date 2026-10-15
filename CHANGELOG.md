# Changelog

## 0.1.0+codex.20261015150328 — 2026-10-15

### Added

- A Codex panel for reviewing reconstructable task artifacts: dependency/build directories, Python environments, and pinned pretrained model downloads.
- Shared `codex-scan`, `codex-plan`, and `codex-clean` commands exposed through MCP and CLI.
- Exact selection binding and provenance checks, with active-use checks before deletion. Incomplete evidence, changed selections, protected content, mounts, and unsafe links preserve the artifact.

### Fixed

- Planning and cleanup validate only selected artifacts, avoiding repeated whole-workspace scans.
- Bounded discovery reports incomplete coverage instead of accepting a partial scan.
- Valid Finder `.DS_Store` updates no longer invalidate an otherwise unchanged selection. A metadata-only directory-removal race receives one bounded retry; new meaningful data still stops cleanup.

## 2026-10-07

- Added gdu-go adapter detection and explicit partial-coverage reporting.
- Corrected APFS free-space reporting and kept logical, allocated, purgeable, and clone-related evidence distinct.

## 2026-08-15

- Added read-only deep scans with native accounting and optional scanner adapters.
- Exposed deep scans in the interactive panel.

## 2026-07-01

- Initial local Codex plugin with cache scan, cleanup planning, cloud status, and Application Support triage.
