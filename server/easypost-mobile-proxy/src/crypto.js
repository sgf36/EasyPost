// Crypto primitives for the mobile proxy. All via Web Crypto (native in
// Workers): AES-GCM for the key ciphertext, Ed25519 for licence verification.
// There is deliberately no server-held master key — the KEK lives on the phone.

export function b64urlEncode(bytes) {
  let bin = "";
  const arr = new Uint8Array(bytes);
  for (let i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(str) {
  const s = str.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((str.length + 3) % 4);
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function randomToken(nBytes = 32) {
  return b64urlEncode(crypto.getRandomValues(new Uint8Array(nBytes)));
}

// Encrypt `plaintext` (string) under a fresh random KEK. Returns the pieces the
// caller stores as ciphertext + iv, plus the KEK to hand to the phone.
export async function encryptWithNewKek(plaintext) {
  const kekBytes = crypto.getRandomValues(new Uint8Array(32));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await crypto.subtle.importKey("raw", kekBytes, { name: "AES-GCM" }, false, ["encrypt"]);
  const ct = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    new TextEncoder().encode(plaintext),
  );
  return {
    kek: b64urlEncode(kekBytes),
    ciphertext: b64urlEncode(ct),
    iv: b64urlEncode(iv),
  };
}

// Decrypt a stored ciphertext using the KEK the phone presented. Throws on any
// tampering (AES-GCM auth) or a wrong KEK.
export async function decryptWithKek(kekB64, ciphertextB64, ivB64) {
  const key = await crypto.subtle.importKey("raw", b64urlDecode(kekB64), { name: "AES-GCM" }, false, ["decrypt"]);
  const pt = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: b64urlDecode(ivB64) },
    key,
    b64urlDecode(ciphertextB64),
  );
  return new TextDecoder().decode(pt);
}

// Verify an EPD1 licence token exactly as the desktop app does: split on ".",
// Ed25519-verify the payload bytes, then sanity-check product + version.
// Returns { order, tier, email } on success or null on any failure.
export async function verifyLicense(token, env) {
  if (!token || typeof token !== "string") return null;
  const parts = token.trim().split(".");
  if (parts.length !== 3) return null;
  const [tag, payloadB64, sigB64] = parts;
  if (tag !== env.LICENSE_FORMAT_TAG) return null;

  let payloadBytes, sigBytes;
  try {
    payloadBytes = b64urlDecode(payloadB64);
    sigBytes = b64urlDecode(sigB64);
  } catch {
    return null;
  }

  let ok = false;
  try {
    const pub = await crypto.subtle.importKey(
      "raw",
      b64urlDecode(base64ToUrl(env.LICENSE_PUBLIC_KEY_B64)),
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    ok = await crypto.subtle.verify({ name: "Ed25519" }, pub, sigBytes, payloadBytes);
  } catch {
    return null;
  }
  if (!ok) return null;

  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(payloadBytes));
  } catch {
    return null;
  }
  if (![1, 2].includes(payload.v) || payload.product !== env.LICENSE_PRODUCT_ID) return null;

  return {
    order: String(payload.order || ""),
    tier: String(payload.tier || "personal"),
    email: String(payload.email || ""),
  };
}

// The app stores the public key as standard base64; our b64urlDecode wants
// url-safe. Normalise so either encoding works.
function base64ToUrl(s) {
  return s.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
