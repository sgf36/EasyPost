import { test } from "node:test";
import assert from "node:assert/strict";
import {
  encryptWithNewKek,
  decryptWithKek,
  verifyLicense,
  b64urlEncode,
} from "../src/crypto.js";

const ENV = {
  LICENSE_FORMAT_TAG: "EPD1",
  LICENSE_PRODUCT_ID: "easypost-desktop",
};

test("AES-GCM round-trips a key with its KEK", async () => {
  const secret = "prod_abc123EASYPOSTKEY";
  const { kek, ciphertext, iv } = await encryptWithNewKek(secret);
  assert.equal(await decryptWithKek(kek, ciphertext, iv), secret);
});

test("wrong KEK cannot decrypt (auth failure)", async () => {
  const { ciphertext, iv } = await encryptWithNewKek("prod_secret");
  const otherKek = b64urlEncode(crypto.getRandomValues(new Uint8Array(32)));
  await assert.rejects(() => decryptWithKek(otherKek, ciphertext, iv));
});

test("ciphertext alone reveals nothing (no KEK stored)", async () => {
  const { kek, ciphertext } = await encryptWithNewKek("prod_secret");
  // The stored ciphertext must not contain the KEK anywhere.
  assert.ok(!ciphertext.includes(kek));
});

// Mint a token with a throwaway keypair, point the env at its public key, and
// confirm verifyLicense accepts genuine tokens and rejects tampered ones.
async function mintToken(privKey, payload) {
  const payloadBytes = new TextEncoder().encode(JSON.stringify(payload));
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, privKey, payloadBytes);
  return "EPD1." + b64urlEncode(payloadBytes) + "." + b64urlEncode(sig);
}

test("verifyLicense accepts a genuine token and reads its claims", async () => {
  const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const rawPub = await crypto.subtle.exportKey("raw", kp.publicKey);
  const env = { ...ENV, LICENSE_PUBLIC_KEY_B64: b64urlEncode(rawPub) };

  const token = await mintToken(kp.privateKey, {
    v: 1,
    product: "easypost-desktop",
    order: "ord_123",
    tier: "business",
    email: "a@b.com",
  });
  const lic = await verifyLicense(token, env);
  assert.equal(lic.order, "ord_123");
  assert.equal(lic.tier, "business");
});

test("verifyLicense rejects tampered payload, wrong product and junk", async () => {
  const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const rawPub = await crypto.subtle.exportKey("raw", kp.publicKey);
  const env = { ...ENV, LICENSE_PUBLIC_KEY_B64: b64urlEncode(rawPub) };

  const good = await mintToken(kp.privateKey, { v: 1, product: "easypost-desktop", order: "x" });
  // Flip a char in the payload segment — signature no longer matches.
  const parts = good.split(".");
  parts[1] = parts[1].slice(0, -2) + (parts[1].endsWith("AA") ? "BB" : "AA");
  assert.equal(await verifyLicense(parts.join("."), env), null);

  const wrongProduct = await mintToken(kp.privateKey, { v: 1, product: "something-else", order: "x" });
  assert.equal(await verifyLicense(wrongProduct, env), null);

  assert.equal(await verifyLicense("not-a-token", env), null);
  assert.equal(await verifyLicense("", env), null);
});
