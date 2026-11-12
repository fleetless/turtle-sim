#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// The release logic of this repository: which version comes next, the tag
// and the GitHub release. .github/workflows/release.yml builds the image
// and asks this file for the rest.
//
// Every Fleetless repository with a Release button carries a copy of one
// file. What keeps the copies from drifting apart is a shared list of test
// cases, not a shared dependency: public repositories cannot call an action
// from a private one, and a shared repository would sit in the path of
// every release. This copy is a subset. What is here is that file's code,
// unchanged, with its cases in release.test.mjs; a change to it belongs in
// every copy. What this repository has no use for is gone, with its cases:
//
//   - the changelog (rotate, changelog-add, changelog-section): there is no
//     CHANGELOG.md, the workflow writes the release notes
//   - the release pull request (release-pr, auto-merge, continuation,
//     release-commit): no file carries the version, so no commit has to
//     land before the tag
//   - npm (prerelease, npm-state, npm-wait): nothing goes to npm
//   - the gates and the announcement (pin-gate, prompt-gate, recipe-gate,
//     dispatch): nothing deploys the image and nothing records it
//   - site-release and next --require-tag: there is no tarball, and the
//     first version is --first 0.1.0, not a bootstrap tag
//
// The other copies create the tag as the organisation's App. Here
// GITHUB_TOKEN does it: no rule of this repository reserves tags.
//
//   release.mjs next [--bump auto|patch|minor|major] [--first X.Y.Z]        prints the next version
//   release.mjs tag-at-head                                                 prints the vX.Y.Z tag on HEAD, or nothing
//   release.mjs create-tag --version X.Y.Z --sha SHA                        GH_TOKEN, GITHUB_REPOSITORY
//   release.mjs github-release --version X.Y.Z [--dir D] [--notes-file F]   GH_TOKEN (GITHUB_TOKEN), GITHUB_REPOSITORY
//   release.mjs check-passed --sha SHA --check NAME                         GH_TOKEN, GITHUB_REPOSITORY
//
// Exit codes: 0 ok, 1 refused (the reason on stderr), 2 usage.

import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, realpathSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

export class ReleaseError extends Error {
  constructor(message, code = 1) {
    super(message)
    this.code = code
  }
}

const SEMVER = /^(\d+)\.(\d+)\.(\d+)$/
const RANK = { patch: 0, minor: 1, major: 2 }
const BUMPS = ['auto', 'patch', 'minor', 'major']

export function parseVersion(v) {
  const m = SEMVER.exec(String(v))
  if (!m) throw new ReleaseError(`'${v}' is not X.Y.Z`, 2)
  return m.slice(1).map(Number)
}

/** The bump one commit asks for, before the 0.x rule. */
export function commitBump({ subject, body = '' }) {
  if (/^[a-z]+(\([^)]*\))?!:/.test(subject) || /^BREAKING[ -]CHANGE:/m.test(body)) return 'major'
  if (/^feat(\([^)]*\))?:/.test(subject)) return 'minor'
  return 'patch'
}

/**
 * The next version from the commits since `current` (null: no tag yet).
 * Below 1.0 a breaking change is a minor — cloud went 0.21 → 0.22 for the
 * auth-config split — but an explicit `major` is the way out of 0.x.
 * An explicit bump only ever raises what the commits ask for.
 */
export function nextVersion({ current, commits, bump = 'auto', first = '1.0.0' }) {
  if (!BUMPS.includes(bump)) throw new ReleaseError(`--bump must be ${BUMPS.join(', ')} (got '${bump}')`, 2)
  if (commits.length === 0) throw new ReleaseError('nothing to release: no commits since the last tag')
  if (current === null) {
    parseVersion(first)
    return { version: first, bump: 'first' }
  }
  const [major, minor, patch] = parseVersion(current)
  let wanted = commits.map(commitBump).reduce((a, b) => (RANK[b] > RANK[a] ? b : a), 'patch')
  if (major === 0 && wanted === 'major') wanted = 'minor'
  if (bump !== 'auto' && RANK[bump] > RANK[wanted]) wanted = bump
  const version =
    wanted === 'major' ? `${major + 1}.0.0` : wanted === 'minor' ? `${major}.${minor + 1}.0` : `${major}.${minor}.${patch + 1}`
  return { version, bump: wanted }
}

