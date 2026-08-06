// End-to-end smoke against a running `wrangler dev` (port 8799). Exercises the
// full pairing -> claim -> proxy -> demo flow with a throwaway licence key that
// matches the LICENSE_PUBLIC_KEY_B64 set in .dev.vars.
const BASE = process.env.BASE || "http://127.0.0.1:8799";
const PRIV_B64URL = "MC4CAQAwBQYDK2VwBCIEIHraDcuqlzbjWa3oz72qzQ-kWcY3rLUUqNbsWjhk8TNP";

const b64url = (b) => Buffer.from(b).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromB64url = (s) => Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");

let failures = 0;
function check(name, cond, extra = "") {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}${extra ? "  — " + extra : ""}`);
  if (!cond) failures++;
}

async function mintLicense() {
  const priv = await crypto.subtle.importKey("pkcs8", fromB64url(PRIV_B64URL), { name: "Ed25519" }, false, ["sign"]);
  const payload = { v: 1, product: "easypost-desktop", order: "ord_test", tier: "business", email: "rev@x.com" };
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, priv, bytes);
  return "EPD1." + b64url(bytes) + "." + b64url(sig);
}

const post = (path, body, headers = {}) =>
  fetch(BASE + path, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) });
const get = (path, headers = {}) => fetch(BASE + path, { headers });

async function main() {
  const health = await (await get("/health")).json();
  check("health ok", health.ok === true);

  const license = await mintLicense();
  const pairingToken = "tok-" + b64url(crypto.getRandomValues(new Uint8Array(12)));

  // register
  const reg = await post("/pair/register", { pairing_token: pairingToken, easypost_key: "EZTKfake_reviewer_key", license });
  const regBody = await reg.json();
  check("register accepts valid licence", reg.status === 200 && regBody.ok === true, `tier=${regBody.tier}`);

  const badReg = await post("/pair/register", { pairing_token: "x", easypost_key: "k", license: "EPD1.bad.bad" });
  check("register rejects bad licence", badReg.status === 403);

  // claim
  const claim = await post("/pair/claim", { pairing_token: pairingToken, platform: "ios" });
  const claimBody = await claim.json();
  check("claim returns device_token + kek", claim.status === 200 && !!claimBody.device_token && !!claimBody.kek);

  const claim2 = await post("/pair/claim", { pairing_token: pairingToken });
  check("pairing token is single-use (burned)", claim2.status === 404);

  const { device_token, kek } = claimBody;
  const auth = { authorization: "Bearer " + device_token, "x-ep-kek": kek };

  // proxy auth + allow-list
  const noAuth = await get("/ep/trackers");
  check("proxy rejects missing auth", noAuth.status === 401);

  const disallowed = await post("/ep/shipments", { shipment: {} }, auth);
  const disallowedBody = await disallowed.json();
  check("proxy refuses non-allow-listed op", disallowed.status === 403, disallowedBody.error);

  const badKek = await get("/ep/trackers", { authorization: "Bearer " + device_token, "x-ep-kek": b64url(crypto.getRandomValues(new Uint8Array(32))) });
  check("proxy rejects wrong KEK", badKek.status === 401);

  // allowed op forwards to EasyPost. The fake key gets rejected upstream, but
  // EasyPost's error shape ({error:{code:...}}) — not our {error:"string"} —
  // proves the Worker decrypted the key and relayed the call.
  const fwd = await get("/ep/trackers", auth);
  let fwdJson = {};
  try { fwdJson = JSON.parse(await fwd.text()); } catch {}
  const forwarded = fwdJson.error && typeof fwdJson.error === "object" && !!fwdJson.error.code;
  check("allowed op decrypts key + forwards to EasyPost", forwarded, `upstream=${fwdJson?.error?.code || fwd.status}`);

  // demo / reviewer path
  const demo = await post("/pair/demo", { code: "REVIEW-TEST-123", platform: "android" });
  const demoBody = await demo.json();
  check("review code grants demo access", demo.status === 200 && demoBody.demo === true && !!demoBody.device_token);

  const demoBad = await post("/pair/demo", { code: "WRONG" });
  check("wrong review code refused", demoBad.status === 403);

  console.log(failures === 0 ? "\nALL PASS" : `\n${failures} FAILURE(S)`);
  process.exit(failures === 0 ? 0 : 1);
}
main().catch((e) => { console.error(e); process.exit(2); });
