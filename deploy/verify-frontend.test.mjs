import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, symlink, readFile, chmod, lstat, readlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { linuxOnly, treeHash, inspectBuild, writeReceipt, checkRewrites, checkGit, ORIGIN, stopChild, staticLink, compareBuild, gitArgumentsForSource } from './verify-frontend.mjs';

const repository = fileURLToPath(new URL('../', import.meta.url));
async function fixture(run) {
  const root = await mkdtemp(join(tmpdir(), 'trippilot-artifact-test-'));
  try {
    const source = await readFile(join(repository, 'frontend/src/lib/api-endpoints.ts'), 'utf8');
    const endpoints = [...source.matchAll(/:\s*"(\/api\/[a-z/-]+)"/g)].map(m => m[1]);
    const rules = endpoints.map(path => ({ source: path, destination: ORIGIN + path.slice(4) }));
    rules.push({ source: '/api/health', destination: ORIGIN + '/health' },
      { source: '/api/saved-trips/:id([0-9a-f]{32})', destination: ORIGIN + '/saved-trips/:id' });
    for (const suffix of ['', '/turns', '/cancel', '/confirm', '/delete', '/places']) rules.push({
      source: `/api/chat/conversations/:id([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})${suffix}`,
      destination: `${ORIGIN}/chat/conversations/:id${suffix}`,
    });
    rules.push({ source: '/_AMapService/v3/config/district', destination: ORIGIN + '/_AMapService/v3/config/district' });
    const manifest = { rewrites: { beforeFiles: rules, afterFiles: [], fallback: [] } };
    for (const path of ['src/lib', 'public', 'node_modules/next', '.next/static/chunks', '.next/cache']) await mkdir(join(root, path), { recursive: true });
    const files = {
      'src/lib/api-endpoints.ts': source,
      'public/icon.svg': '<svg/>',
      'package.json': JSON.stringify({ dependencies: { next: '16.3.8' }, devDependencies: { 'eslint-config-next': '16.3.8' } }),
      'package-lock.json': JSON.stringify({ packages: { 'node_modules/next': { version: '16.3.8' } } }),
      'next.config.ts': '// controlled fixture',
      'tsconfig.json': '{}',
      'node_modules/next/package.json': JSON.stringify({ version: '16.3.8' }),
      '.next/BUILD_ID': 'controlled-linux-build',
      '.next/routes-manifest.json': JSON.stringify(manifest),
      '.next/static/chunks/app.js': 'controlled content',
      '.next/cache/mutable': 'old cache',
    };
    for (const [path, body] of Object.entries(files)) await writeFile(join(root, path), body);
    await run(root, manifest);
  } finally { await rm(root, { recursive: true, force: true }); }
}

test('Linux/x64 gate never labels the current Mac or wrong architecture as Linux verified', () => {
  assert.doesNotThrow(() => linuxOnly('linux', 'x64'));
  assert.throws(() => linuxOnly('darwin', 'arm64'), /linux_x64_required/);
  assert.throws(() => linuxOnly('linux', 'arm64'), /linux_x64_required/);
});

test('assistant additive routes require their exact reviewed source and manifest, without breaking staged historical releases', async () => {
  await fixture(async (root, manifest) => {
    // Existing staged chat builds still use their original six suffixes.
    await checkRewrites(root, manifest);
    await mkdir(join(root, 'src/lib/server'), { recursive: true });
    await writeFile(join(root, 'src/lib/server/backend-routing.ts'), '["/assistant-turns", "/assistant-state", "/assistant-preferences"]');
    await assert.rejects(checkRewrites(root, manifest), /backend_rewrites_mismatch/);
    const current = structuredClone(manifest);
    const at = current.rewrites.beforeFiles.length - 1;
    current.rewrites.beforeFiles.splice(at, 0, ...['/assistant-turns', '/assistant-state', '/assistant-preferences'].map(suffix => ({
      source: `/api/chat/conversations/:id([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})${suffix}`,
      destination: `${ORIGIN}/chat/conversations/:id${suffix}`,
    })));
    await checkRewrites(root, current);
    const wrong = structuredClone(current);
    wrong.rewrites.beforeFiles[at].destination = `${ORIGIN}/chat/conversations/:id/turns`;
    await assert.rejects(checkRewrites(root, wrong), /backend_rewrites_mismatch/);
    await writeFile(join(root, 'src/lib/server/backend-routing.ts'), '["/assistant-turns"]');
    await assert.rejects(checkRewrites(root, current), /assistant_allowlist_incomplete/);
  });
});

