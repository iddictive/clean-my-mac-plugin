---
name: clean-my-mac
description: Diagnose, monitor, and clean macOS disk pressure from safe, regeneratable junk such as package-manager caches, developer caches, repository build outputs, Xcode DerivedData, simulator data, Docker Desktop data, local Trash leftovers, and cloud sync stalls. Use when the user asks to find what is eating SSD space, clean Mac storage, keep repositories from accumulating junk, investigate Finder/bird/cloudd/fileproviderd/iCloud Drive hangs, or create a repeatable macOS cleanup runbook/script.
---

# Clean My Mac

## Safety Gate

Do not run cleanup before a scan/plan step identifies categories and estimated impact. Any destructive or stateful category, including Docker, simulators, Trash, iCloud/File Provider paths, or admin-owned files, requires explicit user acceptance before deletion.

## Workflow

Use the bundled script first unless the user explicitly asks for a manual investigation:

```bash
python3 scripts/clean_my_mac.py scan
python3 scripts/clean_my_mac.py tool-status
python3 scripts/clean_my_mac.py deep-scan --path /System/Volumes/Data
python3 scripts/clean_my_mac.py plan
python3 scripts/clean_my_mac.py app-support-audit
python3 scripts/clean_my_mac.py clean --yes
```

Default `clean --yes` removes only regeneratable caches. Add explicit include flags for destructive or stateful categories:

```bash
python3 scripts/clean_my_mac.py clean --yes --include xcode --include simulators
python3 scripts/clean_my_mac.py clean --yes --include trash
python3 scripts/clean_my_mac.py clean --yes --include docker
```

Run `scan` again after cleanup and report before/after `df -h /System/Volumes/Data`.

## Deep Scan Evidence

Use `deep-scan` as the primary read-only whole-disk audit. It combines a native same-filesystem scanner with optional external adapters:

- native scanner: logical bytes, allocated blocks, hard-link deduplication, symlink and mount exclusion, permission gaps, large files, old files, and same-size duplicate candidates;
- `gdu`: optional independent bounded summary verifier, always invoked with deletion, file viewing, shell spawning, and cross-filesystem traversal disabled; full-tree export is intentionally avoided;
- `dust`: optional JSON tree overview constrained to one filesystem, three levels, and 100 displayed nodes;
- GrandPerspective: physical/logical and cloud-placeholder semantics only; the plugin does not launch the GUI;
- Spacie: APFS clone-awareness, hard-link, duplicate, large/old-file, and permission-coverage concepts only; the plugin does not copy or invoke Spacie code.

Missing optional tools are evidence gaps, not failures. The plugin never installs them. Treat per-file allocated blocks, duplicate candidates, purgeable space, snapshots, and APFS clone-shared storage as different evidence classes. Never report a clone or duplicate estimate as exact reclaimable bytes.

The gdu/dust subprocesses run with isolated config lookup paths so a user-level scanner config cannot silently enable symlink traversal or change the plugin's accounting contract.

Codex runtime, history, credentials and application state, including `~/.codex`, Codex runtime caches, and Codex Application Support, remain outside deep-scan and cleanup scope. Task workspace artifacts use the separate flow below.

## Codex Task Artifacts

Use the Codex view or the dedicated commands to find abandoned downloaded model bundles and reconstructable dependency/build directories in approved Codex task workspaces:

```bash
python3 scripts/clean_my_mac.py codex-scan --json
python3 scripts/clean_my_mac.py codex-scan --root /path/to/codex/task --json
python3 scripts/clean_my_mac.py codex-plan --root /path/to/codex/task --id SCANNED_ID --json
python3 scripts/clean_my_mac.py codex-clean --root /path/to/codex/task --id PLANNED_ID --yes --json
```

Select only eligible rows and accept the concrete plan before cleaning. Each ID binds a path and its current tree; changed or unknown IDs, missing confirmation, active use and incomplete activity evidence block deletion. Empty selection never means all. Plan and clean recheck only the selected artifacts inside the original roots. The default discovery budget is 60 seconds and 500,000 entries; if coverage is incomplete, narrow to the named task or use CLI `--timeout 120 --max-entries 1000000`. The scanner does not classify every large `work` directory or `.safetensors` file as junk. Downloaded model bundles need immutable source/revision evidence and complete unchanged download metadata. Generated dependency links are unlinked without following them; root/ancestor links and model-bundle links remain blocked. Preserve original inputs, trained checkpoints, editable source, delivered outputs and Codex operating data. Measure actual disk free space after cleanup separately from allocated-byte estimates.

## Folder-Scoped Cleanup

When the user names a folder, repository, checkout, or workspace as the suspected source, scan that path before relying on global macOS cache totals. Identify the largest first-level directories and common generated build owners such as Rust/Tauri `target`, web `dist`/`.next`/build output, package-manager caches, and test/browser caches.

