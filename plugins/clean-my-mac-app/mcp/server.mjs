#!/usr/bin/env node
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pluginRoot = dirname(__dirname);
const cleanScript = join(pluginRoot, "skills", "clean-my-mac", "scripts", "clean_my_mac.py");
const widgetUri = "ui://clean-my-mac/app-v2.html";
const widgetPath = join(__dirname, "clean-my-mac-app.html");
const allowedIncludes = new Set(["xcode", "simulators", "docker", "trash", "rantbar-state"]);

const tools = [
  ...["scan", "plan", "clean"].map((action) => ({
    name: `clean_my_mac_codex_${action}`,
    title: `${action === "scan" ? "Scan" : action === "plan" ? "Plan" : "Clean"} Codex Task Artifacts`,
    description: action === "scan"
      ? "Discover bounded Codex task artifact candidates read-only. User data, history, credentials, and runtime state are excluded."
      : action === "plan"
        ? "Review exact selected artifact IDs from a Codex artifact scan. Empty selection is rejected."
        : "Delete only the exact artifact IDs in the accepted plan. Requires yes=true; changed or stale IDs are rejected.",
    inputSchema: codexSchema(action),
    annotations: { readOnlyHint: action !== "clean", destructiveHint: action === "clean", openWorldHint: false },
    _meta: {
      "openai/toolInvocation/invoking": `${action === "scan" ? "Scanning" : action === "plan" ? "Planning" : "Cleaning"} Codex task artifacts`,
      "openai/toolInvocation/invoked": "Codex artifact result ready",
    },
  })),
  {
    name: "open_clean_my_mac_app",
    title: "Open Clean My Mac",
    description: "Render the Clean My Mac control panel for scanning, planning, and explicitly confirmed cleanup.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      openWorldHint: false,
    },
    _meta: {
      ui: { resourceUri: widgetUri, visibility: ["model", "app"] },
      "openai/outputTemplate": widgetUri,
      "openai/widgetAccessible": true,
      "openai/toolInvocation/invoking": "Opening Clean My Mac",
      "openai/toolInvocation/invoked": "Clean My Mac ready",
    },
  },
  {
    name: "clean_my_mac_scan",
    title: "Scan Disk Junk",
    description: "Scan safe macOS cleanup candidates and return disk/category sizes. This does not delete anything.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      openWorldHint: false,
    },
    _meta: {
      "openai/toolInvocation/invoking": "Scanning disk junk",
      "openai/toolInvocation/invoked": "Scan complete",
    },
  },
  {
    name: "clean_my_mac_deep_scan",
    title: "Deep Disk Scan",
    description: "Run a read-only physical/logical audit with native evidence, optional gdu verification, optional dust overview, APFS volume facts, and explicit disagreements. Codex paths are excluded.",
    inputSchema: {
      type: "object",
      properties: {
        path: {
          type: "string",
          default: "/System/Volumes/Data",
          description: "Absolute local directory to scan. Codex-owned paths are rejected.",
        },
        maxEntries: { type: "integer", minimum: 1, maximum: 5000000, default: 1000000 },
        timeoutSeconds: { type: "integer", minimum: 1, maximum: 900, default: 300 },
        top: { type: "integer", minimum: 1, maximum: 100, default: 30 },
      },
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      openWorldHint: false,
    },
    _meta: {
      "openai/toolInvocation/invoking": "Running deep disk scan",
      "openai/toolInvocation/invoked": "Deep scan complete",
    },
  },
  {
    name: "clean_my_mac_tool_status",
    title: "Check Disk Scanner Coverage",
    description: "Report native, gdu, dust, GrandPerspective, and Spacie availability and the plugin's read-only safety policy without installing or launching anything.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      openWorldHint: false,
    },
    _meta: {
      "openai/toolInvocation/invoking": "Checking scanner coverage",
      "openai/toolInvocation/invoked": "Scanner coverage ready",
    },
  },
  {
    name: "clean_my_mac_plan",
    title: "Plan Cleanup",
    description: "Show the cleanup plan for default regeneratable caches and optional accepted categories.",
    inputSchema: includeSchema(),
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      openWorldHint: false,
    },
    _meta: {
      "openai/toolInvocation/invoking": "Building cleanup plan",
      "openai/toolInvocation/invoked": "Plan ready",
    },
  },
  {
    name: "clean_my_mac_clean",
    title: "Clean Disk Junk",
    description: "Remove selected regeneratable cleanup targets. Requires yes=true and should only be called after reviewing a plan.",
    inputSchema: {
      type: "object",
      properties: {
        include: includeSchema().properties.include,
        yes: {
          type: "boolean",
          description: "Must be true after the user explicitly accepts the cleanup plan.",
        },
      },
      required: ["yes"],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: true,
      openWorldHint: false,
    },
    _meta: {
      "openai/toolInvocation/invoking": "Cleaning selected junk",
      "openai/toolInvocation/invoked": "Cleanup finished",
    },
  },
  {
    name: "clean_my_mac_cloud_status",
    title: "Check Cloud Sync",
    description: "Inspect Finder, iCloud, bird, cloudd, fileproviderd, and Google Drive status without deleting anything.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      openWorldHint: false,
    },
    _meta: {
      "openai/toolInvocation/invoking": "Checking cloud sync",
      "openai/toolInvocation/invoked": "Cloud status ready",
    },
  },
  {
    name: "clean_my_mac_app_support_audit",
    title: "Audit Application Support",
    description: "List the largest Application Support owners and classify stateful app data, repair backups, and app-owned cache/state mixes.",
    inputSchema: {
      type: "object",
      properties: {
        limit: {
          type: "integer",
          minimum: 1,
          maximum: 50,
          default: 20,
        },
      },
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      openWorldHint: false,
    },
    _meta: {
      "openai/toolInvocation/invoking": "Auditing app support",
      "openai/toolInvocation/invoked": "App support ready",
    },
  },
];