test('complete artifact is bound to source, lock, static and build; mutable cache excluded', async () => {
  await fixture(async root => {
    const before = await inspectBuild(root);
    await writeFile(join(root, '.next/cache/mutable'), 'new cache');
    assert.deepEqual(await inspectBuild(root), before);
    await writeFile(join(root, '.next/static/chunks/app.js'), 'different');
    const changed = await inspectBuild(root);
    assert.notEqual(changed.build_sha256, before.build_sha256);
    assert.notEqual(changed.static_sha256, before.static_sha256);
    await writeFile(join(root, 'src/lib/other.ts'), 'changed source');
    assert.notEqual((await inspectBuild(root)).source_sha256, before.source_sha256);
  });
});

test('dialogue action is additive and must match the exact source, UUID path and existing assistant increment', async () => {
  await fixture(async (root, manifest) => {
    await mkdir(join(root, 'src/lib/server'), { recursive: true });
    const file = join(root, 'src/lib/server/backend-routing.ts');
    const assistantSource = '["/assistant-turns", "/assistant-state", "/assistant-preferences"]';
    await writeFile(file, assistantSource);
    const assistant = structuredClone(manifest);
    const at = assistant.rewrites.beforeFiles.length - 1;
    const rule = suffix => ({
      source: `/api/chat/conversations/:id([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})${suffix}`,
      destination: `${ORIGIN}/chat/conversations/:id${suffix}`,
    });
    assistant.rewrites.beforeFiles.splice(at, 0, ...['/assistant-turns', '/assistant-state', '/assistant-preferences'].map(rule));
    await checkRewrites(root, assistant);
    await writeFile(file, `${assistantSource}\n["/dialogue-action"]`);
    await assert.rejects(checkRewrites(root, assistant), /backend_rewrites_mismatch/);
    const dialogue = structuredClone(assistant);
    dialogue.rewrites.beforeFiles.splice(dialogue.rewrites.beforeFiles.length - 1, 0, rule('/dialogue-action'));
    await checkRewrites(root, dialogue);
    for (const change of [
      { source: '/api/chat/conversations/:id/dialogue-action' },
      { source: '/api/chat/conversations/:path*' },
      { source: rule('/dialogue-action').source + '/execute' },
      { destination: ORIGIN + '/chat/conversations/:id/assistant-turns' },
      { destination: 'http://untrusted.invalid/dialogue-action' },
    ]) {
      const invalid = structuredClone(dialogue);
      Object.assign(invalid.rewrites.beforeFiles.find(item => item.source.endsWith('/dialogue-action')), change);
      await assert.rejects(checkRewrites(root, invalid), /backend_rewrites_mismatch/);
    }
    await writeFile(file, '["/dialogue-action"]');
    await assert.rejects(checkRewrites(root, dialogue), /dialogue_allowlist_incomplete/);
    // Preserve historical assistant builds exactly; no silent missing-path acceptance.
    await writeFile(file, assistantSource);
    await assert.rejects(checkRewrites(root, dialogue), /backend_rewrites_mismatch/);
    await checkRewrites(root, assistant);
  });
});

test('changed origin, extra forwarding path and malformed build ID stop reuse', async () => {
  await fixture(async (root, manifest) => {
    manifest.rewrites.beforeFiles[0].destination = 'http://127.0.0.1:9999/auth/session';
    await assert.rejects(checkRewrites(root, manifest), /backend_rewrites_mismatch/);
    manifest.rewrites.beforeFiles[0].destination = ORIGIN + '/auth/session';
    manifest.rewrites.fallback.push({ source: '/:path*', destination: ORIGIN + '/:path*' });
    await assert.rejects(checkRewrites(root, manifest), /backend_rewrites_mismatch/);
    await writeFile(join(root, '.next/BUILD_ID'), '../unsafe');
    await assert.rejects(inspectBuild(root), /build_id_invalid/);
  });
});

