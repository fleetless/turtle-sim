// SPDX-License-Identifier: Apache-2.0
// The cases release.mjs must pass. All but the last section are taken,
// unchanged, from the list every copy of that file shares: the version
// rule, the GitHub calls, the tag, the release. The cases of what this copy
// dropped (see its header) are dropped with it. The last section is this
// repository's own.
//
//   node --test '.github/release/*.test.mjs'
//
// The last section runs git in a temporary directory; nothing here uses the
// network.

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { ReleaseError, checkPassed, commitBump, createTag, github, main, nextVersion, publishRelease } from './release.mjs'

const c = (subject, body = '') => ({ subject, body })

// ── the version rule: the design's table, row by row ────────────────────

test('empty range: refused', () => {
  assert.throws(() => nextVersion({ current: '1.4.2', commits: [] }), (e) => e instanceof ReleaseError && /nothing to release/.test(e.message))
})
test('patch only: fix and docs → patch', () => {
  assert.equal(nextVersion({ current: '1.4.2', commits: [c('fix: a'), c('docs: b')] }).version, '1.4.3')
})
test('feature → minor', () => {
  assert.equal(nextVersion({ current: '1.4.2', commits: [c('fix: a'), c('feat: b')] }).version, '1.5.0')
})
test('breaking by ! → major', () => {
  assert.equal(nextVersion({ current: '1.4.2', commits: [c('feat!: a')] }).version, '2.0.0')
  assert.equal(nextVersion({ current: '1.4.2', commits: [c('feat(auth-config)!: a')] }).version, '2.0.0')
})
test('breaking by footer → major', () => {
  assert.equal(nextVersion({ current: '1.4.2', commits: [c('fix: a', 'text\n\nBREAKING CHANGE: gone')] }).version, '2.0.0')
})
test('breaking below 1.0 → minor', () => {
  assert.equal(nextVersion({ current: '0.22.0', commits: [c('feat!: a')] }).version, '0.23.0')
})
test('feature below 1.0 → minor', () => {
  assert.equal(nextVersion({ current: '0.22.0', commits: [c('feat: a')] }).version, '0.23.0')
})
test('override raises', () => {
  assert.equal(nextVersion({ current: '1.4.2', commits: [c('fix: a')], bump: 'minor' }).version, '1.5.0')
})
test('override cannot lower', () => {
  assert.equal(nextVersion({ current: '1.4.2', commits: [c('feat: a')], bump: 'patch' }).version, '1.5.0')
})
test('no Conventional prefix → patch', () => {
  assert.equal(nextVersion({ current: '1.4.2', commits: [c('Update README')] }).version, '1.4.3')
})
test('an explicit major below 1.0 leaves 0.x', () => {
  assert.equal(nextVersion({ current: '0.22.0', commits: [c('fix: a')], bump: 'major' }).version, '1.0.0')
})
test('no tag yet: the first version', () => {
  assert.deepEqual(nextVersion({ current: null, commits: [c('feat: a')], first: '1.0.0' }), { version: '1.0.0', bump: 'first' })
})
test('an unknown bump is a usage error', () => {
  assert.throws(() => nextVersion({ current: '1.0.0', commits: [c('fix: a')], bump: 'huge' }), (e) => e.code === 2)
})
test('commitBump reads the type, not the words', () => {
  assert.equal(commitBump(c('fix: add a feature')), 'patch')
  assert.equal(commitBump(c('feature: not a type')), 'patch')
})

// ── GitHub operations, against a recording fake ─────────────────────────

function fake(routes) {
  const calls = []
  const api = async (method, path, opts = {}) => {
    calls.push({ method, path, body: opts.body, raw: opts.raw })
    for (const [pattern, answer] of routes) if (pattern.test(`${method} ${path}`)) return typeof answer === 'function' ? answer(opts) : answer
    throw new Error(`unexpected call: ${method} ${path}`)
  }
  return { api, calls }
}
const SHA = 'a'.repeat(40)
const OTHER = 'b'.repeat(40)

// A fetch-shaped fake for github() itself, one level below the recording
// `api` fake above (which stands in for github() and never exercises its
// 404 handling at all).
function fetchFake(status, text = '') {
  return async () => ({ status, ok: status >= 200 && status < 300, text: async () => text, json: async () => JSON.parse(text || '{}') })
}

