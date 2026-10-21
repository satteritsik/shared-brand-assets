#!/usr/bin/env node
/**
 * Pinned replacement for the `.gitnexus/run.cjs` runner.
 *
 * `gitnexus analyze` writes its own `run.cjs` beside every index, and that
 * generated runner falls back to `gitnexus@latest` when no `gitnexus` is on
 * PATH. An index written by a newer release cannot be read by an older one, so
 * `ops/indexing/gitnexus-reindex.sh` copies this file over the generated
 * runner after each analyze.
 *
 * This runner sits in `<repository>/.gitnexus/` and hands every invocation to
 * the same repository's `ops/indexing/gitnexus-pinned.sh`, which runs the
 * version in `ops/indexing/gitnexus-version`. It exits 2 when that script is
 * absent.
 */

"use strict";

const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const pinned = path.join(
  __dirname,
  "..",
  "ops",
  "indexing",
  "gitnexus-pinned.sh",
);
if (!fs.existsSync(pinned)) {
  process.stderr.write(
    `gitnexus runner: pinned launcher not found: ${pinned}\n`,
  );
  process.exit(2);
}

try {
  execFileSync(pinned, process.argv.slice(2), { stdio: "inherit" });
} catch (err) {
  if (typeof err.status !== "number") {
    process.stderr.write(
      `gitnexus runner: could not launch ${pinned}: ${err.message}\n`,
    );
  }
  process.exit(typeof err.status === "number" ? err.status : 1);
}