test('registration is an explicit additive resource; old allowlist remains verifiable, substitutions do not', async () => {
  await fixture(async (root, manifest) => {
    await checkRewrites(root, manifest);
    const file = join(root, 'src/lib/api-endpoints.ts');
    const current = await readFile(file, 'utf8');
    assert.ok(current.includes('"/api/auth/register"'));
    const chat1Only = current.replace(/^.*chatCities:.*\n/m, '');
    await writeFile(file, chat1Only);
    const chat1Manifest = structuredClone(manifest);
    chat1Manifest.rewrites.beforeFiles = chat1Manifest.rewrites.beforeFiles.filter(rule => !rule.source.endsWith('/places') && rule.source !== '/api/chat/cities');
    await checkRewrites(root, chat1Manifest);
    const registrationOnly = chat1Only.replace(/^.*chatConversations:.*\n/m, '');
    await writeFile(file, registrationOnly);
    const registrationManifest = structuredClone(manifest);
    registrationManifest.rewrites.beforeFiles = registrationManifest.rewrites.beforeFiles.filter(rule => !rule.source.startsWith('/api/chat/'));
    await checkRewrites(root, registrationManifest);
    const legacy = registrationOnly.replace(/^.*authRegister:.*\n/m, '');
    await writeFile(file, legacy);
    const oldManifest = structuredClone(registrationManifest);
    oldManifest.rewrites.beforeFiles = oldManifest.rewrites.beforeFiles.filter(rule => rule.source !== '/api/auth/register');
    await checkRewrites(root, oldManifest);
    for (const invalid of [current.replace('/api/auth/register', '/api/auth/unreviewed'),
      current.replace('/api/auth/login', '/api/auth/register'),
      current.replace('/api/trips/plan', '/api/trips/unknown'),
      current.replace('/api/chat/cities', '/api/chat/unreviewed'),
      current.replace(/^.*chatConversations:.*\n/m, ''),
      current.replace(/^.*authRegister:.*\n/m, '')]) {
      await writeFile(file, invalid);
      await assert.rejects(checkRewrites(root, manifest), /endpoint_allowlist_changed/);
    }
  });
});

test('chat resources use the exact UUID paths and approved operations, not an arbitrary dynamic proxy', async () => {
  await fixture(async (root, manifest) => {
    await checkRewrites(root, manifest);
    for (const field of ['source', 'destination']) {
      const changed = structuredClone(manifest);
      const rule = changed.rewrites.beforeFiles.find(item => item.source.endsWith('/turns'));
      rule[field] = rule[field].replace('/turns', '/unreviewed');
      await assert.rejects(checkRewrites(root, changed), /backend_rewrites_mismatch/);
    }
    const missing = structuredClone(manifest);
    missing.rewrites.beforeFiles = missing.rewrites.beforeFiles.filter(rule => !rule.source.endsWith('/cancel'));
    await assert.rejects(checkRewrites(root, missing), /backend_rewrites_mismatch/);
    for (const bad of ['/api/chat/conversations/:id/places', '/api/chat/conversations/:path*', '/api/chat/cities/:path*']) {
      const changed = structuredClone(manifest);
      const rule = changed.rewrites.beforeFiles.find(item => item.source.endsWith('/places'));
      rule.source = bad;
      await assert.rejects(checkRewrites(root, changed), /backend_rewrites_mismatch/);
    }
    const missingPlaces = structuredClone(manifest);
    missingPlaces.rewrites.beforeFiles = missingPlaces.rewrites.beforeFiles.filter(rule => !rule.source.endsWith('/places'));
    await assert.rejects(checkRewrites(root, missingPlaces), /backend_rewrites_mismatch/);
  });
});

test('artifact symlinks and lock/installed version mismatch are rejected', async () => {
  await fixture(async root => {
    await symlink('/etc/passwd', join(root, '.next/static/chunks/link'));
    await assert.rejects(treeHash(join(root, '.next/static')), /artifact_symlink_rejected/);
    await rm(join(root, '.next/static/chunks/link'));
    await writeFile(join(root, 'node_modules/next/package.json'), '{"version":"16.3.3"}');
    await assert.rejects(inspectBuild(root), /next_version_mismatch/);
  });
});

