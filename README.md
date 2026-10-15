<h1 align="center">Clean My Mac for Codex</h1>

<p align="center">
  <strong>Find the files eating your SSD.</strong><br>
  An interactive disk audit and cleanup panel, right inside Codex.
</p>

<p align="center">
  <a href="#install"><img src="https://img.shields.io/badge/macOS-111111?style=flat-square&logo=apple&logoColor=white" alt="macOS"></a>
  <a href="plugins/clean-my-mac-app/.codex-plugin/plugin.json"><img src="https://img.shields.io/badge/Codex-plugin-2663EB?style=flat-square" alt="Codex plugin"></a>
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#what-it-finds">What it finds</a> ·
  <a href="#you-choose-what-goes">Cleanup controls</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

Dependencies, build folders and model downloads can outlive the task that created them. Clean My Mac helps you find those leftovers, inspect the largest storage owners and review a cleanup plan on your Mac.

## Install

**macOS · Codex with plugin support · Node.js · Python 3.12+**

Run these commands in your terminal:

```bash
codex plugin marketplace add iddictive/clean-my-mac-plugin
codex plugin add clean-my-mac-app@clean-my-mac-local
```

Open a **new Codex chat**, then ask:

> Open Clean My Mac and find what's taking up disk space. Check unused Codex build folders and downloaded models, and show me a cleanup plan before removing anything.

Codex opens the interactive panel. Scans and cleanup run locally.

<details>
<summary><strong>Updating an existing installation</strong></summary>

For a marketplace installed from this GitHub repository:

```bash
codex plugin marketplace upgrade clean-my-mac-local
codex plugin add clean-my-mac-app@clean-my-mac-local
```

Start a new chat to load the updated tools.

</details>

## What it finds

| Looking for… | Open… |
| --- | --- |
| The biggest files and folders on your SSD | **Deep scan** — file sizes, allocated storage and actual volume free space. |
| Caches that can be regenerated | **Cleanup plan** — package, browser and developer caches, grouped for review. |
| Build folders and downloaded models left behind by Codex | **Codex** — select inactive artifacts with evidence that they can be recreated. |
| An app or cloud folder that keeps growing | **Application Support / Cloud** — inspect storage owners and sync status before choosing a remedy. |

APFS copies can share storage, so adding up folder sizes can overstate what deletion will free. The audit keeps logical size, allocated bytes and volume free space separate, and reports gaps in scan coverage.

## You choose what goes

For Codex artifacts, the flow is:

**Scan → Select → Review plan → Confirm → Clean**

The plugin checks the selected files again before deleting. Active use, changed contents or missing evidence block artifact removal. An empty selection never means “clean everything.”

**Deletion is permanent.** Removed downloads and builds must be recreated. The plugin runs approved cleanup; it does not install a background sweeper or impose storage quotas.

<details>
<summary><strong>What qualifies for Codex cleanup?</strong></summary>

- Ignored Node dependency/build directories, Rust build output and Python environments with retained dependency files.
- Complete pretrained Hugging Face downloads pinned to a repository revision, with matching download metadata.

A large folder or a `.safetensors` extension alone does not qualify. Original inputs, editable sources, trained checkpoints, delivered outputs, Git history, Codex history, credentials and runtime state are preserved. Checks inspect selected task workspaces, not Codex's operating data.

Planning and deletion revalidate provenance, active processes and tree identity. Symlink roots, nested mounts, cloud placeholders and incomplete evidence prevent artifact deletion. Ordinary cache cleanup has its own reviewed category plan; Docker data, simulator devices, Trash and app resets require an explicit category choice.

Allocated-byte totals are estimates. Check volume free space after cleanup for the actual change.

</details>

## For developers

The plugin uses a standard-library Python engine and a dependency-free Node.js MCP server. The interactive panel and CLI share the same artifact classifier.

<details>
<summary><strong>Run the command-line tools</strong></summary>

Requires Node.js and Python 3.12+ on `PATH`.

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

Optional `gdu` and `dust` adapters add independent scan evidence. Missing tools are reported; the plugin does not install them.

</details>

<details>
<summary><strong>Source layout and checks</strong></summary>

The plugin lives in [`plugins/clean-my-mac-app`](plugins/clean-my-mac-app). Its Python engine owns cleanup policy, [`mcp/server.mjs`](plugins/clean-my-mac-app/mcp/server.mjs) exposes the MCP tools, and [`mcp/clean-my-mac-app.html`](plugins/clean-my-mac-app/mcp/clean-my-mac-app.html) owns the panel. MCP and CLI use the same artifact classifier.

```bash
python3 -m unittest discover -s plugins/clean-my-mac-app/tests -v
```

The suite covers scanner accounting, partial/unavailable adapters, MCP protocol, selection validation, activity protection, and cleanup safety. No model download or large build is needed to run it.

</details>

---

This is an independent Codex plugin and is not affiliated with MacPaw's CleanMyMac.
