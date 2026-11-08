#!/usr/bin/env node
// Give Next.js time to finish active requests, but stop before systemd's
// TimeoutStopSec=20s even if an EventSource or the Node event loop hangs.
'use strict';

const { spawn } = require('node:child_process');
const { constants } = require('node:os');

const rawTimeout = process.env.GLOBALID_DASHBOARD_SHUTDOWN_TIMEOUT_MS || '15000';
const timeoutMs = Number(rawTimeout);
if (!Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 18000) {
  console.error('GLOBALID_DASHBOARD_SHUTDOWN_TIMEOUT_MS must be an integer between 100 and 18000');
  process.exit(1);
}
const [command, ...args] = process.argv.slice(2);
if (!command) {
  console.error('Dashboard supervisor requires a server command');
  process.exit(1);
}

const childEnv = { ...process.env };
// The launcher keeps NODE_OPTIONS off this helper process so preload hooks or
// debugger ports are applied once, to the dashboard server itself.
if (Object.hasOwn(childEnv, 'GLOBALID_DASHBOARD_CHILD_NODE_OPTIONS')) {
  childEnv.NODE_OPTIONS = childEnv.GLOBALID_DASHBOARD_CHILD_NODE_OPTIONS;
  delete childEnv.GLOBALID_DASHBOARD_CHILD_NODE_OPTIONS;
}
const child = spawn(command, args, { stdio: 'inherit', env: childEnv });
let stopping = false;
let timedOut = false;
let timer;

function stop(signal) {
  if (stopping) return;
  stopping = true;
  // systemd's default KillMode=control-group may already have delivered this
  // signal directly. Next's own cleanup is idempotent; forwarding also covers
  // callers that send a signal only to this supervisor.
  child.kill(signal);
  timer = setTimeout(() => {
    timedOut = true;
    console.warn(`Dashboard shutdown exceeded ${timeoutMs} ms; stopping the remaining server process`);
    child.kill('SIGKILL');
  }, timeoutMs);
  timer.unref();
}

process.on('SIGTERM', () => stop('SIGTERM'));
process.on('SIGINT', () => stop('SIGINT'));
child.on('error', (error) => {
  console.error(`Dashboard server could not start: ${error.message}`);
  process.exitCode = 1;
});
child.on('exit', (code, signal) => {
  clearTimeout(timer);
  if (stopping && (timedOut || code === 0 || code === 130 || code === 143 || signal === 'SIGTERM' || signal === 'SIGINT')) {
    process.exitCode = 0;
  } else if (code !== null) {
    process.exitCode = code;
  } else {
    process.exitCode = 128 + (constants.signals[signal] || 1);
  }
});
