#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Checks every fleetless/*.fleetless.yaml against the JSON Schema of
// @fleetless/contracts: the shape the console checks when a configuration
// is published. package.json pins the contracts version exactly.
//
// This reaches as far as a schema does. Whether a topic or a type matches
// what the turtle really publishes is only known once the robot is
// connected and the file is published.
//
//   pnpm install --frozen-lockfile && node scripts/validate-fleetless-yaml.mjs
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// The schema is draft 2020-12. Ajv's default export only knows draft-07.
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { load } from 'js-yaml';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SCHEMA_PATH = fileURLToPath(
  new URL('./artifacts/schema/robot-config-doc.schema.json', import.meta.resolve('@fleetless/contracts/package.json')),
);

function main() {
  const schema = JSON.parse(readFileSync(SCHEMA_PATH, 'utf8'));
  // Not strict: the schema carries keywords of its own, such as the
  // sentences shown for a failed pattern.
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  addFormats(ajv);
  const validate = ajv.compile(schema);

  const dir = join(ROOT, 'fleetless');
  const files = readdirSync(dir).filter((file) => file.endsWith('.fleetless.yaml'));
  if (files.length === 0) {
    console.error(`validate-fleetless-yaml: no *.fleetless.yaml in ${dir}`);
    process.exit(1);
  }

  let failed = false;
  for (const file of files) {
    const doc = load(readFileSync(join(dir, file), 'utf8'));
    if (validate(doc)) {
      console.log(`ok    ${file}`);
      continue;
    }
    failed = true;
    console.error(`FAIL  ${file}`);
    for (const error of validate.errors) {
      console.error(`  ${error.instancePath || '/'}: ${error.message}`);
    }
  }
  if (failed) process.exit(1);
}

main();
