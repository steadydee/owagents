#!/usr/bin/env bash
set -euo pipefail

PROFILE="${OPENCLAW_PROFILE:-owlswatch}"
WAIT_SECONDS="${WAIT_SECONDS:-120}"

node <<'NODE'
const fs = require('fs');
const { execFileSync } = require('child_process');

const profile = process.env.OPENCLAW_PROFILE || 'owlswatch';
const waitSeconds = Number(process.env.WAIT_SECONDS || '120');
const stateRoot = `${process.env.HOME}/.openclaw-${profile}`;
const configPath = `${stateRoot}/openclaw.json`;
const deadline = Date.now() + waitSeconds * 1000;

function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`;
    if (entry.isDirectory()) {
      if (!['tmp', 'media', 'backups'].includes(entry.name)) walk(path, out);
    } else if (/gateway.*\.log$|telegram.*\.json$|sessions.*\.json/.test(entry.name)) {
      out.push(path);
    }
  }
  return out;
}

function findGroupIdInText(text) {
  const patterns = [
    /"chat"\s*:\s*\{[^{}]*"id"\s*:\s*(-?\d+)[^{}]*"title"\s*:\s*"Dennis Brain"/,
    /"chat"\s*:\s*\{[^{}]*"title"\s*:\s*"Dennis Brain"[^{}]*"id"\s*:\s*(-?\d+)/,
    /chat[^\n]*id[=:]\s*(-?\d+)[^\n]*Dennis Brain/i,
    /Dennis Brain[^\n]*chat[^\n]*id[=:]\s*(-?\d+)/i,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return match[1];
  }
  return null;
}

function findGroupId() {
  const files = [
    `${stateRoot}/logs/gateway.log`,
    `${stateRoot}/logs/gateway.err.log`,
    ...walk(`${stateRoot}/agents`),
  ];
  for (const file of files) {
    if (!fs.existsSync(file)) continue;
    const stat = fs.statSync(file);
    const start = Math.max(0, stat.size - 1024 * 1024);
    const fd = fs.openSync(file, 'r');
    const buffer = Buffer.alloc(stat.size - start);
    fs.readSync(fd, buffer, 0, buffer.length, start);
    fs.closeSync(fd);
    const found = findGroupIdInText(buffer.toString('utf8'));
    if (found) return found;
  }
  return null;
}

function patchConfig(groupId) {
  const stamp = new Date().toISOString().replace(/[-:T.Z]/g, '').slice(0, 14);
  fs.copyFileSync(configPath, `${configPath}.before-dennis-brain-binding-${stamp}`);
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const groups = { ...(config.channels?.telegram?.groups || {}) };
  groups[groupId] = { ...(groups[groupId] || {}), requireMention: false, groupPolicy: 'open', ingest: true };
  const bindings = Array.isArray(config.bindings)
    ? config.bindings.filter((binding) => !(binding?.agentId === 'brain' && binding?.match?.channel === 'telegram'))
    : [];
  bindings.push({
    type: 'route',
    agentId: 'brain',
    comment: 'Route Dennis Brain private Telegram group to Brain Intake.',
    match: { channel: 'telegram', peer: { kind: 'group', id: groupId } },
  });
  const patch = { bindings, channels: { telegram: { groups } } };
  const patchText = JSON.stringify(patch);
  execFileSync('openclaw', ['--profile', profile, 'config', 'patch', '--stdin', '--dry-run'], { input: patchText, stdio: ['pipe', 'pipe', 'pipe'] });
  execFileSync('openclaw', ['--profile', profile, 'config', 'patch', '--stdin'], { input: patchText, stdio: ['pipe', 'pipe', 'pipe'] });
}

let groupId = findGroupId();
while (!groupId && Date.now() < deadline) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5000);
  groupId = findGroupId();
}

if (!groupId) {
  console.error('Dennis Brain group id not found. Send a message in the Dennis Brain Telegram group, then rerun this script.');
  process.exit(2);
}

patchConfig(groupId);
execFileSync('openclaw', ['--profile', profile, 'config', 'validate'], { stdio: ['ignore', 'pipe', 'pipe'] });
execFileSync('openclaw', ['--profile', profile, 'gateway', 'restart'], { stdio: ['ignore', 'pipe', 'pipe'] });
console.log('Dennis Brain Telegram group bound to Brain Intake and gateway restarted.');
NODE