For repository-local generated output, verify it is ignored/generated and that no active build owns it before cleanup. Prefer the owning clean command, such as `cargo clean` for Rust targets, over raw deletion when available. Do not remove source, config, secrets, personal data, model artifacts, or product assets unless project guidance or the user explicitly marks them disposable. Report before/after size for both the named folder and the disk.

For app-specific `Application Support` bloat, distinguish user state from regeneratable runtime/install artifacts before cleanup. If the largest paths are installer rollback copies, app bundles, packaged build outputs, local webview/runtime caches, or other generated backups, identify the producer script or app flow and add a retention policy there; do not stop at deleting the current pile.

## App Support Audit

Use `app-support-audit` when large `~/Library/Application Support` owners dominate disk usage. Treat the output as a triage list, not a delete plan. Classify each owner before cleanup:

- `repair-backup`: local rollback or reset backups, such as `iCloud-reset-backup-*`; keep until the repair is verified, then ask for a retention decision.
- `sync-state`: File Provider, CloudDocs, Google Drive, or iCloud state; do not delete from Terminal while sync agents are active.
- `game-state`: Steam, Minecraft, or Modrinth libraries; use app-owned uninstall/storage controls.
- `app-output`: generated media or project output, such as WhisperKiller artifacts; identify useful outputs before deleting.
- `app-state`: app databases, login/session state, local projects, and extensions; reset only after explicit app-level decision.

For RantBar specifically, `~/Library/Caches/rantbar` and `~/Library/Caches/com.rantsports.rantbar` are regeneratable cache candidates. `~/Library/Application Support/RantBar` is stateful app data and must remain manual-only; do not include it in default cleanup. If the user asks for a full RantBar reset, scan size first, quit the app, confirm the reset/retention decision, and include `rantbar-state` only with explicit acceptance.

## Repo Waste Monitoring

When the user asks to keep repositories from accumulating junk, establish a lightweight repo-local size audit instead of relying on one-off manual cleanup. First record a baseline of the largest ignored/generated directories and identify the owner of each large path: build output, dependency cache, test artifact, model/product asset, temp output, or stateful local data.

Use the repo's existing owner for the monitor: package script, Make/task runner, environment launcher, preflight check, CI job, or documented maintenance workflow. The monitor should warn or fail on unexpected generated growth above a clear threshold, while excluding intentional product assets and stateful local data. Do not create hidden background jobs or automatic deleters unless the user explicitly asks for automation; cleanup still needs the scan/plan safety gate.

## Safety Rules

- Do not delete `Photos Library.photoslibrary/originals` manually. Tell the user to enable Photos -> Settings -> iCloud -> Optimize Mac Storage.
- Do not delete `~/Library/Mobile Documents/.Trash` with `rm -rf`. iCloud Drive trash can be File Provider placeholders and recursive deletes can hang `Finder`, `bird`, `cloudd`, or `fileproviderd`. Use Finder/iCloud UI, iCloud repair, or let sync finish.
- Do not treat Docker Desktop data as a cache unless the user accepts losing local containers, images, build cache, and volumes.
- Do not treat iOS Simulator devices as a pure cache unless the user accepts losing local simulator state.
- Do not treat app-specific `Application Support` directories as cache by default, including RantBar, Steam, WhisperKiller, Minecraft/Modrinth, FileProvider, CloudDocs, or iCloud reset backups.
- Do not use `sudo` unless the user explicitly approves an admin-password operation.
- Do not delete personal data folders: `Documents`, `Desktop`, `Pictures`, `Movies`, `Music`, repositories, or cloud roots.
- Do not scan or clean Codex runtime, history, credentials or application state. Use only the dedicated selected-artifact flow for approved Codex task workspaces.

## Cloud Sync Diagnosis

When Finder, `bird`, `cloudd`, or `fileproviderd` are hot, gather direct evidence:

```bash
ps -axo pid,%cpu,%mem,etime,comm,args | awk 'NR==1 || /\/bird($| )|\/cloudd($| )|\/fileproviderd($| )|Finder.app|Google Drive.app/'
brctl status 2>/tmp/brctl-status.err | sed -n '1,120p'
fileproviderctl dump -l 2>/tmp/fileprovider-dump.err | grep -E 'display name:|^domain:|reconciliation \(|processing requests' | sed -n '1,120p'
du -sh "$HOME/Library/Mobile Documents/.Trash" "$HOME/Library/CloudStorage" 2>/tmp/cloud-du.err
```

If iCloud Drive shows `needs-sync`, `pending-scan`, `needs-sync-up`, or many reconciliation entries under `/.Trash`, stop terminal deletion attempts and route the user to Finder/iCloud repair. If Google Drive is present, quit Google Drive Desktop temporarily to separate Google File Provider load from iCloud load.

## Reporting

Report three buckets:

1. Cleaned: exact categories and approximate reclaimed space.
2. Left intentionally: Photos originals, iCloud Trash, root-owned Trash, Docker/simulator state if not included.
3. Next manual actions: Photos Optimize Mac Storage, Finder iCloud Repair, Google Drive pause/quit test, admin-approved Trash cleanup when needed.
