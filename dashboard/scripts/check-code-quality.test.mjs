import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  inspectSource,
  isTestFile,
  runCodeQualityChecks,
  stripLineComment,
} from "./check-code-quality.mjs";

test("detects forbidden icon library imports", () => {
  const source = [
    'import { BellIcon } from "@heroicons/react/24/outline";',
    'const remix = require("@remixicon/react");',
    'import { Bell } from "lucide-react";',
    '// import { Foo } from "@heroicons/react";',
  ].join("\n");

  const issues = inspectSource(source, "src/components/Header.tsx");
  assert.equal(issues.length, 2);
  assert.deepEqual(
    issues.map((item) => ({ line: item.line, rule: item.rule })),
    [
      { line: 1, rule: "no-unused-icon-libraries" },
      { line: 2, rule: "no-unused-icon-libraries" },
    ],
  );
});

test("flags console.log in production code while allowing console.error, console.warn, comments, and tests", () => {
  const prodSource = [
    'console.log("debug");',
    'console.error("real error");',
    'console.warn("warning");',
    '// console.log("commented");',
  ].join("\n");

  const prodIssues = inspectSource(prodSource, "src/features/ai/view.tsx");
  assert.equal(prodIssues.length, 1);
  assert.equal(prodIssues[0].line, 1);
  assert.equal(prodIssues[0].rule, "no-console-log");

  const testIssues = inspectSource('console.log("allowed in test");', "src/features/ai/view.test.ts");
  assert.equal(testIssues.length, 0);
});

test("flags stray debugger statements in production and test files", () => {
  const source = [
    "const debuggerEnabled = false;",
    "  debugger;",
    "debugger // temporary",
  ].join("\n");

  const issues = inspectSource(source, "src/features/ai/view.test.tsx");
  assert.deepEqual(
    issues.map((item) => ({ line: item.line, rule: item.rule })),
    [
      { line: 2, rule: "no-debugger" },
      { line: 3, rule: "no-debugger" },
    ],
  );
});

test("flags hardcoded private 192.168.* LAN IPs in production code only", () => {
  const source = [
    'const url = "http://192.168.1.10:8000/api";',
    'const envUrl = process.env.API_BASE_URL ?? "http://localhost:8000";',
    '// example: 192.168.1.10',
  ].join("\n");

  const prodIssues = inspectSource(source, "src/lib/api.ts");
  assert.equal(prodIssues.length, 1);
  assert.equal(prodIssues[0].line, 1);
  assert.equal(prodIssues[0].rule, "no-hardcoded-lan-ip");

  const testIssues = inspectSource(source, "src/lib/__tests__/api.ts");
  assert.equal(testIssues.length, 0);
});

test("requires explanation on @ts-ignore comments", () => {
  const source = [
    "// @ts-ignore",
    "// @ts-ignore fix",
    "// @ts-ignore: third-party chart option type omits custom field",
    "/* @ts-ignore legacy library typing */",
  ].join("\n");

  const issues = inspectSource(source, "src/components/Chart.tsx");
  assert.deepEqual(
    issues.map((item) => ({ line: item.line, rule: item.rule })),
    [
      { line: 1, rule: "ts-ignore-requires-reason" },
      { line: 2, rule: "ts-ignore-requires-reason" },
    ],
  );
});

test("handles line comments safely around URLs inside strings and identifies test files", () => {
  assert.equal(
    stripLineComment('const url = "https://example.com/path"; // trailing comment'),
    'const url = "https://example.com/path"; ',
  );
  assert.equal(isTestFile("src/lib/foo.test.ts"), true);
  assert.equal(isTestFile("src/test/setup.ts"), true);
  assert.equal(isTestFile("src/lib/foo.ts"), false);
});

test("runCodeQualityChecks scans workspace files, includes next.config.ts, and skips ignored dirs", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "globalid-quality-check-"));
  try {
    fs.mkdirSync(path.join(tmpDir, "src", "generated"), { recursive: true });
    fs.mkdirSync(path.join(tmpDir, "src", "features"), { recursive: true });

    fs.writeFileSync(
      path.join(tmpDir, "src", "features", "valid.ts"),
      'export function ok() { console.error("ok"); return 1; }\n',
    );
    fs.writeFileSync(
      path.join(tmpDir, "src", "features", "bad.ts"),
      'console.log("oops");\n',
    );
    fs.writeFileSync(
      path.join(tmpDir, "src", "generated", "ignored.ts"),
      'console.log("ignored generated file");\n',
    );
    fs.writeFileSync(
      path.join(tmpDir, "next.config.ts"),
      'export default { images: { unoptimized: true } };\n',
    );

    const result = runCodeQualityChecks({ rootDir: tmpDir });
    assert.equal(result.filesChecked.length, 3);
    assert.equal(result.issues.length, 1);
    assert.equal(result.issues[0].file, "src/features/bad.ts");
    assert.equal(result.issues[0].rule, "no-console-log");
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
