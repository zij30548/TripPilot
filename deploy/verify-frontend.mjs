#!/usr/bin/env node
// Linux release artifact checks. No dotenv loading, credential output or API calls.
import { createHash } from 'node:crypto';
import { execFileSync, spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { open, readFile, readdir, lstat, realpath, readlink } from 'node:fs/promises';
import { isAbsolute, join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

export const ORIGIN = 'http://127.0.0.1:8000';
const VERSION = 'frontend-release-v1';
const NEXT = '16.3.8';
const LEGACY_API_PATHS = Object.freeze([
  '/api/auth/session', '/api/auth/login', '/api/auth/logout', '/api/usage',
  '/api/places/search', '/api/places/candidates', '/api/trips/schedule-preview',
  '/api/weather/forecast', '/api/trips/schedule-explanation', '/api/trips/adjustment-intent',
  '/api/trips/itinerary-query', '/api/media/landmark', '/api/media/video-frames',
  '/api/saved-trips', '/api/knowledge/search', '/api/knowledge/answer',
  '/api/routes/walking', '/api/routes/transit', '/api/trips/plan',
]);
const sha = data => createHash('sha256').update(data).digest('hex');
const fail = code => { throw new Error(code); };

export function linuxOnly(platform = process.platform, architecture = process.arch) {
  if (platform !== 'linux' || architecture !== 'x64') fail('linux_x64_required');
}

async function json(path, max = 2 * 1024 * 1024) {
  const info = await lstat(path);
  if (!info.isFile() || info.size > max) fail('invalid_json_file');
  return JSON.parse(await readFile(path, 'utf8'));
}

export async function fileHash(path) {
  // Read large native .node libraries in bounded chunks, not one full Buffer.
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path, { highWaterMark: 64 * 1024 })) hash.update(chunk);
  return hash.digest('hex');
}

export async function treeHash(directory, exclude = [], { allowInternalSymlinks = false } = {}) {
  if ((await lstat(directory)).isSymbolicLink()) fail('artifact_symlink_root_rejected');
  const canonical = await realpath(directory);
  const entries = [];
  async function walk(path, prefix) {
    for (const name of (await readdir(path)).sort()) {
      const key = prefix ? `${prefix}/${name}` : name;
      if (exclude.includes(key)) continue;
      const file = join(path, name);
      const info = await lstat(file);
      // npm .bin links may stay relative and within this exact dependency tree.
      // Hash the link text without following it; never traverse cycles or escape.
      if (info.isSymbolicLink()) {
        if (!allowInternalSymlinks) fail('artifact_symlink_rejected');
        const text = await readlink(file);
        if (isAbsolute(text)) fail('artifact_absolute_symlink_rejected');
        const target = await realpath(file);
        if (target !== canonical && !target.startsWith(canonical + sep)) fail('artifact_escaping_symlink_rejected');
        entries.push([key, 'symlink', text]);
        continue;
      }
      if (info.isDirectory()) await walk(file, key);
      else if (info.isFile()) entries.push([key, 'file', await fileHash(file)]);
      else fail('artifact_special_file_rejected');
    }
  }
  await walk(directory, '');
  if (!entries.length) fail('empty_artifact_tree');
  return sha(JSON.stringify(entries));
}

export async function sourceHash(frontend) {
  const hashes = [];
  // Whitelist actual runtime configuration and source; never hash/read .env contents.
  for (const file of ['package.json', 'package-lock.json', 'next.config.ts', 'tsconfig.json']) {
    hashes.push([file, sha(await readFile(join(frontend, file)))]);
  }
  hashes.push(['src', await treeHash(join(frontend, 'src'))]);
  hashes.push(['public', await treeHash(join(frontend, 'public'))]);
  return sha(JSON.stringify(hashes));
}