// ── git ─────────────────────────────────────────────────────────────────

const git = (...args) => execFileSync('git', args, { encoding: 'utf8' }).trim()
const RELEASE_TAG = /^v\d+\.\d+\.\d+$/

export function currentTag() {
  return git('tag', '--list', 'v[0-9]*.[0-9]*.[0-9]*', '--merged', 'HEAD', '--sort=-v:refname').split('\n').find((t) => RELEASE_TAG.test(t)) ?? null
}

export function commitsSince(tag) {
  const out = git('log', '--format=%H%x1f%s%x1f%b%x1e', tag ? `${tag}..HEAD` : 'HEAD')
  return out
    .split('\x1e')
    .map((r) => r.replace(/^\n+/, ''))
    .filter((r) => r.trim() !== '')
    .map((r) => {
      const [sha, subject, body = ''] = r.split('\x1f')
      return { sha, subject, body }
    })
}

export function tagAtHead() {
  return git('tag', '--points-at', 'HEAD').split('\n').find((t) => RELEASE_TAG.test(t)) ?? null
}

// ── GitHub ──────────────────────────────────────────────────────────────

/**
 * One REST call. A 404 on a read (GET) or on a delete of something already
 * gone (DELETE) answers null — both are legitimate "not there". A 404 on a
 * write (POST, PUT, PATCH) is not a softer form of success: it means the
 * dispatch, the tag object or the merge never happened, so it is a refusal
 * like any other failed response, not a null the caller might mistake for success.
 */
export async function github(method, path, { body, raw, base = 'https://api.github.com', token = process.env.GH_TOKEN, fetchImpl = globalThis.fetch, graphql = false } = {}) {
  if (!token) throw new ReleaseError('GH_TOKEN is not set', 2)
  const headers = { authorization: `Bearer ${token}`, accept: 'application/vnd.github+json', 'x-github-api-version': '2022-11-28' }
  if (raw) headers['content-type'] = 'application/octet-stream'
  else if (body) headers['content-type'] = 'application/json'
  const res = await fetchImpl(`${base}${path}`, { method, headers, body: raw ?? (body ? JSON.stringify(body) : undefined) })
  if (res.status === 404 && (method === 'GET' || method === 'DELETE')) return null
  if (!res.ok) throw new ReleaseError(`${method} ${path}: ${res.status} ${await res.text()}`)
  if (res.status === 204) return {}
  const json = await res.json()
  // A GraphQL refusal is a 200 with an `errors` array, not an HTTP error.
  // Throw so no caller can mistake it for success; autoMerge catches and
  // recognises the two names it handles (clean status, auto-merge not
  // allowed) and refuses the rest. A success answers its `data`.
  if (graphql && json.errors?.length) throw new ReleaseError(json.errors.map((e) => e.message).join('; '))
  return graphql ? json.data : json
}

/** A release tag, created by the App. An existing tag is reused, never moved. */
export async function createTag({ repo, version, sha, api = github }) {
  parseVersion(version)
  const ref = await api('GET', `/repos/${repo}/git/ref/tags/v${version}`)
  if (ref) {
    let at = ref.object.sha
    if (ref.object.type === 'tag') at = (await api('GET', `/repos/${repo}/git/tags/${at}`)).object.sha
    if (at !== sha) throw new ReleaseError(`v${version} already exists at ${at}, not at ${sha} — a tag is not moved`)
    return 'exists'
  }
  const tag = await api('POST', `/repos/${repo}/git/tags`, { body: { tag: `v${version}`, message: `v${version}`, object: sha, type: 'commit' } })
  if (!tag) throw new ReleaseError(`could not create the tag object v${version} in ${repo}`)
  const made = await api('POST', `/repos/${repo}/git/refs`, { body: { ref: `refs/tags/v${version}`, sha: tag.sha } })
  if (!made) throw new ReleaseError(`could not create refs/tags/v${version} in ${repo}`)
  return 'created'
}

