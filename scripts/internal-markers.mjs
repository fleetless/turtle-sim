// SPDX-License-Identifier: Apache-2.0
/**
 * What a commit message of this repository must not carry, as patterns.
 *
 * The history is public from the moment it is pushed, and nothing sweeps it
 * afterwards. So a message names nothing that only somebody inside the
 * organisation can resolve: a host, an internal id, a path on a developer's
 * machine, a file of another repository, a colleague, a sentence in German.
 * `verify-commit-messages.mjs` runs these patterns over the messages, and
 * `.github/workflows/verify.yml` runs that.
 *
 * The list is the organisation's. This copy is ported from the SDK's
 * (`scripts/internal-markers.mjs` in github.com/fleetless/sdk). A copy and
 * not a dependency, because the check has to run on a bare clone, with git
 * and Node and nothing to install. It is a subset:
 *
 *   - The SDK holds its source files to four more classes: a decision label,
 *     "this repository", a document the reader does not have, and how a
 *     behaviour was found. A commit message is about its repository and may
 *     say all four, there as here. They are not ported.
 *   - The SDK computes the set of files npm publishes. Nothing here goes to
 *     npm, and this repository's files have their own guard
 *     (`test/test_repository.py`).
 *
 * And it differs in one name: `docs` is not on the list of sibling
 * repositories. Here `docs/` is this repository's own directory, and a
 * message that names `docs/view.png` names a file its reader has.
 *
 * Every detector carries its own fixtures: a detector cannot be added
 * without a text it must match and one it must leave alone, and the test
 * runs them all. `DETECTOR_FLOOR` makes deleting one red.
 */

/**
 * German function words with no English homograph. Function words rather
 * than a topic vocabulary: prose switches language wholesale, so the cheap
 * words are the reliable detector, and a noun list would miss a paragraph
 * that happens to avoid its nouns.
 *
 * `was` and `hat` are deliberately absent: both are ordinary English words.
 */
const GERMAN = [
  'und', 'oder', 'nicht', 'aber', 'sondern', 'weil', 'dass', 'damit', 'wenn',
  'denn', 'doch', 'schon', 'noch', 'auch', 'nur', 'sehr', 'hier', 'dort',
  'jetzt', 'dann', 'immer', 'nie', 'alle', 'alles', 'viele', 'jede', 'jeder',
  'jedes', 'eine', 'einen', 'einem', 'einer', 'eines', 'kein', 'keine',
  'keinen', 'ist', 'sind', 'wurde', 'wurden', 'wird', 'werden', 'worden',
  'kann', 'können', 'muss', 'müssen', 'soll', 'sollen', 'darf', 'dürfen',
  'haben', 'hatte', 'hatten', 'für', 'über', 'unter', 'zwischen',
  'gegen', 'ohne', 'durch', 'nach', 'vor', 'bei', 'beim', 'zum', 'zur', 'vom',
  'aus', 'mit', 'von', 'auf', 'sich', 'ihre', 'ihrer', 'seine', 'seiner',
  'diese', 'dieser', 'dieses', 'welche', 'welcher', 'wer', 'wie',
  'warum', 'deshalb', 'daher', 'gemessen', 'gilt', 'steht', 'liegt',
]

/**
 * Each word is matched as `[Ww]ort`: a sentence-initial capital still
 * counts, an all-caps acronym does not. `mit` is on the list and `MIT` is
 * the licence of the three.js this repository vendors.
 *
 * Not covered, and known: German written in block capitals.
 */
const germanWord = (w) => `[${w[0].toUpperCase()}${w[0]}]${w.slice(1)}`

/**
 * URL-ish runs. Removed before the German scan only: several German
 * function words are also ordinary path segments (`/nach/`, `/bei/`).
 */