function codexSchema(action) {
  return {
    type: "object",
    properties: {
      roots: {
        type: "array", items: { type: "string", minLength: 1, pattern: "^/" },
        maxItems: 32, uniqueItems: true,
        description: "Optional absolute task roots for bounded discovery. The backend validates every root; preserve scan roots for plan and clean.",
      },
      ...(action !== "scan" ? {
        ids: { type: "array", items: { type: "string", minLength: 1 }, minItems: 1, maxItems: 200, uniqueItems: true,
          description: "Exact eligible candidate IDs returned by scan and reviewed in plan; never paths." },
      } : {}),
      ...(action === "clean" ? { yes: { type: "boolean", description: "Must be true after the user accepts this exact selected plan." } } : {}),
    },
    ...(action !== "scan" ? { required: action === "clean" ? ["ids", "yes"] : ["ids"] } : {}),
    additionalProperties: false,
  };
}

function codexArguments(action, input) {
  if (action === "clean" && input.yes !== true) throw new Error("codex_clean_requires_yes_true");
  const roots = input.roots ?? [];
  if (!Array.isArray(roots) || roots.length > 32 || roots.some((root) => typeof root !== "string" || !root.startsWith("/") || root.includes("\0"))) {
    throw new Error("codex_requires_absolute_roots");
  }
  const ids = action === "scan" ? [] : input.ids;
  if (!Array.isArray(ids) || (action !== "scan" && !ids.length) || ids.length > 200 || ids.some((id) => typeof id !== "string" || !id.trim() || id.startsWith("-") || id.includes("\0"))) {
    throw new Error("codex_requires_nonempty_ids");
  }
  return [`codex-${action}`, "--json", ...(action === "clean" ? ["--yes"] : []),
    ...[...new Set(roots)].flatMap((root) => ["--root", root]),
    ...[...new Set(ids)].flatMap((id) => ["--id", id])];
}

function includeSchema() {
  return {
    type: "object",
    properties: {
      include: {
        type: "array",
        items: { type: "string", enum: [...allowedIncludes] },
        uniqueItems: true,
        default: [],
        description: "Optional manual categories accepted by the user: xcode, simulators, docker, trash, rantbar-state.",
      },
    },
    additionalProperties: false,
  };
}

function normalizeIncludes(input = {}) {
  const include = Array.isArray(input.include) ? input.include : [];
  const invalid = include.filter((item) => !allowedIncludes.has(item));
  if (invalid.length > 0) {
    throw new Error(`Unsupported include categories: ${invalid.join(", ")}`);
  }
  return [...new Set(include)];
}