/**
 * The GitHub release for vX.Y.Z: exactly `files` and a SHA256SUMS over them,
 * or — with no files (app-starter) — the notes alone. Uploaded with the token
 * this runs under, GITHUB_TOKEN, because the ops repository's
 * fetch-site-release.sh and fetch-bridge-release.sh refuse any other
 * uploader. Every asset present (a re-run): left alone, the earlier build
 * stands. Some of them: removed, all uploaded again. Anything else: refused,
 * not overwritten.
 *
 * `files` is [{ name, bytes }]; the notes are the body of a release this
 * call creates, and are not rewritten on one that exists.
 */
export async function publishRelease({ repo, version, files = [], notes, api = github }) {
  parseVersion(version)
  const names = files.map((f) => f.name)
  if (names.includes('SHA256SUMS')) throw new ReleaseError('SHA256SUMS is written here, not handed in', 2)
  if (new Set(names).size !== names.length) throw new ReleaseError(`two assets share a name: ${names.join(', ')}`, 2)
  let release = await api('GET', `/repos/${repo}/releases/tags/v${version}`)
  let created = false
  if (release) {
    // A release this workflow did not make — hand-drafted, or by someone
    // else's identity — is not a re-run to continue; it is refused, the same
    // way a foreign asset on an otherwise-matching release is refused below.
    if (release.draft) throw new ReleaseError(`the release v${version} in ${repo} is a draft — refusing to reuse it`)
    if (release.author?.login !== 'github-actions[bot]')
      throw new ReleaseError(`the release v${version} in ${repo} was not made by github-actions[bot] (author: ${release.author?.login ?? 'unknown'}) — refusing to reuse it`)
  } else {
    release = await api('POST', `/repos/${repo}/releases`, { body: { tag_name: `v${version}`, name: `v${version}`, body: notes } })
    if (!release) throw new ReleaseError(`could not create the release v${version} in ${repo}`)
    created = true
  }
  const expected = names.length ? [...names, 'SHA256SUMS'] : []
  const have = new Map((release.assets ?? []).map((a) => [a.name, a]))
  const foreign = [...have.keys()].filter((n) => !expected.includes(n))
  if (foreign.length) throw new ReleaseError(`the release v${version} carries assets this workflow did not make: ${foreign.join(', ')}`)
  if (expected.every((n) => have.has(n))) return created ? 'created' : 'exists'
  for (const a of have.values()) await api('DELETE', `/repos/${repo}/releases/assets/${a.id}`)
  const upload = release.upload_url.replace(/\{.*\}$/, '')
  const put = (n, data) => api('POST', `${upload}?name=${encodeURIComponent(n)}`, { raw: data, base: '' })
  let sums = ''
  for (const f of files) {
    await put(f.name, f.bytes)
    sums += `${createHash('sha256').update(f.bytes).digest('hex')}  ${f.name}\n`
  }
  await put('SHA256SUMS', Buffer.from(sums))
  return 'uploaded'
}

/**
 * Belt and braces before the tag: the App is a bypass actor on every
 * `main`, so GitHub would merge a release PR without its check if the
 * ruleset ever changed. The continuation refuses to tag a commit whose
 * required check did not complete successfully.
 */
export async function checkPassed({ repo, sha, check, api = github }) {
  const runs = await api('GET', `/repos/${repo}/commits/${sha}/check-runs`)
  const ok = (runs?.check_runs ?? []).some((r) => r.name === check && r.status === 'completed' && r.conclusion === 'success')
  if (!ok) throw new ReleaseError(`no successful ${check} check run on ${sha}`)
  return true
}