test('github: a 404 on a read (GET) answers null', async () => {
  assert.equal(await github('GET', '/repos/fleetless/web/git/ref/tags/v1.0.0', { token: 't', fetchImpl: fetchFake(404) }), null)
})
test('github: a 404 on a delete of something already gone (DELETE) answers null', async () => {
  assert.equal(await github('DELETE', '/repos/fleetless/web/releases/assets/1', { token: 't', fetchImpl: fetchFake(404) }), null)
})
test('github: a 404 on a write (POST) is a refusal, never a null the caller mistakes for success', async () => {
  await assert.rejects(
    github('POST', '/repos/fleetless/web/git/tags', { token: 't', body: {}, fetchImpl: fetchFake(404, 'Not Found') }),
    (e) => e instanceof ReleaseError && /^POST \/repos\/fleetless\/web\/git\/tags: 404/.test(e.message),
  )
})
test('github: a GraphQL refusal (a 200 with errors) is a ReleaseError, not a silent success', async () => {
  await assert.rejects(
    github('POST', '/graphql', { token: 't', graphql: true, body: { query: 'x' }, fetchImpl: fetchFake(200, JSON.stringify({ errors: [{ message: 'nope' }] })) }),
    (e) => e instanceof ReleaseError && /nope/.test(e.message),
  )
})
test('github: a GraphQL success answers its data', async () => {
  assert.deepEqual(
    await github('POST', '/graphql', { token: 't', graphql: true, body: { query: 'x' }, fetchImpl: fetchFake(200, JSON.stringify({ data: { ok: true } })) }),
    { ok: true },
  )
})

