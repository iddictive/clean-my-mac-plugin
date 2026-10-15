# Clean My Mac for Codex

Find what is filling your Mac's SSD and review exactly what can be removed. Clean My Mac is a local Codex plugin with an interactive disk audit and cleanup panel.

[Install](#install) · [Usage](#usage) · [How cleanup works](#how-cleanup-works) · [Changelog](CHANGELOG.md)

## Install

Requires macOS, Codex with plugin support, Node.js, and Python 3.12 or newer on `PATH`. The backend uses Python's standard library; the MCP server has no npm dependencies.

```bash
codex plugin marketplace add iddictive/clean-my-mac-plugin
codex plugin add clean-my-mac-app@clean-my-mac-local
```

Start a new Codex chat after installation, then ask:

> Use Clean My Mac to find what is using disk space and show me a cleanup plan.

For task leftovers:

> Use Clean My Mac to find unused Codex build directories and downloaded model bundles. Show me the exact selection before cleaning.

The marketplace identifier is `clean-my-mac-local`. Scanning and cleanup run on your Mac; there is no hosted cleanup service.

To update a Git-installed marketplace and reinstall the current plugin:

```bash
codex plugin marketplace upgrade clean-my-mac-local
codex plugin add clean-my-mac-app@clean-my-mac-local
```

Start a new chat to load the updated tools.

## Usage

The panel provides disk scans, a cleanup plan, cloud sync status, Application Support triage, and a dedicated **Codex** view.

- **Deep scan:** compare logical file sizes, allocated blocks, actual volume free space, large files, and incomplete scan coverage. APFS clones and duplicate candidates are reported separately from recoverable space.
- **Cache cleanup:** review regeneratable package, browser, and developer caches. Docker data, simulator devices, Trash, and app resets require an explicit category choice.
- **Codex artifacts:** select inactive dependency/build directories and downloaded model bundles whose retained source or download metadata establishes how to recreate them.
- **Cloud and app storage:** inspect sync status and large Application Support owners before choosing an app's own recovery or storage controls.

Optional `gdu` and `dust` adapters add independent scan evidence. Missing tools are reported; the plugin does not install them.

## How cleanup works

The Codex flow is **scan → select → plan → confirm → clean**. A selection ID binds an artifact path to its observed tree. Planning and deletion recheck provenance, active processes, and tree identity. Changed files, incomplete evidence, active use, symlink roots, nested mounts, and cloud placeholders prevent deletion. Empty selection never means “clean everything.”

Eligible artifacts include ignored Node dependency/build directories, Rust build output and Python environments with retained dependency files, and complete pretrained Hugging Face downloads pinned to a repository revision. A large directory or a `.safetensors` extension alone does not qualify.

Original inputs, editable sources, trained checkpoints, delivered outputs, Git history, Codex history, credentials, and runtime state are preserved. Codex artifact checks inspect selected task workspaces; they do not purge Codex's operating data. These checks concern the dedicated artifact flow; ordinary cache cleanup has its own reviewed category plan.

Deletion is permanent. Downloads and builds must be recreated afterward. Allocated-byte totals are estimates; measure volume free space after cleanup to see the actual change.

This plugin performs approved cleanup. It does not install a background sweeper or impose storage quotas on other applications.

## Command line

From a source checkout:

```bash
git clone https://github.com/iddictive/clean-my-mac-plugin.git
cd clean-my-mac-plugin
python3 plugins/clean-my-mac-app/skills/clean-my-mac/scripts/clean_my_mac.py --help
```

Read-only examples:

```bash
python3 plugins/clean-my-mac-app/skills/clean-my-mac/scripts/clean_my_mac.py tool-status
python3 plugins/clean-my-mac-app/skills/clean-my-mac/scripts/clean_my_mac.py deep-scan --path /System/Volumes/Data --json
python3 plugins/clean-my-mac-app/skills/clean-my-mac/scripts/clean_my_mac.py codex-scan --root /absolute/path/to/task --json
```

Use an eligible ID returned by that scan, keeping the same root:

```bash
python3 plugins/clean-my-mac-app/skills/clean-my-mac/scripts/clean_my_mac.py codex-plan --root /absolute/path/to/task --id SCANNED_ID --json
python3 plugins/clean-my-mac-app/skills/clean-my-mac/scripts/clean_my_mac.py codex-clean --root /absolute/path/to/task --id PLANNED_ID --yes --json
```

`SCANNED_ID`, `PLANNED_ID`, and the task path are placeholders. IDs are selection evidence, not authorization to bypass a rejected path. The default artifact budget is 60 seconds and 500,000 entries; narrow the root when coverage is incomplete. CLI limits can increase to 120 seconds and 1,000,000 entries.

## Development

The plugin lives in [`plugins/clean-my-mac-app`](plugins/clean-my-mac-app). Its Python engine owns cleanup policy, [`mcp/server.mjs`](plugins/clean-my-mac-app/mcp/server.mjs) exposes the MCP tools, and [`mcp/clean-my-mac-app.html`](plugins/clean-my-mac-app/mcp/clean-my-mac-app.html) owns the panel. MCP and CLI use the same artifact classifier.

```bash
python3 -m unittest discover -s plugins/clean-my-mac-app/tests -v
```

The suite covers scanner accounting, partial/unavailable adapters, MCP protocol, selection validation, activity protection, and cleanup safety. No model download or large build is needed to run it.

This is an independent Codex plugin and is not affiliated with MacPaw's CleanMyMac.