test('all installed dependency bytes and relative in-tree npm links are receipt-bound', async () => {
  await fixture(async root => {
    await mkdir(join(root, 'node_modules/.bin'));
    await writeFile(join(root, 'node_modules/next/cli.js'), 'controlled native or javascript content');
    await symlink('../next/cli.js', join(root, 'node_modules/.bin/next'));
    const build = await inspectBuild(root);
    const receipt = { schema_version: 'frontend-release-v1', platform: 'linux', architecture: 'x64',
      commit: 'a'.repeat(40), provenance: 'operator-attested-existing-build', ...build };
    assert.doesNotThrow(() => compareBuild(receipt, build));
    await writeFile(join(root, 'node_modules/next/cli.js'), 'changed dependency bytes');
    const changed = await inspectBuild(root);
    assert.notEqual(changed.node_modules_sha256, build.node_modules_sha256);
    assert.throws(() => compareBuild(receipt, changed), /artifact_mismatch_node_modules_sha256/);
    await rm(join(root, 'node_modules/.bin/next'));
    await symlink('../next/package.json', join(root, 'node_modules/.bin/next'));
    assert.notEqual((await inspectBuild(root)).node_modules_sha256, changed.node_modules_sha256);
  });
});

test('removing dependency group/world write bits preserves build receipts, executable bits and npm links', async () => {
  await fixture(async root => {
    await mkdir(join(root, 'node_modules/.bin'));
    await writeFile(join(root, 'node_modules/next/cli.js'), 'controlled executable dependency');
    await writeFile(join(root, 'node_modules/next/native.node'), 'controlled native dependency bytes');
    const link = join(root, 'node_modules/.bin/next');
    await symlink('../next/cli.js', link);
    const modes = [
      ['node_modules', 0o775],
      ['node_modules/.bin', 0o777],
      ['node_modules/next', 0o775],
      ['node_modules/next/package.json', 0o664],
      ['node_modules/next/cli.js', 0o775],
      ['node_modules/next/native.node', 0o666],
    ];
    for (const [path, mode] of modes) await chmod(join(root, path), mode);
    const before = await inspectBuild(root);
    const originalStats = await Promise.all(modes.map(([path]) => lstat(join(root, path))));
    const unrelated = ['package-lock.json', 'src/lib/api-endpoints.ts', '.next/BUILD_ID', '.next/static/chunks/app.js'];
    const unrelatedModes = await Promise.all(unrelated.map(async path => (await lstat(join(root, path))).mode));
    const linkBefore = await lstat(link);
    const linkText = await readlink(link);

    // The Python permission tool is tested separately. Here only metadata changes,
    // on known ordinary fixture entries, exercise the existing receipt contract.
    for (const [index, [path]] of modes.entries()) {
      const info = originalStats[index];
      assert.ok(info.isFile() || info.isDirectory());
      await chmod(join(root, path), (info.mode & 0o777) & ~0o022);
    }

    assert.deepEqual(await inspectBuild(root), before);
    for (const [index, [path, mode]] of modes.entries()) {
      const after = await lstat(join(root, path));
      assert.equal(after.mode & 0o777, mode & ~0o022);
      assert.equal(after.mode & 0o111, mode & 0o111);
      assert.equal(after.uid, originalStats[index].uid);
      assert.equal(after.gid, originalStats[index].gid);
    }
    assert.deepEqual(await Promise.all(unrelated.map(async path => (await lstat(join(root, path))).mode)), unrelatedModes);
    assert.equal(await readlink(link), linkText);
    assert.equal((await lstat(link)).isSymbolicLink(), true);
    assert.equal((await lstat(link)).mode, linkBefore.mode);
  });
});

test('installed dependency absolute/escaping links and special files are rejected', async () => {
  await fixture(async root => {
    const link = join(root, 'node_modules/unsafe');
    await symlink('/etc/passwd', link);
    await assert.rejects(inspectBuild(root), /artifact_absolute_symlink_rejected/);
    await rm(link);
    await symlink('../package.json', link);
    await assert.rejects(inspectBuild(root), /artifact_escaping_symlink_rejected/);
    await rm(link);
    execFileSync('mkfifo', [join(root, 'node_modules/unsafe-pipe')]);
    await assert.rejects(inspectBuild(root), /artifact_special_file_rejected/);
  });
});

test('receipt writes are exclusive and never overwrite an earlier attestation', async () => {
  await fixture(async root => {
    const path = join(root, 'receipt.json');
    await writeReceipt(path, { safe: 'first' });
    await assert.rejects(writeReceipt(path, { safe: 'replacement' }), { code: 'EEXIST' });
    assert.equal(JSON.parse(await readFile(path)).safe, 'first');
    await assert.rejects(writeReceipt('relative.json', {}), /absolute_receipt_path_required/);
  });
});

