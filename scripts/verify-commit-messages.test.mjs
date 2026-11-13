// SPDX-License-Identifier: Apache-2.0
// The cases the commit-message check must pass: the detectors with their
// fixtures, the choice of the range, and the check against a real git
// history.
//
//   node --test "scripts/*.test.mjs"
//
// The last section runs git in a temporary directory; nothing here uses the
// network.

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { DETECTOR_FLOOR, SIBLING_REPOS, detectors, hitsIn, normalise } from './internal-markers.mjs'
import { SINCE, check, chooseRange, resolves } from './verify-commit-messages.mjs'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const DETECTORS = detectors()

// ── the detectors themselves ────────────────────────────────────────────

test('every detector is there, under a name of its own, with both kinds of fixture', () => {
  // The floor. Deleting a detector, or adding one without a text it must
  // match and one it must leave alone, is red here.
  assert.equal(DETECTOR_FLOOR, 14)
  assert.ok(DETECTORS.length >= DETECTOR_FLOOR, `${DETECTORS.length} detectors, the floor is ${DETECTOR_FLOOR}`)
  assert.equal(new Set(DETECTORS.map((d) => d.name)).size, DETECTORS.length)
  for (const d of DETECTORS) {
    assert.ok(d.pattern instanceof RegExp, `${d.name} has no pattern`)
    assert.ok(!d.pattern.global && !d.pattern.sticky, `${d.name} would remember where it last matched`)
    assert.ok(d.mustMatch.length > 0, `${d.name} has no fixture it must match`)
    assert.ok(d.mustNotMatch.length > 0, `${d.name} has no fixture it must leave alone`)
  }
})

for (const d of DETECTORS) {
  for (const text of d.mustMatch) {
    test(`${d.name} catches: ${text}`, () => {
      assert.equal(d.pattern.test(normalise(text, d.name)), true)
      assert.equal(hitsIn(d, 'fixture', text).length, 1)
    })
  }
  for (const text of d.mustNotMatch) {
    test(`${d.name} leaves alone: ${text}`, () => {
      assert.equal(d.pattern.test(normalise(text, d.name)), false)
      assert.deepEqual(hitsIn(d, 'fixture', text), [])
    })
  }
}

test('a hit names its label, its line and the words around it', () => {
  const host = DETECTORS.find((d) => d.name === 'an internal host or workspace name')
  const message = 'fix: a subject\n\nThe runner is at gitlab.example.test, and so on.\n'
  assert.deepEqual(hitsIn(host, 'abc123def [an internal host or workspace name]', message), [
    'abc123def [an internal host or workspace name]:3: …The runner is at gitlab.example.test, and so on.…',
  ])
})

test('no sibling repository on the list is a directory of this checkout', () => {
  // A path that begins with a sibling's name is then never one of this
  // repository's own. `docs` is one of ours, which is why it is not listed.
  for (const name of SIBLING_REPOS) assert.equal(existsSync(join(ROOT, name)), false, `${name}/ exists here`)
  assert.equal(SIBLING_REPOS.includes('docs'), false)
  assert.equal(existsSync(join(ROOT, 'docs')), true)
})

// ── the range ───────────────────────────────────────────────────────────

const A = 'a'.repeat(40)
const B = 'b'.repeat(40)
const all = () => true
const none = () => false

test('range: an argument wins over everything', () => {
  assert.equal(chooseRange({ argument: 'main~3..main', before: A, head: B, resolves: all }), 'main~3..main')
})
test('range: a push is exactly what was pushed', () => {
  assert.equal(chooseRange({ before: A, head: B, resolves: all }), `${A}..${B}`)
})
test('range: a push without a usable before is everything up to its head', () => {
  // A first push names zeros, a rewritten history a commit nobody has.
  assert.equal(chooseRange({ before: '0'.repeat(40), head: B, resolves: all }), B)
  assert.equal(chooseRange({ before: A, head: B, resolves: none }), B)
  assert.equal(chooseRange({ before: '', head: B, resolves: all }), B)
  assert.equal(chooseRange({ head: B, resolves: all }), B)
})
test('range: a pull request and a developer get what the branch adds', () => {
  assert.equal(chooseRange({ resolves: all }), 'origin/main..HEAD')
  assert.equal(chooseRange({ argument: undefined, before: '', head: '', resolves: all }), 'origin/main..HEAD')
  // A before alone names no push.
  assert.equal(chooseRange({ before: A, resolves: all }), 'origin/main..HEAD')
})

// ── the check against a real git history ────────────────────────────────

