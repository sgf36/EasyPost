/**
 * Translating support relay.
 *
 * INBOUND: a customer writes in their own language. The message is detected and
 * translated to English for the owner, who is forwarded the English with the
 * original beneath it. Any automatic first reply goes out in the language the
 * customer used, not English.
 *
 * OUTBOUND: the owner hits Reply in Outlook. The reply does not go straight to
 * the customer -- the forward carries a per-thread relay address as its
 * Reply-To, so the reply comes back here, is translated, and is sent on with
 * the English original beneath it.
 *
 * Bilingual on purpose. A machine translation of a support answer can be wrong
 * in ways neither end can see; shipping the English underneath means anyone
 * with some English can check what was meant, and it is honest about what the
 * message is.
 *
 * Everything here fails towards "a human still gets the message": a detection
 * failure forwards the original untranslated rather than nothing at all.
 */
import { createHmac, timingSafeEqual } from "node:crypto";

const AI_MODEL = "claude-haiku-4-5-20251001";

// reply+EPD-260818-K7QN.9f3c2a71@easy-post.spencerfields.com
const REPLY_RE = /^reply\+([A-Z0-9-]+)\.([0-9a-f]{8})$/i;

/** Deterministic, unguessable suffix binding a reply address to one case. */
export function replyMac(caseId, secret) {
  return createHmac("sha256", secret).update(caseId).digest("hex").slice(0, 8);
}

export function replyAddress(caseId, domain, secret) {
  return "reply+" + caseId + "." + replyMac(caseId, secret) + "@" + domain;
}

/**
 * Pull the case id out of a relay address, rejecting anything whose MAC fails.
 *
 * The MAC is the whole security model for this route. A bare case id is
 * guessable -- EPD-YYMMDD-XXXX is a small enough space to search -- and a
 * guessed address would let a stranger send mail to a customer that appears to
 * come from the owner.
 */
export function parseReplyAddress(address, secret) {
  const local = String(address || "").split("@")[0];
  const m = REPLY_RE.exec(local);
  if (!m) return null;
  const caseId = m[1].toUpperCase();
  const expected = replyMac(caseId, secret);
  const a = Buffer.from(m[2].toLowerCase(), "utf8");
  const b = Buffer.from(expected, "utf8");
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;
  return caseId;
}

/** Svix-format webhook signature, as used by Resend. */
export function verifyWebhook(secret, headers, rawBody) {
  const id = headers.get("svix-id");
  const ts = headers.get("svix-timestamp");
  const sigHeader = headers.get("svix-signature");
  if (!id || !ts || !sigHeader || !secret) return false;

  // Five-minute window: a captured request must not be replayable tomorrow.
  const age = Math.abs(Date.now() / 1000 - Number(ts));
  if (!Number.isFinite(age) || age > 300) return false;

  // The secret is base64 AFTER its whsec_ prefix. Using the whole string, or
  // skipping the decode, yields a signature that never matches and looks
  // exactly like a wrong secret.
  const key = Buffer.from(secret.replace(/^whsec_/, ""), "base64");
  const expected = createHmac("sha256", key)
    .update(id + "." + ts + "." + rawBody)
    .digest("base64");

  // The header carries a space-separated list of versioned signatures.
  return sigHeader.split(" ").some(function (part) {
    const value = part.includes(",") ? part.split(",")[1] : part;
    const a = Buffer.from(value, "utf8");
    const b = Buffer.from(expected, "utf8");
    return a.length === b.length && timingSafeEqual(a, b);
  });
}

async function claude(env, system, user, maxTokens) {
  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: AI_MODEL,
      max_tokens: maxTokens || 3000,
      system: system,
      messages: [{ role: "user", content: user }],
    }),
  });
  if (!r.ok) throw new Error("anthropic " + r.status);
  const data = await r.json();
  return (data.content && data.content[0] && data.content[0].text) || "";
}

function parseJson(raw) {
  return JSON.parse(
    raw.trim().replace(/^```json\s*/i, "").replace(/```$/, "").trim()
  );
}

/**
 * Identify the language of an inbound message and render it in English.
 *
 * On any failure it returns the original marked as English. A support message
 * that arrives untranslated is a nuisance; one that does not arrive is a lost
 * customer.
 */