const URLISH = /\b[a-z][a-z0-9+.-]*:\/\/\S+|(?:^|[\s(`"'])\/[A-Za-z0-9_.:${}*-]+(?:\/[A-Za-z0-9_.:${}*-]+)+/g

/**
 * Single-token inline code spans. Removed before the German scan only.
 *
 * `` `von` `` is a citation and cannot be prose, while
 * `` `Das gilt nur hier` `` is a German sentence that happens to stand in
 * backticks. Hence one token and no more.
 */
const CODESPAN_TOKEN = /`[^`\s]*`/g

/**
 * A host or working tree that only a maintainer can reach, by shape.
 *
 * Four branches: any GitLab host, any `.cloud` host, any WireGuard peer
 * name, any tree renamed `.old`.
 *
 * No literal, and that is the point. A detector that spells the host it
 * hunts publishes it: the pattern becomes the leak, one file along. Each
 * branch is wider than the name it stands for, which is the safe direction.
 *
 * The guard after `.old` and `cloud` is written `(?![a-zA-Z0-9_])` rather
 * than `\b`, so that it reads the same in every engine the list is ported
 * to. It keeps `config.older` out.
 */
const INTERNAL_HOST =
  /gitlab\.(?:[a-z0-9-]+\.)*[a-z]{2,}|(?:[a-z0-9-]+\.)+cloud(?![a-zA-Z0-9_])|wg-[a-z0-9]+(?:-[a-z0-9]+)*|[a-z0-9_]+\.old(?![a-zA-Z0-9_])/i

/**
 * The organisation's other repositories whose files a message may not cite.
 * The SDK's list without `docs` (see the header). The test holds that none
 * of these names is a directory of this checkout: a path that begins with
 * one of them cannot be a path of this repository.
 */
export const SIBLING_REPOS = ['cloud', 'console', 'bridge', 'infra', 'contracts', 'sdk', 'web']

/**
 * `[^\p{L}]` rather than `\b`. `\b` in JavaScript is an ASCII word boundary:
 * `ü` is no word character to it, so it misses `über` after a space and
 * finds a boundary inside `darüber`.
 */
const germanPattern = new RegExp(`(^|[^\\p{L}])(${GERMAN.map(germanWord).join('|')})([^\\p{L}]|$)`, 'u')

/**
 * @returns {{name: string, pattern: RegExp, mustMatch: string[], mustNotMatch: string[]}[]}
 */
export function detectors() {
  const repos = SIBLING_REPOS.join('|')
  return [
    {
      name: 'German prose',
      pattern: germanPattern,
      mustMatch: [
        'Die Cloud wusste es die ganze Zeit und warf die Markierung weg.',
        // The umlaut-initial word an ASCII `\b` boundary misses.
        'Eine Grenze, über die niemand spricht.',
        // A single-token span is stripped; the prose beside it is not.
        'The `von` segment ist hier nicht gemeint.',
        // A sentence-initial capital.
        'Über die Grenze spricht niemand.',
        // A multi-word span is no citation and is not stripped.
        'The comment read `Das gilt nur mit einer Grenze` and was rewritten.',
      ],
      mustNotMatch: [
        // `MIT`, as this repository's NOTICE writes it.
        'MIT License, https://github.com/mrdoob/three.js',
        'three.js is vendored under the MIT license.',
        // A URL whose segments are also German function words.
        'See `https://example.test/api/v1/von/bei/nach/wie` for the shape.',
        'The path `/nach/bei` is reserved.',
        // German letters inside an English word.
        'The überflieger identifier is not a keyword here.',
        // Naming the words the detector looks for.
        'It stopped tripping on `von`, `bei`, `nach` and `wie`.',
      ],
    },
    {
      name: 'an internal feature id (FL-0xx)',
      // The prefix is the shape: an id class cannot be detected without the
      // letters that name it. The fixture's number is invented.
      pattern: /FL-0\d/,
      mustMatch: ['Closed under FL-07; the route answers 409 now.'],
      mustNotMatch: ['The flag is FL_ENABLED and defaults to false.', 'Serial FL-12 is printed on the chassis.'],
    },
    {
      name: 'an internal defect id (DEF-nnn)',
      pattern: /DEF-\d/,
      mustMatch: ['* **Was fehlt, und wovon (W0, DEF-77).**'],
      mustNotMatch: ['The default is DEF_TIMEOUT_MS.', 'A `DEF-` prefix is not used by this API.'],
    },
    {
      name: 'an internal wave label (W1..W9x)',
      // Case-sensitive, which is why it is a detector of its own: `w2` is
      // an ordinary identifier.
      pattern: /(^|[^\p{L}\d])W\d[a-e]?([^\p{L}\d]|$)/u,
      mustMatch: ['* **Was fehlt, und wovon (W0, DEF-77).**', 'Closed in W0, reopened in W9e.'],
      mustNotMatch: ['const w2 = { robots: {} }', 'The W3C spelling is different.', 'Bucket w9 holds the remainder.'],
    },
    {
      name: 'an internal wave or train label',
      // Four shapes: the numbered `wave 4` and `train 6 review`, the
      // determined `the wave` and `one train early`, one or two words in
      // between as in `the per-app train`, and `the wave's gate`.
      //
      // `a wave label` is left alone on purpose. Naming the class is not
      // using it, and this file has to be able to say what it looks for.
      pattern: /\b(?:wave|train)s?\s+(?:\d|lead\b)|\b(?:the|this|that|a|an|each|every|next|last|later|earlier|same|whole|entire|following|previous|one|two|three|four|five|several|another)\s+(?:[a-z][a-z-]*\s+){0,2}(?:waves?|trains?)\b(?!\s+labels?\b)|\bwave(?:'s)?\s+(?:lead|gate|review|sweep|browser)\b/iu,
      mustMatch: [
        'The flag is `attempted`, not `changed` (train 9 review, C97).',
        'It was renamed for a wave 9 deletion that did not happen.',
        'NOTE (from the wave lead): the shared cloud restarts.',
        'It sent `passwordResetRequest { email }` for an entire wave of commits.',
        'It has one producer already, one train early.',
        'The per-app train adds the second one.',
        'This is the defect this file has spent two waves removing.',
      ],
      mustNotMatch: [
        'The waveform is sampled at 40 Hz.',
        'Trained models are out of scope for this simulator.',
        'Put a hostname, a wave label or a pipeline number into a message.',
        '| internal wave labels | 134 | 0 |',
      ],
    },
    {
      name: 'an absolute path from a developer machine',
      pattern: /\/home\/|\/Users\//,
      mustMatch: ['Written to /home/someone/work/pkg/dist.', 'See /Users/someone/work/notes.txt for the tally.'],
      mustNotMatch: ['The page is served from `/`.', 'The view hands it out at /fleetless.yaml.'],
    },
    {
      name: 'the reference robot by name',
      // The one literal on the list. There is no shape behind it: two
      // letters and a digit is every other identifier. The SDK's public
      // copy of the list carries it too, so this one discloses nothing new.
      pattern: /rx1/i,
      mustMatch: ['Verified against rx1 rather than a container.', 'ssh wg-RX1-example'],
      mustNotMatch: ['The register is RX_1 on that board.', 'A `matrix1` field is not read.'],
    },
    {
      name: 'an internal host or workspace name',
      pattern: INTERNAL_HOST,
      mustMatch: [
        // A host inside a longer token, as a package manager writes one.
        '// node_modules/.pnpm/@fleetless+contracts@git+ssh+++git@gitlab.example.test+fleetless+f/node_modules/@fleetless/contracts/dist/common.js',
        // A host inside a markdown link.
        '[source](https://gitlab.example.test/example/example)',
        'Work items live at https://docs.example.cloud/notes.',
        'The tunnel peer is wg-example-app.',
        'The previous checkout is in tree.old-2026-01.',
      ],
      mustNotMatch: [
        'The console is at https://console.fleetless.dev.',
        'A `wg0` interface is not required.',
        // A bare `cloud` with no host in front of it, and `older`.
        'The bridge reports no connection to the cloud.',
        'It reads `config.older` from the cache directory.',
        // What this repository's own tests name as a cloud that is none.
        'FLEETLESS_CLOUD_URL=wss://cloud.invalid/bridge reaches the bridge.',
      ],
    },
    {
      name: 'a person by first name',
      pattern: /Andr[eé]\b/,
      mustMatch: ['Andre confirmed the shape on the call.'],
      mustNotMatch: ['The Andrews constant is unrelated.', 'Copyright 2026 Dehne Robotik GmbH'],
    },
    {
      name: 'an internal review codename',
      // Bare, and with a suffix. `Eve` and `Data` are ordinary enough words
      // that they need the suffix.
      pattern: /\b(?:Momus|Kassandra|Threepio|Argus|Nimbus|Rosie)\b|\b(?:Eve|Data|Momus|Kassandra|Threepio|Argus|Nimbus|Rosie)-W\d*[a-e]?\b/,
      mustMatch: ['confirmed with Nimbus: `time_bucket(width, captured_at)`', 'Raised by Eve-W0 and closed the same day.'],
      mustNotMatch: ['The datapoint payload is opaque data.', 'Every frame carries a `type` member.'],
    },
    {
      name: 'a file only the maintainers have',
      // In backticks or not: a message that cites such a file cites it
      // either way.
      pattern: /\bCLAUDE\.md\b|\.superpowers\/|\bdocs\/superpowers\/|\bMEMORY\.md\b/,
      mustMatch: ['A comment cited `CLAUDE.md` and is rewritten.', 'The plan is under docs/superpowers/plans/.'],
      mustNotMatch: ['See CONTRIBUTING.md for the test rules.', 'The claude field is not part of the schema.'],
    },
    {
      name: 'an internal task or step id',
      // `task 6.3` is a coordinate in a plan the reader does not have.
      pattern: /\b(?:tasks?|steps?)\s+\d+\.\d+\b/i,
      mustMatch: ["They are the browser check's subject (task 9.7).", 'Deferred to step 8.4.'],
      mustNotMatch: ['The task id is a uuid.', 'Retry after 1.5 seconds.', 'See section 6.3 of the RFC.', 'The world steps 50 times a second.'],
    },
    {
      name: 'an internal pipeline or job number',
      // The number of a pipeline or a merge request on a host only a
      // maintainer can open.
      pattern: /\b(?:pipelines?|MRs?|merge requests?)\s+#?\d{3,}|\bjobs?\s+#?\d{4,}/i,
      mustMatch: ['Measured on pipeline 9001: this runner uses bash.', 'A retry is safe here (pipeline 9001, job 9002).'],
      mustNotMatch: ['A job id is a uuid, never an integer.', 'The 202 carries `job_id`.', 'Cancel job 7 of the queue.'],
    },
    {
      name: 'a path into another repository',
      // Three shapes: a file (it ends in an extension), a directory of any
      // depth that ends in a slash, and a bare two-segment `cloud/src`. The
      // third stops at two segments on purpose: `the cloud/bridge/job are
      // untouched` is an alternation in a sentence, not a path.
      pattern: new RegExp(
        `(?<![\\w./-])(?:${repos})/(?:[A-Za-z0-9_.-]+/)*\\.?[A-Za-z0-9_-][A-Za-z0-9_.-]*\\.[A-Za-z]{1,6}(?![A-Za-z0-9])` +
          `|(?<![\\w./-])(?:${repos})(?:/[A-Za-z0-9_.-]+)+/(?![A-Za-z0-9])` +
          `|(?<![\\w./-])(?:${repos})/[A-Za-z0-9_.-]+(?![A-Za-z0-9_./-])`,
      ),
      mustMatch: [
        'Produced by `cloud/example/config.ts`.',
        'The shape is defined in sdk/src/rest.ts.',
        "Those are `infra/example/check.mjs`'s job.",
        'A generated script lives under infra/example/.',
        'The value is read from console/example/shell.mjs.',
        // A dotfile.
        '`bridge/.dockerignore` already leaves the tests out.',
        // A bare two-segment directory, with no trailing slash and no extension.
        'It is produced nowhere in cloud/example.',
        'See web/example/index.html for it.',
      ],
      mustNotMatch: [
        // This repository's own paths, also where a segment of one is a
        // sibling's name.
        'The check is `scripts/verify-commit-messages.mjs`.',
        'ros/fleetless_turtle_viewer/web/index.html names the stylesheet.',
        'docs/view.png is taken anew for that moment.',
        'Mount a checkout of the bridge at /bridge-src.',
        'The view hands it out at /fleetless.yaml.',
        'the cloud/bridge/job are entirely untouched',
        'The window is 3/4 of the configured budget.',
      ],
    },
  ]
}

/**
 * The floor under the detector list. Deleting a detector, the cheapest way
 * to make a red check green, is red in the test and in the check itself.
 * The count is stated rather than derived, so that a rename cannot take one
 * out quietly.
 */
export const DETECTOR_FLOOR = 14

/** The German scan is the only one that strips anything. */
export function normalise(line, detectorName) {
  return detectorName === 'German prose' ? line.replace(URLISH, ' ').replace(CODESPAN_TOKEN, ' ') : line
}

/**
 * Run one detector over a text and return `label:line: excerpt` for every
 * line that has a hit.
 * @param {{name: string, pattern: RegExp}} detector
 */
export function hitsIn(detector, label, text) {
  const out = []
  const lines = text.split('\n')
  for (let i = 0; i < lines.length; i++) {
    const m = normalise(lines[i], detector.name).match(detector.pattern)
    if (m) out.push(`${label}:${i + 1}: …${lines[i].slice(Math.max(0, m.index - 40), m.index + 80).trim()}…`)
  }
  return out
}