// A repository of its own in a temporary directory, with nothing of the
// machine's git configuration in it: a signing key or a hook of whoever
// runs the tests must not decide the result.
function scratchRepository(t) {
  const dir = mkdtempSync(join(tmpdir(), 'commit-messages-test-'))
  const env = { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null', GIT_CONFIG_NOSYSTEM: '1' }
  delete env.GIT_DIR
  delete env.GIT_WORK_TREE
  const git = (...args) =>
    execFileSync('git', ['-c', 'user.name=test', '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false', ...args], {
      cwd: dir,
      env,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    }).trimEnd()
  let n = 0
  /** One commit with this message; returns its hash. */
  const commit = (message) => {
    writeFileSync(join(dir, 'file'), String(++n))
    git('add', 'file')
    git('commit', '-q', '-m', message)
    return git('rev-parse', 'HEAD')
  }
  git('init', '-q', '-b', 'main')
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  return { git, commit }
}

/** Runs the check and returns its exit code and both of its voices. */
function run(options) {
  const said = []
  const complained = []
  const code = check({ ...options, say: (line) => said.push(line), complain: (line) => complained.push(line) })
  return { code, said, complained }
}

const CLEAN = 'fix(viewer): the page says when the socket dropped\n\nIt said nothing and showed the last pose.'
const HOSTNAME = 'ci: a runner of our own\n\nThe job now runs on gitlab.example.test, which is faster.'

test('check: a clean range passes and says how many messages it read', (t) => {
  const { git, commit } = scratchRepository(t)
  const since = commit('chore: lay out the repository')
  commit(CLEAN)
  commit('docs(readme): a picture of the pond')
  const { code, said, complained } = run({ git, range: 'HEAD', since })
  assert.equal(code, 0)
  assert.deepEqual(said, [`== commit messages in HEAD, after ${since.slice(0, 7)} (2) ==`, '  2 message(s) clean against 14 detectors'])
  assert.deepEqual(complained, [])
})

test('check: a host name in a message is refused, with the commit and the class', (t) => {
  const { git, commit } = scratchRepository(t)
  const since = commit('chore: lay out the repository')
  commit(CLEAN)
  const bad = commit(HOSTNAME)
  commit('docs(readme): a picture of the pond')
  const { code, said, complained } = run({ git, range: `${since}..HEAD`, since })
  assert.equal(code, 1)
  assert.deepEqual(said, [`== commit messages in ${since}..HEAD, after ${since.slice(0, 7)} (3) ==`])
  assert.equal(complained[0], `✗ 1 internal reference(s) in the commit messages of ${since}..HEAD:`)
  assert.equal(complained[1], `    ${bad.slice(0, 9)} [an internal host or workspace name]:3: …The job now runs on gitlab.example.test, which is faster.…`)
  assert.match(complained.join('\n'), /Reword it/)
})

test('check: every class is found in a message, each under its own name', (t) => {
  const { git, commit } = scratchRepository(t)
  const since = commit('chore: lay out the repository')
  for (const d of DETECTORS) commit(`fix: one of each\n\n${d.mustMatch[0]}`)
  const { code, complained } = run({ git, range: 'HEAD', since })
  assert.equal(code, 1)
  for (const d of DETECTORS) assert.ok(complained.some((line) => line.includes(`[${d.name}]`)), `${d.name} was not reported`)
})

test('check: the commits up to the floor are never read, whatever the range says', (t) => {
  const { git, commit } = scratchRepository(t)
  commit(HOSTNAME)
  const since = commit(HOSTNAME)
  const after = commit(CLEAN)
  // The whole history, and a range that begins before the floor.
  for (const range of ['HEAD', `${since}~1..HEAD`]) {
    const { code, said } = run({ git, range, since })
    assert.equal(code, 0, range)
    assert.equal(said[0], `== commit messages in ${range}, after ${since.slice(0, 7)} (1) ==`)
  }
  // And the commit after the floor is read.
  commit(HOSTNAME)
  assert.equal(run({ git, range: `${after}..HEAD`, since }).code, 1)
  assert.equal(run({ git, range: 'HEAD', since }).code, 1)
})

test('check: an empty range says that nothing was checked', (t) => {
  const { git, commit } = scratchRepository(t)
  const since = commit('chore: lay out the repository')
  const { code, said, complained } = run({ git, range: 'HEAD..HEAD', since })
  assert.equal(code, 0)
  assert.deepEqual(said, [`== commit messages in HEAD..HEAD, after ${since.slice(0, 7)} (0) ==`, `  no commits in HEAD..HEAD after ${since.slice(0, 7)}: nothing was checked`])
  assert.deepEqual(complained, [])
})

test('check: a clone without the floor is refused, not read from wherever it begins', (t) => {
  const { git, commit } = scratchRepository(t)
  commit(CLEAN)
  const { code, said, complained } = run({ git, range: 'HEAD', since: SINCE })
  assert.equal(code, 1)
  assert.deepEqual(said, [])
  assert.match(complained[0], /^✗ 0b115e7, the commit this check starts after, is not in this clone\.$/)
})

test('check: a range git does not know is a refusal with a sentence', (t) => {
  const { git, commit } = scratchRepository(t)
  const since = commit('chore: lay out the repository')
  const { code, said, complained } = run({ git, range: 'origin/main..HEAD', since })
  assert.equal(code, 1)
  assert.deepEqual(said, [])
  assert.equal(complained.length, 1)
  assert.match(complained[0], /^✗ git cannot list the commits of origin\/main\.\.HEAD: /)
})

test('resolves: a commit the clone holds, and nothing that only looks like one', (t) => {
  const { git, commit } = scratchRepository(t)
  const sha = commit('chore: lay out the repository')
  assert.equal(resolves(git, sha), true)
  assert.equal(resolves(git, 'HEAD'), true)
  assert.equal(resolves(git, 'f'.repeat(40)), false)
  assert.equal(resolves(git, 'no-such-branch'), false)
})

test('this repository holds the commit the check starts after', () => {
  const git = (...args) => execFileSync('git', args, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trimEnd()
  assert.match(SINCE, /^[0-9a-f]{40}$/)
  assert.equal(resolves(git, SINCE), true, 'a shallow clone? fetch the history: git fetch --unshallow')
})