// ── CLI ─────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const [command, ...rest] = argv
  const flags = {}
  for (let i = 0; i < rest.length; i++) {
    if (!rest[i].startsWith('--')) throw new ReleaseError(`unexpected argument '${rest[i]}'`, 2)
    const key = rest[i].slice(2)
    const next = rest[i + 1]
    if (next === undefined || next.startsWith('--')) flags[key] = true
    else {
      flags[key] = next
      i++
    }
  }
  return { command, flags }
}

function need(flags, key) {
  if (typeof flags[key] !== 'string' || flags[key] === '') throw new ReleaseError(`--${key} <value> is required`, 2)
  return flags[key]
}

function repository() {
  const repo = process.env.GITHUB_REPOSITORY
  if (!repo) throw new ReleaseError('GITHUB_REPOSITORY is not set', 2)
  return repo
}

export async function main(argv) {
  const { command, flags } = parseArgs(argv)
  switch (command) {
    case 'next': {
      const current = currentTag()
      const commits = commitsSince(current)
      const { version, bump } = nextVersion({ current: current ? current.slice(1) : null, commits, bump: flags.bump ?? 'auto', first: flags.first ?? '1.0.0' })
      console.error(`${current ?? 'no tag yet'} + ${commits.length} commit(s) → ${version} (${bump})`)
      return version
    }
    case 'tag-at-head':
      return tagAtHead() ?? ''
    case 'create-tag': {
      const sha = need(flags, 'sha')
      if (!/^[0-9a-f]{40}$/.test(sha)) throw new ReleaseError(`--sha '${sha}' is not a 40-hex commit`, 2)
      const version = need(flags, 'version')
      const outcome = await createTag({ repo: repository(), version, sha })
      console.error(`v${version}: ${outcome} at ${sha}`)
      return ''
    }
    case 'github-release': {
      const version = need(flags, 'version')
      // --dir: every regular file in it except a SHA256SUMS, which is
      // written again over exactly what goes up.
      const dir = typeof flags.dir === 'string' ? flags.dir : null
      const files = dir
        ? readdirSync(dir, { withFileTypes: true })
            .filter((e) => e.isFile() && e.name !== 'SHA256SUMS')
            .map((e) => e.name)
            .sort()
            .map((name) => ({ name, bytes: readFileSync(join(dir, name)) }))
        : []
      if (dir && files.length === 0) throw new ReleaseError(`${dir} holds no file to release`)
      const notes = typeof flags['notes-file'] === 'string' ? readFileSync(flags['notes-file'], 'utf8').trim() : `${repository().split('/')[1]} v${version}`
      const outcome = await publishRelease({ repo: repository(), version, files, notes })
      console.error(`release v${version}: ${files.length ? `${files.length} file(s) + SHA256SUMS` : 'notes only'}, ${outcome}`)
      return ''
    }
    case 'check-passed': {
      const sha = need(flags, 'sha')
      if (!/^[0-9a-f]{40}$/.test(sha)) throw new ReleaseError(`--sha '${sha}' is not a 40-hex commit`, 2)
      await checkPassed({ repo: repository(), sha, check: need(flags, 'check') })
      return ''
    }
    default:
      throw new ReleaseError(`unknown command '${command ?? ''}'`, 2)
  }
}

const invokedDirectly = (() => {
  try {
    return process.argv[1] !== undefined && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))
  } catch {
    return false
  }
})()
if (invokedDirectly) {
  main(process.argv.slice(2)).then(
    (out) => {
      if (out) console.log(out)
    },
    (error) => {
      if (error instanceof ReleaseError) {
        console.error(`release: ${error.message}`)
        process.exit(error.code)
      }
      throw error
    },
  )
}