test('git commit and frontend clean state checked, without touching existing work', async () => {
  await fixture(async root => {
    const git = args => execFileSync('git', ['-C', root, ...args], { stdio: ['ignore', 'pipe', 'ignore'], encoding: 'utf8' }).trim();
    git(['init', '--quiet']);
    git(['config', 'user.email', 'controlled@example.invalid']);
    git(['config', 'user.name', 'Controlled Test']);
    git(['add', 'package.json', 'src']);
    git(['commit', '--quiet', '-m', 'controlled fixture']);
    const commit = git(['rev-parse', 'HEAD']);
    assert.doesNotThrow(() => checkGit(root, commit));
    assert.throws(() => checkGit(root, 'a'.repeat(40)), /source_commit_mismatch/);
    await writeFile(join(root, 'package.json'), '{}');
    assert.throws(() => checkGit(root, commit), /tracked_source_modified/);
  });
});

test('root Git ownership exception is command-local and restricted to the fixed admin checkout', () => {
  const args = ['rev-parse', 'HEAD'];
  assert.deepEqual(gitArgumentsForSource('/home/admin/apps/trippilot', args, 0),
    ['-c', 'safe.directory=/home/admin/apps/trippilot', '-C', '/home/admin/apps/trippilot', ...args]);
  assert.deepEqual(gitArgumentsForSource('/home/admin/apps/trippilot', args, 1000),
    ['-C', '/home/admin/apps/trippilot', ...args]);
  assert.deepEqual(gitArgumentsForSource('/tmp/untrusted-checkout', args, 0),
    ['-C', '/tmp/untrusted-checkout', ...args]);
  assert.deepEqual(gitArgumentsForSource('/home/admin/apps/trippilot-other', args, 0),
    ['-C', '/home/admin/apps/trippilot-other', ...args]);
  assert.deepEqual(args, ['rev-parse', 'HEAD']);
});

test('spawn failure cleanup does not wait forever for a nonexistent process', async () => {
  await stopChild({ pid: undefined, exitCode: null, signalCode: null });
});

test('smoke accepts SSR shell while auth is loading, but no unrelated HTML or external static URL', () => {
  assert.equal(staticLink(Buffer.from('<title>TripPilot｜单城旅行规划助手</title><p>正在核对登录状态…</p><script src="/_next/static/chunks/app.js"></script>')), '/_next/static/chunks/app.js');
  assert.throws(() => staticLink(Buffer.from('<title>Other service</title><script src="/_next/static/app.js"></script>')), /smoke_application_shell_missing/);
  assert.throws(() => staticLink(Buffer.from('<title>TripPilot</title><script src="https://example.invalid/evil.js"></script>')), /smoke_static_link_missing/);
});

test('source receipt comparison cannot ignore commit provenance or changed build fields', () => {
  const receipt = { schema_version: 'frontend-release-v1', platform: 'linux', architecture: 'x64',
    commit: 'a'.repeat(40), provenance: 'operator-attested-existing-build', build_id: 'first' };
  assert.doesNotThrow(() => compareBuild(receipt, { build_id: 'first' }));
  assert.throws(() => compareBuild(receipt, { build_id: 'second' }), /artifact_mismatch_build_id/);
  assert.throws(() => compareBuild({ ...receipt, schema_version: 'unknown' }, { build_id: 'first' }), /receipt_invalid/);
});

test('CLI rejects unsupported platform without echoing private environment values', () => {
  if (process.platform === 'linux' && process.arch === 'x64') return;
  let failure;
  try {
    execFileSync(process.execPath, [join(repository, 'deploy/verify-frontend.mjs'), 'attest', '--source', repository,
      '--commit', 'a'.repeat(40), '--receipt', '/tmp/not-created-trippilot-attestation.json', '--confirm-existing-build'],
    { env: { ...process.env, NEXT_PUBLIC_AMAP_JS_KEY: 'NEVER_PRINT_THIS_VALUE_123456789' }, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (error) { failure = error; }
  assert.ok(failure);
  assert.match(failure.stderr.toString(), /linux_x64_required/);
  assert.doesNotMatch(failure.stderr.toString(), /NEVER_PRINT_THIS/);
});
