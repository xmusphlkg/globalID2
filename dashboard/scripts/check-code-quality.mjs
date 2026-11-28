#!/usr/bin/env node
/**
 * Lightweight zero-dependency static quality check for the GlobalID dashboard.
 * Runs alongside `tsc --noEmit` via `npm run lint`.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const dashboardRoot = path.resolve(__dirname, "..");

const SOURCE_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]);
const IGNORED_DIRS = new Set(["node_modules", ".next", "generated"]);

const FORBIDDEN_ICON_IMPORT_RE =
  /(?:from\s+|require\s*\(\s*)["'](?:@heroicons\/react|@remixicon\/react)(?:\/[^"']*)?["']/;
const CONSOLE_LOG_RE = /\bconsole\.log\s*\(/;
const PRIVATE_LAN_IP_RE = /\b192\.168\.\d{1,3}\.\d{1,3}\b/;
const DEBUGGER_STMT_RE = /^\s*debugger\s*;?\s*(?:\/\/.*)?$/;
const TS_IGNORE_RE = /@ts-ignore(?:\s*[:\-]?\s*(.*))?$/;

export function isTestFile(relPath) {
  return (
    /(?:^|\/)(?:__tests__|test)\//.test(relPath) ||
    /\.(?:test|spec)\.[cm]?[jt]sx?$/.test(relPath)
  );
}

export function collectFiles(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (IGNORED_DIRS.has(entry.name)) continue;
      collectFiles(path.join(dir, entry.name), out);
    } else if (entry.isFile() && SOURCE_EXTENSIONS.has(path.extname(entry.name))) {
      out.push(path.join(dir, entry.name));
    }
  }
  return out;
}

export function stripLineComment(line) {
  let inSingle = false;
  let inDouble = false;
  let inTemplate = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    const next = line[i + 1];
    if (ch === "\\" && (inSingle || inDouble || inTemplate)) {
      i += 1;
      continue;
    }
    if (!inDouble && !inTemplate && ch === "'") {
      inSingle = !inSingle;
    } else if (!inSingle && !inTemplate && ch === '"') {
      inDouble = !inDouble;
    } else if (!inSingle && !inDouble && ch === "`") {
      inTemplate = !inTemplate;
    } else if (!inSingle && !inDouble && !inTemplate && ch === "/" && next === "/") {
      return line.slice(0, i);
    }
  }
  return line;
}

export function inspectSource(content, relPath = "src/example.ts") {
  const normalizedRelPath = relPath.split(path.sep).join("/");
  const testFile = isTestFile(normalizedRelPath);
  const lines = content.split(/\r?\n/);
  const issues = [];

  for (let index = 0; index < lines.length; index += 1) {
    const lineNum = index + 1;
    const rawLine = lines[index];
    const codePart = stripLineComment(rawLine);

    if (FORBIDDEN_ICON_IMPORT_RE.test(codePart)) {
      issues.push({
        file: normalizedRelPath,
        line: lineNum,
        rule: "no-unused-icon-libraries",
        message: "Use lucide-react instead of removed @heroicons/react or @remixicon/react.",
      });
    }

    if (!testFile && CONSOLE_LOG_RE.test(codePart)) {
      issues.push({
        file: normalizedRelPath,
        line: lineNum,
        rule: "no-console-log",
        message:
          "Avoid console.log in production dashboard code (use console.warn/console.error if needed).",
      });
    }

    if (DEBUGGER_STMT_RE.test(rawLine)) {
      issues.push({
        file: normalizedRelPath,
        line: lineNum,
        rule: "no-debugger",
        message: "Remove stray debugger statement.",
      });
    }

    if (!testFile && PRIVATE_LAN_IP_RE.test(codePart)) {
      issues.push({
        file: normalizedRelPath,
        line: lineNum,
        rule: "no-hardcoded-lan-ip",
        message:
          "Avoid hardcoded private LAN IPs (192.168.*); configure via environment variables.",
      });
    }

    const tsIgnoreMatch = rawLine.match(TS_IGNORE_RE);
    if (tsIgnoreMatch) {
      const reason = (tsIgnoreMatch[1] || "").replace(/\*\/\s*$/, "").trim();
      if (reason.length < 4) {
        issues.push({
          file: normalizedRelPath,
          line: lineNum,
          rule: "ts-ignore-requires-reason",
          message:
            "@ts-ignore must include a brief explanation (or prefer @ts-expect-error with reason).",
        });
      }
    }
  }

  return issues;
}

export function checkFile(absPath, rootDir = dashboardRoot) {
  const relPath = path.relative(rootDir, absPath).split(path.sep).join("/");
  const content = fs.readFileSync(absPath, "utf8");
  return inspectSource(content, relPath);
}

export function runCodeQualityChecks({
  rootDir = dashboardRoot,
  srcDir = path.join(rootDir, "src"),
  includeNextConfig = true,
} = {}) {
  const filesToCheck = fs.existsSync(srcDir) ? collectFiles(srcDir) : [];
  if (includeNextConfig) {
    const nextConfigPath = path.join(rootDir, "next.config.ts");
    if (fs.existsSync(nextConfigPath)) {
      filesToCheck.push(nextConfigPath);
    }
  }

  const issues = [];
  for (const file of filesToCheck) {
    issues.push(...checkFile(file, rootDir));
  }

  return { filesChecked: filesToCheck, issues };
}

function isMainModule() {
  if (!process.argv[1]) return false;
  try {
    return fs.realpathSync(process.argv[1]) === fs.realpathSync(__filename);
  } catch {
    return path.resolve(process.argv[1]) === __filename;
  }
}

if (isMainModule()) {
  const { filesChecked, issues } = runCodeQualityChecks();

  if (issues.length > 0) {
    console.error(`Dashboard static quality check failed with ${issues.length} issue(s):\n`);
    for (const issue of issues) {
      console.error(`  ${issue.file}:${issue.line} [${issue.rule}] ${issue.message}`);
    }
    process.exit(1);
  }

  console.log(`Dashboard static quality check passed (${filesChecked.length} files checked).`);
}