function runCleanScript(args, timeoutMs = 330000) {
  return new Promise((resolve) => {
    const child = spawn("python3", [cleanScript, ...args], {
      cwd: pluginRoot,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
      detached: true,
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;
    let forceKillTimer;
    const timer = setTimeout(() => {
      if (settled) return;
      timedOut = true;
      try {
        process.kill(-child.pid, "SIGTERM");
      } catch {
        child.kill("SIGTERM");
      }
      forceKillTimer = setTimeout(() => {
        if (settled) return;
        try {
          process.kill(-child.pid, "SIGKILL");
        } catch {
          child.kill("SIGKILL");
        }
      }, 2000);
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      clearTimeout(forceKillTimer);
      resolve({ code: 1, stdout, stderr: error.message });
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      clearTimeout(forceKillTimer);
      resolve({
        code: timedOut ? 124 : (code ?? 1),
        stdout,
        stderr: timedOut ? `${stderr}\nPlugin command timed out.`.trim() : stderr,
      });
    });
  });
}

function parseJsonOutput(output) {
  try {
    return JSON.parse(output);
  } catch {
    return { raw: output.trim() };
  }
}

async function callCleanTool(name, input = {}) {
  if (name === "open_clean_my_mac_app") {
    return {
      structuredContent: {
        status: "ready",
        includeOptions: [...allowedIncludes],
        safety: "Run scan and plan before cleanup. Cleanup requires yes=true. App support state is manual-only.",
      },
      content: [{ type: "text", text: "Clean My Mac control panel is ready." }],
    };
  }

  if (name === "clean_my_mac_clean" && input.yes !== true) {
    return {
      isError: true,
      structuredContent: { error: "clean_requires_yes_true" },
      content: [{ type: "text", text: "Refusing to clean without yes=true after the user accepts the plan." }],
    };
  }

  if (name === "clean_my_mac_deep_scan" && input.path !== undefined && (typeof input.path !== "string" || !input.path.startsWith("/"))) {
    return {
      isError: true,
      structuredContent: { error: "deep_scan_requires_absolute_path" },
      content: [{ type: "text", text: "Deep scan requires an absolute local path." }],
    };
  }

  let args = [];
  if (["clean_my_mac_codex_scan", "clean_my_mac_codex_plan", "clean_my_mac_codex_clean"].includes(name)) {
    try {
      args = codexArguments(name.slice("clean_my_mac_codex_".length), input);
    } catch (error) {
      return { isError: true, structuredContent: { error: error.message }, content: [{ type: "text", text: error.message }] };
    }
  } else if (name === "clean_my_mac_scan") {
    args = ["scan", "--json"];
  } else if (name === "clean_my_mac_deep_scan") {
    const path = typeof input.path === "string" ? input.path : "/System/Volumes/Data";
    const maxEntries = Number.isInteger(input.maxEntries) ? Math.min(Math.max(input.maxEntries, 1), 5000000) : 1000000;
    const timeoutSeconds = Number.isInteger(input.timeoutSeconds) ? Math.min(Math.max(input.timeoutSeconds, 1), 900) : 300;
    const top = Number.isInteger(input.top) ? Math.min(Math.max(input.top, 1), 100) : 30;
    args = [
      "deep-scan",
      "--json",
      "--path", path,
      "--max-entries", String(maxEntries),
      "--timeout", String(timeoutSeconds),
      "--top", String(top),
    ];
  } else if (name === "clean_my_mac_tool_status") {
    args = ["tool-status", "--json"];
  } else if (name === "clean_my_mac_plan") {
    args = ["plan", "--json", ...normalizeIncludes(input).flatMap((item) => ["--include", item])];
  } else if (name === "clean_my_mac_clean") {
    args = ["clean", "--json", "--yes", ...normalizeIncludes(input).flatMap((item) => ["--include", item])];
  } else if (name === "clean_my_mac_cloud_status") {
    args = ["cloud-status"];
  } else if (name === "clean_my_mac_app_support_audit") {
    const limit = Number.isInteger(input.limit) ? Math.min(Math.max(input.limit, 1), 50) : 20;
    args = ["app-support-audit", "--json", "--limit", String(limit)];
  } else {
    return {
      isError: true,
      content: [{ type: "text", text: `Unknown tool: ${name}` }],
    };
  }

  const timeoutMs = name === "clean_my_mac_deep_scan"
    ? ((Number.isInteger(input.timeoutSeconds) ? Math.min(Math.max(input.timeoutSeconds, 1), 900) : 300) + 30) * 1000
    : 330000;
  const result = await runCleanScript(args, timeoutMs);
  const parsed = name === "clean_my_mac_cloud_status" ? { output: result.stdout.trim() } : parseJsonOutput(result.stdout);
  let summary = result.code === 0 ? "Command completed." : `Command failed with exit code ${result.code}.`;
  if (result.code === 0 && name === "clean_my_mac_tool_status") {
    const available = Object.entries(parsed.tools ?? {}).filter(([, value]) => value.status === "available").map(([key]) => key);
    summary = `Scanner coverage ready: ${available.join(", ") || "native only"}.`;
  } else if (result.code === 0 && name === "clean_my_mac_deep_scan") {
    const summaryResult = parsed.summary ?? {};
    const isPartial = parsed.status === "partial" || parsed.native?.status === "partial";
    const stopReason = parsed.summary?.stop_reason ?? parsed.native?.stop_reason ?? "partial";
    const statusLabel = isPartial ? `Deep scan partial (${stopReason})` : "Deep scan complete";
    summary = `${statusLabel}: ${summaryResult.allocated_bytes ?? 0} allocated bytes, ${summaryResult.logical_bytes ?? 0} logical bytes, ${(parsed.disagreements ?? []).length} material disagreement(s).`;
  }
  return {
    isError: result.code !== 0,
    structuredContent: {
      command: args[0],
      exitCode: result.code,
      result: parsed,
      stderr: result.stderr.trim() || undefined,
    },
    content: [{ type: "text", text: summary }],
  };
}

function makeResponse(id, result) {
  return { jsonrpc: "2.0", id, result };
}

function makeError(id, code, message) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

async function handle(message) {
  if (message.method === "notifications/initialized") {
    return null;
  }
  if (message.method === "initialize") {
    return makeResponse(message.id, {
      protocolVersion: "2025-06-18",
      capabilities: {
        tools: {},
        resources: {},
      },
      serverInfo: {
        name: "clean-my-mac-app",
        version: "0.2.0",
      },
      instructions:
        "Use deep scan, scan, plan, app-support audit, and cloud status before cleanup. Deep scan is read-only and excludes Codex-owned paths. Never call clean_my_mac_clean unless the user explicitly accepts the plan and yes=true is provided. Codex task artifacts use clean_my_mac_codex_scan, clean_my_mac_codex_plan and clean_my_mac_codex_clean: retain scan roots, review exact nonempty selected IDs, then clean only accepted IDs with yes=true. User data, history, credentials and Codex runtime state are excluded and never default cleanup targets.",
    });
  }
  if (message.method === "ping") {
    return makeResponse(message.id, {});
  }
  if (message.method === "tools/list") {
    return makeResponse(message.id, { tools });
  }
  if (message.method === "tools/call") {
    const result = await callCleanTool(message.params?.name, message.params?.arguments ?? {});
    return makeResponse(message.id, result);
  }
  if (message.method === "resources/list") {
    return makeResponse(message.id, {
      resources: [
        {
          uri: widgetUri,
          name: "Clean My Mac App",
          title: "Clean My Mac App",
          mimeType: "text/html;profile=mcp-app",
          description: "Interactive launcher for safe macOS disk cleanup, app-support audit, and cloud sync diagnostics.",
        },
      ],
    });
  }
  if (message.method === "resources/read") {
    if (message.params?.uri !== widgetUri) {
      return makeError(message.id, -32602, `Unknown resource: ${message.params?.uri ?? ""}`);
    }
    const html = await readFile(widgetPath, "utf8");
    return makeResponse(message.id, {
      contents: [
        {
          uri: widgetUri,
          mimeType: "text/html;profile=mcp-app",
          text: html,
          _meta: {
            ui: {
              prefersBorder: true,
              csp: { connectDomains: [], resourceDomains: [] },
            },
            "openai/widgetDescription":
              "A local Clean My Mac launcher for scan, plan, explicit cleanup, app-support audit, and cloud sync diagnostics.",
            "openai/widgetPrefersBorder": true,
            "openai/widgetCSP": {
              connect_domains: [],
              resource_domains: [],
            },
          },
        },
      ],
    });
  }
  return makeError(message.id, -32601, `Method not found: ${message.method}`);
}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", async (chunk) => {
  buffer += chunk;
  const lines = buffer.split(/\r?\n/);
  buffer = lines.pop() ?? "";
  for (const line of lines) {
    if (!line.trim()) continue;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      process.stdout.write(`${JSON.stringify(makeError(null, -32700, "Parse error"))}\n`);
      continue;
    }
    try {
      const response = await handle(message);
      if (response && message.id !== undefined) {
        process.stdout.write(`${JSON.stringify(response)}\n`);
      }
    } catch (error) {
      process.stdout.write(`${JSON.stringify(makeError(message.id, -32000, error.message))}\n`);
    }
  }
});