export async function toEnglish(env, message) {
  const fallback = {
    lang: "en",
    langName: "English",
    english: message,
    translated: false,
  };
  if (!env.ANTHROPIC_API_KEY || !message) return fallback;
  try {
    const raw = await claude(
      env,
      "Identify the language of the message and translate it into British " +
        "English. Reply with STRICT JSON only, no prose and no code fences: " +
        '{"lang": "<ISO 639-1 code>", "name": "<language name in English>", ' +
        '"english": "<the translation, or the original if already English>"}. ' +
        "Translate faithfully, including any confusion or frustration; do not " +
        "soften it, summarise it, or answer it.",
      message
    );
    const parsed = parseJson(raw);
    if (!parsed.lang || !parsed.english) return fallback;
    const lang = String(parsed.lang).toLowerCase().slice(0, 8);
    return {
      lang: lang,
      langName: String(parsed.name || lang),
      english: String(parsed.english),
      translated: lang !== "en",
    };
  } catch (e) {
    return fallback;
  }
}

/** Render English text into the customer language. Null if it cannot. */
export async function fromEnglish(env, text, lang, langName) {
  if (!env.ANTHROPIC_API_KEY || !text || !lang || lang === "en") return null;
  try {
    const out = await claude(
      env,
      "Translate the support reply below from British English into " +
        (langName || lang) +
        " (" +
        lang +
        "). Reply with the translation ONLY -- no preamble, no notes, no " +
        "quotation marks around it. Keep the tone warm, plain and " +
        "professional, and address the reader with the polite form a company " +
        "uses when writing to a customer. Do not translate product names, " +
        "email addresses, URLs, licence keys or case references; reproduce " +
        "those exactly as given.",
      text
    );
    return out.trim() || null;
  } catch (e) {
    return null;
  }
}

/**
 * Keep only what the owner just typed.
 *
 * Outlook appends the entire prior thread and a signature. Translating that
 * sends the customer their own message back, in translation, over the owner
 * name. Cutting at the first quote marker and at common signature openers
 * handles the ordinary cases; a model is deliberately not asked to judge the
 * boundary, because when it gets that wrong it fails silently.
 */
export function stripQuoted(text) {
  if (!text) return "";
  const lines = String(text).replace(/\r\n/g, "\n").split("\n");
  const cut = [
    /^\s*>/,
    /^\s*-{2,}\s*Original Message\s*-{2,}/i,
    /^\s*_{5,}\s*$/,
    /^\s*From:\s.+/i,
    /^\s*On .+ wrote:\s*$/i,
    /^\s*Sent from my /i,
    /^\s*--\s*$/,
  ];
  const out = [];
  for (const line of lines) {
    let stop = false;
    for (const re of cut) {
      if (re.test(line)) {
        stop = true;
        break;
      }
    }
    if (stop) break;
    out.push(line);
  }
  return out.join("\n").trim();
}

/**
 * Is this something we must not relay?
 *
 * Without these, one auto-responder on the customer side is enough to start a
 * loop: we send, it replies, the relay receives, we send again.
 */
export function isAutomated(headers) {
  const h = headers || {};
  const get = function (k) {
    return String(h[k] || h[k.toLowerCase()] || "");
  };
  if (get("auto-submitted") && !/^no$/i.test(get("auto-submitted"))) return true;
  if (/^(bulk|list|junk|auto_reply)$/i.test(get("precedence"))) return true;
  if (get("x-autoreply") || get("x-autorespond")) return true;
  if (get("list-id") || get("list-unsubscribe")) return true;
  return false;
}

/** Fetch a received email in full. The webhook carries metadata only. */
export async function fetchReceived(apiKey, id) {
  const r = await fetch("https://api.resend.com/emails/receiving/" + id, {
    headers: { Authorization: "Bearer " + apiKey },
  });
  if (!r.ok) throw new Error("receiving " + r.status);
  return r.json();
}

/**
 * What the customer receives: their language first, English beneath.
 *
 * The English is not a footnote. It is the only part guaranteed to say what
 * the owner meant, and it is what makes an imperfect translation safe to send.
 */
export function bilingual(opts) {
  const caseId = opts.caseId;
  if (!opts.translated) {
    return {
      text: opts.english + "\n\nYour support reference: " + caseId,
      note: "sent in English (translation unavailable)",
    };
  }
  const rule = "-----------------------------------------";
  return {
    text:
      opts.translated +
      "\n\n" +
      rule +
      "\n" +
      "The original English of this reply follows, in case anything above is " +
      "unclear.\n\n" +
      opts.english +
      "\n\nYour support reference: " +
      caseId,
    note: "translated into " + opts.langName + ", English included beneath",
  };
}
