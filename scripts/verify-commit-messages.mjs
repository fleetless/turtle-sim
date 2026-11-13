#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
/**
 * The commit messages in a range carry nothing internal.
 *
 * This history is public from the moment it is pushed, and nothing sweeps
 * it afterwards. The patterns are `scripts/internal-markers.mjs`; that file
 * says where they come from.
 *
 *   node scripts/verify-commit-messages.mjs            # origin/main..HEAD
 *   node scripts/verify-commit-messages.mjs <range>    # any git range
 *
 * It needs git and Node, and nothing installed.
 *
 * Which commits, case by case:
 *
 * | situation | range |
 * |---|---|
 * | an argument was given | that argument |
 * | a push to `main` (`PUSHED_BEFORE` and `PUSHED_HEAD`, from `verify.yml`) | exactly what was pushed |
 * | a push whose `PUSHED_BEFORE` is zeros or does not resolve | everything up to `PUSHED_HEAD` |
 * | a pull request, your machine | `origin/main..HEAD`: what the branch adds |
 *
 * A pull request is read whole on every push to it, never from the push
 * before: its earlier commits are part of what it proposes. On GitHub its
 * `HEAD` is the merge commit GitHub makes, so the range is that commit and
 * every commit of the branch.
 *
 * **Where it starts.** The check was added after `SINCE`. The commits up to
 * `SINCE` are never read, whatever the range says: they are published as
 * they are, and a pattern added later must not turn red over a message
 * nobody can change. A clone that does not hold `SINCE` is shallow; the
 * check refuses it rather than read less than it was asked to.
 *
 * It prints the range and the number of messages it read, so a run that
 * read one commit cannot look like a run that read twenty.
 *
 * What makes it fail: a host name, an internal id, a path into another
 * repository or a German sentence in a commit message of the range. Reword
 * the message and push again.
 */
import { execFileSync } from 'node:child_process'
import { realpathSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { DETECTOR_FLOOR, detectors, hitsIn } from './internal-markers.mjs'

/** The last commit written before this check. Reading begins after it. */
export const SINCE = '0b115e7d813a6d6c447245d7c73498d18041161b'

const ROOT = fileURLToPath(new URL('..', import.meta.url))

/** git in this repository. It throws when git exits non-zero. */
const gitHere = (...args) => execFileSync('git', args, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trimEnd()

/**
 * Whether `rev` names a commit this clone holds. `rev-parse --verify` alone
 * accepts any full-length hex string on its looks; `^{commit}` makes it look
 * the object up.
 */
export function resolves(git, rev) {
  try {
    git('rev-parse', '--verify', '--quiet', '--end-of-options', `${rev}^{commit}`)
    return true
  } catch {
    return false
  }
}

/** The range a run is about. The table in this file's header, as code. */
export function chooseRange({ argument, before, head, resolves }) {
  if (argument) return argument
  if (head && before && !/^0+$/.test(before) && resolves(before)) return `${before}..${head}`
  if (head) return head
  return 'origin/main..HEAD'
}

/**
 * Reads the messages of `range` after `since` and reports. Returns the exit
 * code: 0 when every message is clean or there is none, 1 otherwise.
 */
export function check({ git = gitHere, range, since = SINCE, say = console.log, complain = console.error }) {
  const start = since.slice(0, 7)
  if (!resolves(git, since)) {
    complain(`✗ ${start}, the commit this check starts after, is not in this clone.`)
    complain('  A shallow clone holds too little to say what was pushed. Fetch the history: git fetch --unshallow')
    return 1
  }
  let shas
  try {
    shas = git('rev-list', '--end-of-options', range, `^${since}`).split('\n').filter(Boolean)
  } catch (error) {
    complain(`✗ git cannot list the commits of ${range}: ${String(error.stderr ?? error.message).trim().split('\n')[0]}`)
    return 1
  }

  say(`== commit messages in ${range}, after ${start} (${shas.length}) ==`)

  // An empty range is not a clean range, and does not print like one.
  if (shas.length === 0) {
    say(`  no commits in ${range} after ${start}: nothing was checked`)
    return 0
  }

  const active = detectors()
  // A list that lost its detectors would report no hit over no pattern and
  // print the same line as a clean range.
  if (active.length < DETECTOR_FLOOR) {
    complain(`✗ only ${active.length} of ${DETECTOR_FLOOR} detectors are left in scripts/internal-markers.mjs`)
    return 1
  }

  const hits = []
  for (const sha of shas) {
    const message = git('log', '-1', '--format=%B', sha)
    for (const detector of active) hits.push(...hitsIn(detector, `${sha.slice(0, 9)} [${detector.name}]`, message))
  }

  if (hits.length > 0) {
    complain(`✗ ${hits.length} internal reference(s) in the commit messages of ${range}:`)
    for (const hit of hits) complain(`    ${hit}`)
    complain('')
    complain('  A commit message is public the moment it is pushed, and stays.')
    complain('  Reword it (git commit --amend, or git rebase -i for an older one) and push again.')
    return 1
  }

  say(`  ${shas.length} message(s) clean against ${active.length} detectors`)
  return 0
}

const invokedDirectly = (() => {
  try {
    return process.argv[1] !== undefined && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))
  } catch {
    return false
  }
})()
if (invokedDirectly) {
  const range = chooseRange({
    argument: process.argv[2],
    before: process.env.PUSHED_BEFORE,
    head: process.env.PUSHED_HEAD,
    resolves: (rev) => resolves(gitHere, rev),
  })
  process.exit(check({ range }))
}
