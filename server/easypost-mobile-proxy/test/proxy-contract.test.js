/**
 * The query string must reach EasyPost.
 *
 * This is a cross-repo contract and it fails silently. The mobile app's list
 * screens page with `page_size` and `before_id`; drop them and EasyPost returns
 * only the first page. Nothing errors, no test in this repository goes red, and
 * the app just shows short lists — in the review account, 25 of 54 trackers,
 * every one `delivered`, which made it look incapable of any other status.
 *
 * These read the source rather than calling `handleProxy`, which is not
 * exported and would need D1 bindings and a paired device to reach. The two
 * failure modes worth catching are both visible in the text: the query being
 * dropped from the upstream URL, and the allow-list moving from the path to the
 * whole URL. A behavioural test would be better; no test at all would be worse,
 * and a comment on its own has already proved too easy to walk past.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const SOURCE = readFileSync(
  fileURLToPath(new URL("../src/worker.js", import.meta.url)),
  "utf8",
);

/** The upstream URL line, comments stripped. */
function upstreamLine() {
  const line = SOURCE.split("\n").find(
    (l) => l.includes("const upstream") && !l.trimStart().startsWith("//"),
  );
  assert.ok(line, "could not find the `const upstream` assignment");
  return line;
}

test("the upstream URL forwards the query string", () => {
  assert.match(
    upstreamLine(),
    /\$\{url\.search\}/,
    "the upstream URL no longer interpolates url.search — the mobile app's " +
      "list screens will silently return only their first page",
  );
});

test("the upstream URL is built from the EasyPost base and the stripped path", () => {
  const line = upstreamLine();
  assert.match(line, /\$\{env\.EASYPOST_API_BASE\}/);
  assert.match(line, /\$\{epPath\}/);
});

test("the allow-list matches on the path, never the query", () => {
  // isAllowed(method, epPath). If this ever takes a full URL or url.href, a
  // path pattern anchored with $ stops matching as soon as a query is present,
  // and every paged request 403s — or, worse, someone "fixes" that by
  // dropping the query.
  assert.match(
    SOURCE,
    /function isAllowed\(method, epPath\)/,
    "isAllowed's signature changed; it must take the stripped path",
  );
  assert.match(
    SOURCE,
    /a\.re\.test\(epPath\)/,
    "isAllowed no longer tests epPath — matching the full URL breaks paging",
  );
  assert.doesNotMatch(
    SOURCE,
    /a\.re\.test\((?:url\.href|url\.toString\(\)|upstream)\)/,
    "the allow-list is matching a full URL rather than the path",
  );
});

test("epPath is the pathname with the /ep prefix removed, not the whole URL", () => {
  assert.match(
    SOURCE,
    /const epPath = url\.pathname\.slice\("\/ep"\.length\)/,
    "epPath is derived differently now; check it still excludes the query",
  );
});