test('createTag: an absent tag is created as an annotated tag by the App', async () => {
  const { api, calls } = fake([
    [/^GET .*\/git\/ref\/tags\/v1\.0\.0$/, null],
    [/^POST .*\/git\/tags$/, { sha: 'c'.repeat(40) }],
    [/^POST .*\/git\/refs$/, { ref: 'refs/tags/v1.0.0' }],
  ])
  assert.equal(await createTag({ repo: 'fleetless/web', version: '1.0.0', sha: SHA, api }), 'created')
  assert.deepEqual(calls[1].body, { tag: 'v1.0.0', message: 'v1.0.0', object: SHA, type: 'commit' })
  assert.deepEqual(calls[2].body, { ref: 'refs/tags/v1.0.0', sha: 'c'.repeat(40) })
})
test('createTag: an existing tag at the same commit is reused (a re-run)', async () => {
  const { api } = fake([
    [/^GET .*\/git\/ref\/tags\//, { object: { type: 'tag', sha: 'd'.repeat(40) } }],
    [/^GET .*\/git\/tags\//, { object: { sha: SHA } }],
  ])
  assert.equal(await createTag({ repo: 'fleetless/web', version: '1.0.0', sha: SHA, api }), 'exists')
})
test('createTag: an existing tag at another commit is refused, never moved', async () => {
  const { api, calls } = fake([[/^GET .*\/git\/ref\/tags\//, { object: { type: 'commit', sha: OTHER } }]])
  await assert.rejects(createTag({ repo: 'fleetless/web', version: '1.0.0', sha: SHA, api }), /already exists at b+, not at a+/)
  assert.equal(calls.filter((x) => x.method === 'POST').length, 0)
})
test('createTag: a missing answer from the tag-object POST is a ReleaseError, not a TypeError on tag.sha', async () => {
  const { api } = fake([
    [/^GET .*\/git\/ref\/tags\//, null],
    [/^POST .*\/git\/tags$/, null],
  ])
  await assert.rejects(createTag({ repo: 'fleetless/web', version: '1.0.0', sha: SHA, api }), (e) => e instanceof ReleaseError && /could not create the tag object v1\.0\.0/.test(e.message))
})

const RELEASE = (assets, extra = {}) => ({
  id: 7,
  upload_url: 'https://uploads.github.com/repos/fleetless/web/releases/7/assets{?name,label}',
  assets,
  draft: false,
  author: { login: 'github-actions[bot]' },
  ...extra,
})

// ── the belt and braces before the tag ───────────────────────────────────

test('checkPassed: a successful required check on the commit passes', async () => {
  const { api } = fake([[/^GET .*\/check-runs$/, { check_runs: [{ name: 'verify', status: 'completed', conclusion: 'success' }] }]])
  assert.equal(await checkPassed({ repo: 'fleetless/contracts', sha: SHA, check: 'verify', api }), true)
})
test('checkPassed: failure, neutral, in progress, another name and none are all refused', async () => {
  const cases = [
    [{ name: 'verify', status: 'completed', conclusion: 'failure' }],
    [{ name: 'verify', status: 'completed', conclusion: 'neutral' }],
    [{ name: 'verify', status: 'in_progress', conclusion: null }],
    [{ name: 'other', status: 'completed', conclusion: 'success' }],
    [],
  ]
  for (const check_runs of cases) {
    const { api } = fake([[/^GET .*\/check-runs$/, { check_runs }]])
    await assert.rejects(checkPassed({ repo: 'fleetless/contracts', sha: SHA, check: 'verify', api }), /no successful verify check run/)
  }
})

// ── a GitHub release of several files, or of notes alone ────────────────

const DEBS = ['ros-humble-fleetless-bridge_4.0.1-0jammy_all.deb', 'ros-jazzy-fleetless-bridge_4.0.1-0noble_all.deb', 'ros-lyrical-fleetless-bridge_4.0.1-0resolute_all.deb']

test('publishRelease: every file, then one SHA256SUMS over exactly them', async () => {
  const { api, calls } = fake([
    [/^GET .*\/releases\/tags\/v4\.0\.1$/, null],
    [/^POST \/repos\/fleetless\/bridge\/releases$/, RELEASE([])],
    [/^POST https:\/\/uploads\.github\.com\//, {}],
  ])
  const files = DEBS.map((name) => ({ name, bytes: Buffer.from(name) }))
  assert.equal(await publishRelease({ repo: 'fleetless/bridge', version: '4.0.1', files, notes: 'notes', api }), 'uploaded')
  assert.equal(calls[1].body.body, 'notes')
  const uploads = calls.filter((x) => x.path.startsWith('https://uploads.github.com/'))
  assert.deepEqual(uploads.map((u) => u.path.split('?name=')[1]), [...DEBS, 'SHA256SUMS'])
  const sums = uploads[3].raw.toString().trim().split('\n')
  assert.deepEqual(sums.map((l) => l.split('  ')[1]), DEBS)
})
test('publishRelease: notes alone (app-starter): created, nothing uploaded', async () => {
  const { api, calls } = fake([
    [/^GET .*\/releases\/tags\/v1\.0\.0$/, null],
    [/^POST \/repos\/fleetless\/app-starter\/releases$/, RELEASE([])],
  ])
  assert.equal(await publishRelease({ repo: 'fleetless/app-starter', version: '1.0.0', notes: '- First.', api }), 'created')
  assert.deepEqual(calls[1].body, { tag_name: 'v1.0.0', name: 'v1.0.0', body: '- First.' })
  assert.equal(calls.length, 2)
})
test('publishRelease: notes alone, the release already there (a re-run): left alone', async () => {
  const { api, calls } = fake([[/^GET .*\/releases\/tags\//, RELEASE([])]])
  assert.equal(await publishRelease({ repo: 'fleetless/app-starter', version: '1.0.0', notes: 'x', api }), 'exists')
  assert.equal(calls.length, 1)
})
test('publishRelease: notes alone, but the release carries an asset: refused', async () => {
  const { api } = fake([[/^GET .*\/releases\/tags\//, RELEASE([{ id: 3, name: 'x.zip' }])]])
  await assert.rejects(publishRelease({ repo: 'fleetless/app-starter', version: '1.0.0', notes: 'x', api }), /did not make: x\.zip/)
})
test('publishRelease: two of three packages present: all removed, all uploaded again', async () => {
  const { api, calls } = fake([
    [/^GET .*\/releases\/tags\//, RELEASE([{ id: 1, name: DEBS[0] }, { id: 2, name: DEBS[1] }])],
    [/^DELETE .*\/releases\/assets\/[12]$/, {}],
    [/^POST https:\/\/uploads\.github\.com\//, {}],
  ])
  const files = DEBS.map((name) => ({ name, bytes: Buffer.from(name) }))
  assert.equal(await publishRelease({ repo: 'fleetless/bridge', version: '4.0.1', files, notes: 'n', api }), 'uploaded')
  assert.deepEqual(calls.map((x) => x.method), ['GET', 'DELETE', 'DELETE', 'POST', 'POST', 'POST', 'POST'])
})
test('publishRelease: a SHA256SUMS handed in is a usage error — it is written here', async () => {
  const { api } = fake([])
  await assert.rejects(publishRelease({ repo: 'fleetless/bridge', version: '4.0.1', files: [{ name: 'SHA256SUMS', bytes: Buffer.from('') }], notes: 'n', api }), (e) => e.code === 2)
})

// ── this repository's own: the CLI against a real git history ───────────

// A repository of its own in a temporary directory, with nothing of the
// machine's git configuration in it: a signing key or a hook of whoever
// runs the tests must not decide the result.
function scratchRepository(t) {
  const dir = mkdtempSync(join(tmpdir(), 'release-test-'))
  const env = { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null', GIT_CONFIG_NOSYSTEM: '1' }
  delete env.GIT_DIR
  delete env.GIT_WORK_TREE
  const git = (...args) => execFileSync('git', ['-c', 'user.name=test', '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false', ...args], { cwd: dir, env, encoding: 'utf8' })
  let n = 0
  const commit = (subject) => {
    writeFileSync(join(dir, 'file'), String(++n))
    git('add', 'file')
    git('commit', '-q', '-m', subject)
  }
  git('init', '-q', '-b', 'main')
  // release.mjs runs git in the process's own directory and environment.
  const before = { cwd: process.cwd(), env: { ...process.env } }
  process.chdir(dir)
  Object.assign(process.env, { GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null', GIT_CONFIG_NOSYSTEM: '1' })
  delete process.env.GIT_DIR
  delete process.env.GIT_WORK_TREE
  t.after(() => {
    process.chdir(before.cwd)
    for (const key of Object.keys(process.env)) if (!(key in before.env)) delete process.env[key]
    Object.assign(process.env, before.env)
    rmSync(dir, { recursive: true, force: true })
  })
  return { git, commit }
}

test('CLI next: without a tag, --first names the version, whatever the commits ask for', async (t) => {
  const { commit } = scratchRepository(t)
  commit('chore: lay out the repository')
  commit('feat!: the world')
  assert.equal(await main(['next', '--bump', 'auto', '--first', '0.1.0']), '0.1.0')
})
test('CLI next: from the last tag on, the commits since it decide', async (t) => {
  const { git, commit } = scratchRepository(t)
  commit('feat: the world')
  git('tag', 'v0.1.0')
  await assert.rejects(main(['next', '--first', '0.1.0']), (e) => e instanceof ReleaseError && /nothing to release/.test(e.message))
  commit('fix(image): bridge 6.2.4-0noble')
  assert.equal(await main(['next', '--first', '0.1.0']), '0.1.1')
  commit('feat(sim): the turtle swims')
  assert.equal(await main(['next']), '0.2.0')
  assert.equal(await main(['next', '--bump', 'major']), '1.0.0')
})
test('CLI next: a tag that is not vX.Y.Z is not a release', async (t) => {
  const { git, commit } = scratchRepository(t)
  commit('feat: the world')
  git('tag', 'v0.1.0')
  commit('fix: a')
  git('tag', 'v0.2.0-next.1')
  git('tag', 'nightly')
  assert.equal(await main(['next']), '0.1.1')
})
test('CLI tag-at-head: the release tag on HEAD, or nothing', async (t) => {
  const { git, commit } = scratchRepository(t)
  commit('feat: the world')
  assert.equal(await main(['tag-at-head']), '')
  git('tag', 'v0.1.0')
  assert.equal(await main(['tag-at-head']), 'v0.1.0')
  commit('fix: a')
  assert.equal(await main(['tag-at-head']), '')
})
test('CLI: a --sha that is not 40 hex is a usage error before any network call', async () => {
  await assert.rejects(main(['create-tag', '--version', '0.1.0', '--sha', 'HEAD']), (e) => e instanceof ReleaseError && e.code === 2 && /40-hex/.test(e.message))
  await assert.rejects(main(['check-passed', '--sha', 'main', '--check', 'verify']), (e) => e instanceof ReleaseError && e.code === 2 && /40-hex/.test(e.message))
})
test('CLI: a command this copy dropped is a usage error, not a silent no-op', async () => {
  const dropped = ['rotate', 'changelog-add', 'changelog-section', 'release-pr', 'auto-merge', 'continuation', 'release-commit', 'prerelease', 'npm-state', 'npm-wait', 'pin-gate', 'prompt-gate', 'recipe-gate', 'dispatch', 'site-release']
  for (const command of dropped) await assert.rejects(main([command]), (e) => e instanceof ReleaseError && e.code === 2 && e.message === `unknown command '${command}'`)
  await assert.rejects(main([]), (e) => e.code === 2)
})