export async function checkRewrites(frontend, manifest) {
  const source = await readFile(join(frontend, 'src/lib/api-endpoints.ts'), 'utf8');
  const api = [...source.matchAll(/:\s*"(\/api\/[a-z/-]+)"/g)].map(match => match[1]);
  // Keep every reviewed historical allowlist, including already-staged releases.
  // Anonymous workspace bootstrap is one explicit replacement version: account
  // routes cannot be mixed back into it, and chat cities/places stay together.
  const increments = [[], ['/api/auth/register'], ['/api/auth/register', '/api/chat/conversations'],
    ['/api/auth/register', '/api/chat/conversations', '/api/chat/cities']];
  const chat = api.includes('/api/chat/conversations');
  const chatPlaces = api.includes('/api/chat/cities');
  const workspace = api.includes('/api/workspace/session');
  let assistant = false, dialogue = false;
  try {
    const routing = await readFile(join(frontend, 'src/lib/server/backend-routing.ts'), 'utf8');
    assistant = /["']\/assistant-turns["']/.test(routing);
    dialogue = /["']\/dialogue-action["']/.test(routing);
    if (dialogue && !assistant) fail('dialogue_allowlist_incomplete');
    if (assistant && (!chatPlaces || !/["']\/assistant-state["']/.test(routing) || !/["']\/assistant-preferences["']/.test(routing))) fail('assistant_allowlist_incomplete');
  } catch (error) {
    // Historical source/test fixtures without this module have only the
    // original fixed list. Other read failures are never version bypasses.
    if (error?.code !== 'ENOENT') throw error;
  }
  const legacyAllowed = increments.some(increment => {
    const expected = [...LEGACY_API_PATHS, ...increment];
    return api.length === expected.length && expected.every(path => api.includes(path));
  });
  const workspaceExpected = [
    '/api/workspace/session',
    ...LEGACY_API_PATHS.filter(path => !path.startsWith('/api/auth/')),
    '/api/chat/conversations', '/api/chat/cities',
  ];
  const workspaceAllowed = workspace && assistant && dialogue &&
    api.length === workspaceExpected.length && workspaceExpected.every(path => api.includes(path));
  if (new Set(api).size !== api.length || !(legacyAllowed || workspaceAllowed)) fail('endpoint_allowlist_changed');
  const expected = api.map(path => ({ source: path, destination: ORIGIN + path.slice(4) }));
  expected.push(
    { source: '/api/health', destination: `${ORIGIN}/health` },
    { source: '/api/saved-trips/:id([0-9a-f]{32})', destination: `${ORIGIN}/saved-trips/:id` },
  );
  if (chat) for (const suffix of ['', '/turns', '/cancel', '/confirm', '/delete', ...(chatPlaces ? ['/places'] : []), ...(assistant ? ['/assistant-turns', '/assistant-state', '/assistant-preferences'] : []), ...(dialogue ? ['/dialogue-action'] : [])]) expected.push({
    source: `/api/chat/conversations/:id([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})${suffix}`,
    destination: `${ORIGIN}/chat/conversations/:id${suffix}`,
  });
  expected.push({ source: '/_AMapService/v3/config/district', destination: `${ORIGIN}/_AMapService/v3/config/district` });
  const rules = manifest?.rewrites;
  if (!rules || !Array.isArray(rules.beforeFiles) || rules.beforeFiles.length !== expected.length ||
      !Array.isArray(rules.afterFiles) || rules.afterFiles.length ||
      !Array.isArray(rules.fallback) || rules.fallback.length ||
      !expected.every((rule, index) => rule.source === rules.beforeFiles[index]?.source &&
        rule.destination === rules.beforeFiles[index]?.destination)) fail('backend_rewrites_mismatch');
}

function publicKeyFingerprint() {
  const key = process.env.NEXT_PUBLIC_AMAP_JS_KEY;
  if (key === undefined || key === '') return null;
  if (!/^[a-zA-Z0-9_-]{16,128}$/.test(key)) fail('public_key_format_invalid');
  return sha(key);
}

export async function inspectBuild(frontend) {
  const pkg = await json(join(frontend, 'package.json'));
  const lock = await json(join(frontend, 'package-lock.json'), 8 * 1024 * 1024);
  const installed = await json(join(frontend, 'node_modules/next/package.json'));
  if (pkg.dependencies?.next !== NEXT || pkg.devDependencies?.['eslint-config-next'] !== NEXT ||
      lock.packages?.['node_modules/next']?.version !== NEXT || installed.version !== NEXT) fail('next_version_mismatch');
  const buildId = (await readFile(join(frontend, '.next/BUILD_ID'), 'utf8')).trim();
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(buildId)) fail('build_id_invalid');
  await checkRewrites(frontend, await json(join(frontend, '.next/routes-manifest.json')));
  return {
    next_version: NEXT,
    build_id: buildId,
    backend_origin: ORIGIN,
    package_lock_sha256: sha(await readFile(join(frontend, 'package-lock.json'))),
    source_sha256: await sourceHash(frontend),
    build_sha256: await treeHash(join(frontend, '.next'), ['cache']),
    static_sha256: await treeHash(join(frontend, '.next/static')),
    node_modules_sha256: await treeHash(join(frontend, 'node_modules'), [], { allowInternalSymlinks: true }),
    // Operator-supplied declaration, NOT proof of the value in an older compiled bundle.
    declared_public_key_sha256: publicKeyFingerprint(),
  };
}

export async function writeReceipt(path, receipt) {
  if (!isAbsolute(path)) fail('absolute_receipt_path_required');
  const handle = await open(path, 'wx', 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(receipt, null, 2)}\n`);
    await handle.sync();
  } finally { await handle.close(); }
}

export function gitArgumentsForSource(source, arguments_, uid = process.getuid?.()) {
  const fixedSource = '/home/admin/apps/trippilot';
  // Only the root staging command needs to inspect admin's explicit checkout.
  // This applies to one command, never a persisted safe.directory or wildcard.
  const trusted = uid === 0 && resolve(source) === fixedSource
    ? ['-c', `safe.directory=${fixedSource}`] : [];
  return [...trusted, '-C', source, ...arguments_];
}

export function checkGit(source, commit) {
  if (!/^[a-f0-9]{40}$/.test(commit ?? '')) fail('full_commit_required');
  const git = args => execFileSync('git', gitArgumentsForSource(source, args), { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  if (git(['rev-parse', 'HEAD']) !== commit) fail('source_commit_mismatch');
  // Explicitly preserve the five existing tracked/untracked cache files. Do not
  // ignore frontend source changes or permit an arbitrary dirty-build receipt.
  const dirty = git(['diff', '--name-only', 'HEAD', '--']);
  const allowed = new Set([
    'backend/app/__pycache__/config.cpython-313.pyc',
    'backend/app/integrations/__pycache__/amap.cpython-313.pyc',
  ]);
  if (dirty.split('\n').filter(Boolean).some(file => !allowed.has(file))) fail('tracked_source_modified');
}

export async function attest({ source, commit, receipt, confirmed }) {
  linuxOnly();
  if (!confirmed) fail('operator_build_attestation_required');
  checkGit(source, commit);
  const frontend = join(source, 'frontend');
  const build = await inspectBuild(frontend);
  const value = {
    schema_version: VERSION,
    platform: 'linux', architecture: 'x64',
    commit,
    provenance: 'operator-attested-existing-build',
    provenance_limit: 'HEAD and clean tracked sources checked; original compilation commit and public key cannot be independently proven retrospectively.',
    recorded_at: new Date().toISOString(),
    ...build,
  };
  await writeReceipt(receipt, value);
  return value;
}

function assertReceipt(receipt) {
  if (receipt.schema_version !== VERSION || receipt.platform !== 'linux' || receipt.architecture !== 'x64' ||
      !/^[a-f0-9]{40}$/.test(receipt.commit ?? '') || receipt.provenance !== 'operator-attested-existing-build') fail('receipt_invalid');
}

export function compareBuild(receipt, build) {
  assertReceipt(receipt);
  for (const [key, value] of Object.entries(build)) {
    if (receipt[key] !== value) fail(`artifact_mismatch_${key}`);
  }
}

export async function verifySource(source, commit, receiptPath) {
  linuxOnly();
  checkGit(source, commit);
  const receipt = await json(receiptPath);
  if (receipt.commit !== commit) fail('receipt_commit_mismatch');
  // Source .env.local may exist, but is never opened, copied or considered proof
  // of what an earlier build used. Final releases still strictly reject dotenv.
  compareBuild(receipt, await inspectBuild(join(source, 'frontend')));
  return receipt;
}

export async function verify(frontend, receiptPath, { native = false } = {}) {
  linuxOnly();
  const receipt = await json(receiptPath);
  assertReceipt(receipt);
  // Final releases have no dotenv file. Separate systemd EnvironmentFiles are
  // supplied by the operator; backend secrets must never be readable by Next.
  if ((await readdir(frontend)).some(name => name.startsWith('.env') && name !== '.env.example')) fail('dotenv_in_release_rejected');
  const build = await inspectBuild(frontend);
  compareBuild(receipt, build);
  if (native) return { ...receipt, native: await verifyNative(frontend) };
  return receipt;
}

export async function verifyNative(frontend) {
  linuxOnly();
  const require = createRequire(join(frontend, 'package.json'));
  // Never enable download/install fallback. Correct npm optional prebuilt packages
  // must already be present for this host and libc.
  if (process.env.NAPI_RS_NATIVE_LIBRARY_PATH) fail('native_library_override_rejected');
  const unrs = require('unrs-resolver');
  if (typeof unrs.sync !== 'function' || !unrs.sync(frontend, 'next').path) fail('unrs_native_unavailable');
  const sharp = require('sharp');
  if (sharp.versions.sharp !== '0.35.5' || !/^\d+\.\d+\.\d+$/.test(sharp.versions.rsvg ?? '')) fail('sharp_version_mismatch');
  const [major, minor, patch] = sharp.versions.rsvg.split('.').map(Number);
  if (major < 2 || (major === 2 && (minor < 63 || (minor === 63 && patch < 2)))) fail('librsvg_not_patched');
  const png = await sharp({ create: { width: 2, height: 2, channels: 3, background: '#123456' } }).png().toBuffer();
  if (!png.length) fail('sharp_native_unavailable');
  const swc = require('@next/swc-linux-x64-gnu');
  if (require('@next/swc-linux-x64-gnu/package.json').version !== NEXT || typeof swc.transformSync !== 'function') fail('swc_native_unavailable');
  return { unrs: 'loaded-and-resolved', sharp: sharp.versions.sharp, librsvg: sharp.versions.rsvg, swc: 'linux-x64-gnu-loaded' };
}

export async function boundedGet(url, bytes) {
  const response = await fetch(url, { redirect: 'manual', signal: AbortSignal.timeout(5000) });
  if (response.status !== 200) { await response.body?.cancel(); fail('smoke_http_status'); }
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > bytes) fail('smoke_response_too_large');
      chunks.push(Buffer.from(value));
    }
  } finally { await reader.cancel(); }
  return Buffer.concat(chunks);
}

export async function stopChild(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const exited = new Promise(resolveExit => child.once('exit', resolveExit));
  const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
  try { await exited; } finally { clearTimeout(timer); }
}

export function staticLink(html) {
  // Workspace bootstrap is client-driven. This check never runs browser scripts
  // or requests an identity/session endpoint, on current or historical builds.
  const text = html.toString('utf8');
  if (!/<title>TripPilot(?:[^<]*)<\/title>/.test(text)) fail('smoke_application_shell_missing');
  const link = text.match(/(?:src|href)="(\/_next\/static\/[a-zA-Z0-9_./%-]+\.(?:js|css))"/)?.[1];
  if (!link) fail('smoke_static_link_missing');
  return link;
}

export async function smoke(frontend, receiptPath) {
  await verify(frontend, receiptPath, { native: true });
  // Fixed localhost address; no API endpoint or browser is used. An occupied port
  // is a failure, never permission to reuse/stop an unknown process.
  const { createServer } = await import('node:net');
  await new Promise((yes, no) => {
    const probe = createServer();
    probe.once('error', () => no(new Error('smoke_port_busy')));
    probe.listen(3102, '127.0.0.1', () => probe.close(yes));
  });
  const child = spawn(process.execPath, [join(frontend, 'node_modules/next/dist/bin/next'), 'start', '--hostname', '127.0.0.1', '--port', '3102'], {
    cwd: frontend,
    env: { PATH: '/usr/bin:/bin', NODE_ENV: 'production', NEXT_TELEMETRY_DISABLED: '1', TRIPPILOT_BACKEND_ORIGIN: ORIGIN,
      ...(process.env.NEXT_PUBLIC_AMAP_JS_KEY ? { NEXT_PUBLIC_AMAP_JS_KEY: process.env.NEXT_PUBLIC_AMAP_JS_KEY } : {}) },
    stdio: 'ignore',
  });
  let startupError = false;
  child.on('error', () => { startupError = true; });
  try {
    let html;
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline && !startupError && child.exitCode === null) {
      try { html = await boundedGet('http://127.0.0.1:3102/', 3 * 1024 * 1024); break; }
      catch { await new Promise(r => setTimeout(r, 150)); }
    }
    if (!html) fail('smoke_application_shell_missing');
    const staticUrl = staticLink(html);
    const relativePath = decodeURIComponent(staticUrl.slice('/_next/static/'.length));
    const disk = resolve(frontend, '.next/static', relativePath);
    if (!disk.startsWith(resolve(frontend, '.next/static') + sep)) fail('smoke_static_path_invalid');
    const served = await boundedGet(`http://127.0.0.1:3102${staticUrl}`, 5 * 1024 * 1024);
    if (sha(served) !== sha(await readFile(disk))) fail('smoke_static_hash_mismatch');
    return { result: 'application-shell-and-static-hash-ok', port: 3102, business_requests: 0, native: 'verified', scope: 'no browser scripts or authenticated login performed' };
  } finally { await stopChild(child); }
}

async function main(args) {
  if (args.length === 1 && ['--help', '-h'].includes(args[0])) {
    console.log('Linux x64 only. Never reads dotenv files or calls business APIs.\n' +
      'attest --source ABS_REPO --commit FULL_SHA --receipt NEW_ABS_FILE --confirm-existing-build\n' +
      'verify-source --source ABS_REPO --commit FULL_SHA --receipt ABS_FILE\n' +
      'verify --frontend ABS_RELEASE_FRONTEND --receipt ABS_FILE [--native]\n' +
      'smoke --frontend ABS_RELEASE_FRONTEND --receipt ABS_FILE\n' +
      'Use the same explicitly supplied NEXT_PUBLIC_AMAP_JS_KEY for each action; only its fingerprint is retained.');
    return;
  }
  const action = args.shift();
  const options = {};
  for (let index = 0; index < args.length; index++) {
    const arg = args[index];
    if (arg === '--confirm-existing-build' || arg === '--native') options[arg.slice(2)] = true;
    else if (['--source', '--commit', '--receipt', '--frontend'].includes(arg) && args[index + 1] && !args[index + 1].startsWith('--')) options[arg.slice(2)] = args[++index];
    else fail('invalid_arguments');
  }
  for (const key of ['receipt', ['attest', 'verify-source'].includes(action) ? 'source' : 'frontend']) {
    if (!isAbsolute(options[key] ?? '')) fail('absolute_paths_required');
    if (key !== 'receipt' && await realpath(options[key]) !== resolve(options[key])) fail('symlink_root_rejected');
  }
  if (action === 'attest') {
    await attest({ source: options.source, commit: options.commit, receipt: options.receipt, confirmed: options['confirm-existing-build'] });
    console.log('OK: Linux source/build attestation recorded; this is not retrospective proof of the build commit.');
  } else if (action === 'verify-source') {
    await verifySource(options.source, options.commit, options.receipt);
    console.log('OK: source commit and attested artifact hashes still match; source dotenv was not read.');
  } else if (action === 'verify') {
    await verify(options.frontend, options.receipt, { native: !!options.native });
    console.log(`OK: release artifact hashes${options.native ? ' and Linux native modules' : ''} verified.`);
  } else if (action === 'smoke') {
    console.log(JSON.stringify(await smoke(options.frontend, options.receipt)));
  } else fail('invalid_action');
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch(error => {
    // No paths, subprocess output, environment values or raw exception text.
    const code = /^[a-z][a-z0-9_]{1,80}$/.test(error?.message ?? '') ? error.message : 'verification_failed';
    console.error(`STOP: ${code}. No artifact was replaced; do not overwrite the receipt or skip the check.`);
    process.exitCode = 1;
  });
}
