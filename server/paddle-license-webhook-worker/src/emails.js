import { strings, AVAILABLE } from "./email-strings.js";

/**
 * Email presentation for the licence and support Worker, which serves two
 * products: Easy-Post Desktop and Wren.
 *
 * Pure, dependency-free builders so they can be unit-tested and previewed
 * outside the Worker. Each `*Email(...)` returns { subject, text, html }:
 * every message ships a branded HTML body AND a plain-text fallback, because a
 * meaningful proportion of clients (and every plain-text reader) never render
 * the HTML.
 *
 * Each design mirrors its own product site: serif (Georgia) headings over a
 * system sans body, on a warm light ground with the product's accent in the
 * header bar. Image-free and table-based on purpose — remote images are blocked
 * by default in most inboxes and hurt deliverability, and a wordmark in an
 * accent bar reads as "designed" without any of that risk.
 *
 * Wren's SITE is dark (#12332F ground, gold on deep green). The email is
 * deliberately NOT: a dark-background HTML email is mangled by several clients,
 * fights their own dark-mode handling, and prints badly. Wren's identity is
 * carried instead by its deep green header bar with the wordmark in gold —
 * which is the logo — over the same light, well-behaved shell Easy-Post uses.
 * The light ground is a derived tone: Wren's palette has no light value.
 */

// Tones shared by both brands: text colours and white, which carry no identity.
const INK = "#191a1c";
const BODY = "#404040";
const MUTED = "#6b6b6b";
const WHITE = "#ffffff";

const BRANDS = {
  "easy-post": {
    green: "#1f5c54",
    greenDark: "#17443e",
    // Colour of the wordmark sitting on the header bar.
    wordmarkColor: "#ffffff",
    ink: INK,
    body: BODY,
    muted: MUTED,
    cream: "#f7f4ed",
    rule: "#e2ded4",
    white: WHITE,
    footerKey: "footer_product",
    wordmark: "Easy&#8209;Post Desktop",
    footerName: "Easy&#8209;Post Desktop",
    site: "https://easy-post.spencerfields.com",
    links: [
      ["link_website", ""],
      ["link_faq", "/faq.html"],
      ["link_privacy", "/privacy.html"],
    ],
    support: "Apps@spencerfields.com",
  },
  software: {
    green: "#1b2a33",
    greenDark: "#16212a",
    wordmarkColor: "#e8dcc8",
    ink: INK,
    body: BODY,
    muted: MUTED,
    cream: "#f7f5f1",
    rule: "#e0dad0",
    white: WHITE,
    footerKey: "footer_business",
    wordmark: "Spencer Fields",
    footerName: "Spencer Fields",
    site: "https://software.spencerfields.com",
    links: [["link_software", "/#software"], ["link_business", "/#business"]],
    support: "Apps@spencerfields.com",
  },
  wren: {
    green: "#1E4B45",      // --raised on the Wren site
    greenDark: "#12332F",  // --ground
    wordmarkColor: "#F2C879", // --gold: green bar + gold wordmark is the logo
    ink: INK,
    body: BODY,
    muted: MUTED,
    cream: "#f5f1e8",
    rule: "#e4ddcd",
    white: WHITE,
    footerKey: "footer_product",
    wordmark: "Wren",
    footerName: "Wren",
    site: "https://wren.spencerfields.com",
    // No FAQ page on the Wren site; the support page carries that content.
    links: [
      ["link_website", ""],
      ["link_support", "/support.html"],
      ["link_privacy", "/privacy.html"],
    ],
    support: "Apps@spencerfields.com",
  },
};

// Every pre-existing builder — the licence email above all — was written
// against `B` and is Easy-Post-only, so `B` stays exactly what it was.
const B = BRANDS["easy-post"];

// Unknown ids fall back to Easy-Post, matching the Worker's own product
// fallback so a typo degrades to the old behaviour rather than a crash.
export function brandFor(productId) {
  return BRANDS[productId] || B;
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

const SANS =
  "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif";
const SERIF = "Georgia,'Times New Roman',serif";
const MONO = "ui-monospace,'SFMono-Regular',Menlo,Consolas,monospace";

// Escaped user/free text -> paragraphs (blank line splits, single newline -> <br>).
export function paraFromText(text) {
  return String(text)
    .trim()
    .split(/\n{2,}/)
    .map(
      (blk) =>
        `<p style="margin:0 0 14px;">${escapeHtml(blk).replace(/\n/g, "<br>")}</p>`
    )
    .join("");
}

// Every helper below takes the brand as a trailing optional argument defaulting
// to Easy-Post, so the licence-email builders keep working untouched.
function heading(text, br = B) {
  return `<h1 style="margin:0 0 16px;font-family:${SERIF};font-size:22px;font-weight:700;color:${br.ink};line-height:1.25;">${escapeHtml(
    text
  )}</h1>`;
}

// A highlighted reference block (support case number, licence key, etc.).
function refBox(label, value, note, br = B) {
  return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px;"><tr>
    <td style="background:${br.cream};border-left:3px solid ${br.green};padding:14px 16px;font-family:${SANS};font-size:13px;color:${br.body};">
      ${escapeHtml(label)}<br>
      <span style="font-family:${MONO};font-size:16px;font-weight:700;color:${br.ink};letter-spacing:.5px;">${escapeHtml(
    value
  )}</span>
      ${note ? `<br><span style="color:${br.muted};font-size:12px;">${escapeHtml(note)}</span>` : ""}
    </td></tr></table>`;
}

// RTL scripts. Only the translated block is flipped, never the whole document:
// the English half sits beside it and must stay left-to-right.
const RTL = new Set(["ar", "he", "fa", "ur"]);

/**
 * A small coloured chip naming the language of the block beneath it.
 *
 * A bilingual message is confusing without one -- the reader cannot tell at a
 * glance which half is theirs, and the second half looks like a mistake.
 */
export function langTag(code, br = B) {
  return (
    `<span style="display:inline-block;font-family:${SANS};font-size:11px;` +
    `font-weight:700;letter-spacing:.09em;background:${br.green};color:${br.white};` +
    `border-radius:4px;padding:3px 8px;margin:0 0 12px;">` +
    escapeHtml(String(code).toUpperCase().replace("_", "-")) +
    "</span>"
  );
}

/**
 * Where a footer link should point for this reader.
 *
 * A translated email whose footer links land on English pages undoes the point
 * of translating it, so links go to the reader's own language when that
 * language has published pages, and to English when it does not.
 */
function siteHref(br, path, lang) {
  if (lang === "en" || !AVAILABLE.has(lang)) return br.site + (path || "/");
  // An empty path means the home page. The file is named explicitly rather
  // than trusting a bare /de/ to hit a directory index.
  if (!path) return `${br.site}/${lang}/index.html`;
  // "/#software" is an anchor on the home page, not a page of its own.
  if (path.startsWith("/#")) return `${br.site}/${lang}/index.html${path.slice(1)}`;
  return `${br.site}/${lang}${path}`;
}

// The branded outer shell. `preheader` is the hidden inbox-preview snippet.
export function emailShell({ title, preheader, bodyHtml, brand = B, lang = "en" }) {
  const br = brand;
  const t = strings(lang);
  const nav = br.links
    .map(
      ([key, path]) =>
        `<a href="${siteHref(br, path, lang)}" style="color:${br.green};text-decoration:none;">${escapeHtml(
          t[key] || key
        )}</a>`
    )
    .join(" &nbsp;·&nbsp; ");
  const footerText = escapeHtml(t[br.footerKey] || t.footer_business);
  return `<!DOCTYPE html>
<html lang="${escapeHtml(lang.replace("_", "-"))}"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>${escapeHtml(title)}</title>
</head>
<body style="margin:0;padding:0;background:${br.cream};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:${br.cream};">${escapeHtml(
    preheader || ""
  )}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:${br.cream};">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:${br.white};border:1px solid ${br.rule};border-radius:10px;overflow:hidden;">
    <tr><td style="background:${br.green};padding:20px 28px;">
      <span style="font-family:${SERIF};font-size:20px;font-weight:700;color:${br.wordmarkColor};letter-spacing:.3px;">${br.wordmark}</span>
    </td></tr>
    <tr><td style="padding:28px;font-family:${SANS};font-size:15px;line-height:1.6;color:${br.body};">
      ${bodyHtml}
    </td></tr>
    <tr><td style="padding:18px 28px;border-top:1px solid ${br.rule};font-family:${SANS};font-size:12px;line-height:1.7;color:${br.muted};">
      ${br.footerName} — ${footerText}<br>
      ${nav} &nbsp;·&nbsp;
      <a href="mailto:${br.support}" style="color:${br.green};text-decoration:none;">${br.support}</a>
    </td></tr>
  </table>
</td></tr></table>
</body></html>`;
}

function signoff(line, br = B) {
  return `<p style="margin:18px 0 0;color:${br.body};">— ${escapeHtml(line)}</p>`;
}

// EPD-YYMMDD-XXXX, with a 4-char non-ambiguous suffix (no I/O/0/1).
export function newCaseId(now = new Date()) {
  const yy = String(now.getUTCFullYear()).slice(2);
  const mm = String(now.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(now.getUTCDate()).padStart(2, "0");
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = new Uint8Array(4);
  (globalThis.crypto || crypto).getRandomValues(bytes);
  let suffix = "";
  for (let i = 0; i < 4; i++) suffix += alphabet[bytes[i] % alphabet.length];
  return `EPD-${yy}${mm}${dd}-${suffix}`;
}

// ---- Licence key email -----------------------------------------------------
export function licenseEmail({ licenseKey, seats = 3, annual = false }) {
  const allowance =
    seats === 0
      ? "This key has no computer limit."
      : `This key covers up to ${seats} computer${seats === 1 ? "" : "s"}.`;
  const billing = annual
    ? "This is an annual subscription. Keep this key — it stays the same every year and renewals apply automatically. You will not be sent a new one."
    : "This is a one-time purchase. The key does not expire.";

  const text =
    "Thank you for buying Easy-Post Desktop.\n\n" +
    "Your licence key:\n\n" +
    `${licenseKey}\n\n` +
    `${allowance}\n${billing}\n\n` +
    "STEP 1 — Download the application\n\n" +
    "  https://easy-post.spencerfields.com/download.html\n\n" +
    "  Windows and macOS builds are both there. Windows shows a blue " +
    '"Windows protected your PC" screen the first time, because the download is ' +
    'not yet code-signed — click "More info", then "Run anyway". The download ' +
    "page explains this and lists the checksums if you would like to verify the " +
    "file first.\n\n" +
    "STEP 2 — Activate\n\n" +
    "  Open Easy-Post Desktop, paste the key above on the activation screen, and " +
    "click Activate. Keep this email for your records.\n\n" +
    "STEP 3 — Connect your EasyPost account\n\n" +
    "  The application ships no postage of its own: it drives your own EasyPost " +
    "account. Paste your EasyPost API key when asked. A test-mode key lets you " +
    "explore everything without buying real labels.\n\n" +
    "Changing computers? Open Settings and release the old one first, or release " +
    "it from the new computer when prompted.\n\n" +
    `Questions? ${B.support}\n`;

  const step = (n, title, bodyHtml) =>
    `<tr>
      <td width="34" valign="top" style="font-family:${SERIF};font-size:18px;font-weight:700;color:${B.green};padding:0 0 14px;">${n}</td>
      <td valign="top" style="padding:0 0 14px;">
        <div style="font-weight:700;color:${B.ink};margin:0 0 3px;">${escapeHtml(title)}</div>
        <div style="color:${B.body};">${bodyHtml}</div>
      </td>
    </tr>`;

  const bodyHtml =
    heading("Your licence key") +
    `<p style="margin:0 0 16px;">Thank you for buying Easy&#8209;Post Desktop.</p>` +
    `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;"><tr>
      <td style="background:${B.cream};border:1px dashed ${B.green};border-radius:8px;padding:18px;text-align:center;">
        <div style="font-family:${SANS};font-size:12px;color:${B.muted};margin:0 0 6px;">YOUR LICENCE KEY</div>
        <div style="font-family:${MONO};font-size:16px;font-weight:700;color:${B.ink};word-break:break-all;line-height:1.5;">${escapeHtml(
      licenseKey
    )}</div>
      </td></tr></table>` +
    `<p style="margin:0 0 20px;color:${B.body};">${escapeHtml(allowance)} ${escapeHtml(
      billing
    )}</p>` +
    `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 4px;">
      ${step(
        1,
        "Download the application",
        `<a href="${B.site}/download.html" style="color:${B.green};font-weight:600;text-decoration:none;">easy-post.spencerfields.com/download.html</a><br>Windows and macOS are both there. Windows shows a “Windows protected your PC” screen the first time (the download is not yet code-signed) — click <strong>More info</strong>, then <strong>Run anyway</strong>. Checksums are on that page.`
      )}
      ${step(
        2,
        "Activate",
        `Open Easy&#8209;Post Desktop, paste the key above on the activation screen, and click Activate. Keep this email for your records.`
      )}
      ${step(
        3,
        "Connect your EasyPost account",
        `The app drives your own EasyPost account and ships no postage of its own. Paste your EasyPost API key when asked — a test-mode key lets you explore everything with no real charges.`
      )}
    </table>` +
    `<p style="margin:14px 0 0;color:${B.muted};font-size:13px;">Changing computers? Open Settings and release the old one first, or release it from the new computer when prompted.</p>`;

  return {
    subject: "Your Easy-Post Desktop licence key",
    text,
    html: emailShell({
      title: "Your Easy-Post Desktop licence key",
      preheader: "Your licence key, plus how to download and activate.",
      bodyHtml,
    }),
  };
}

// ---- Contact form: reply to the customer -----------------------------------

/**
 * One language's worth of the message: chip, heading, greeting, body,
 * reference and sign-off, all in that language.
 *
 * Everything except the customer's name and the case reference is translated.
 * A reply whose answer is in Spanish but whose greeting, reference label and
 * sign-off are in English reads as half-finished, which is what prompted this.
 */
function section({ code, name, body, caseId, br, isLast }) {
  const t = strings(code);
  const rtl = RTL.has(code) ? ' dir="rtl"' : "";
  return (
    `<div${rtl}>` +
    langTag(code, br) +
    heading(t.received, br) +
    `<p style="margin:0 0 16px;">${escapeHtml(t.hello)} ${escapeHtml(name)},</p>` +
    paraFromText(body) +
    refBox(t.ref_label, caseId, t.ref_note, br) +
    signoff(t.signoff, br) +
    (isLast
      ? ""
      : `<p style="margin:14px 0 0;color:${br.muted};font-size:12px;">${escapeHtml(
          t.in_english
        )}</p>`) +
    "</div>"
  );
}

/**
 * The acknowledgement or first answer.
 *
 * When the enquiry arrived in another language the message is built twice:
 * their language first, English beneath, each block labelled with a coloured
 * chip so it is obvious at a glance which half is which. The English is not a
 * courtesy -- it is the only text guaranteed to say what was meant.
 */
export function contactCustomerEmail({
  name,
  topic,
  caseId,
  english = null,
  translated = null,
  lang = "en",
  productName = "Easy-Post Desktop",
  productId = "easy-post",
  isAutoReply = false,
}) {
  const br = brandFor(productId);
  const en = strings("en");
  const englishBody = english || en.thanks;
  // The standard acknowledgement is in the table already, so translating it
  // costs nothing. Only a real answer needs the model.
  const otherBody =
    translated || (lang !== "en" && !english ? strings(lang).thanks : null);
  const NL = String.fromCharCode(10);
  const RULE = "-".repeat(41);

  const plain = (code, body) => {
    const t = strings(code);
    return (
      t.hello + " " + name + "," + NL + NL +
      body + NL + NL +
      t.ref_label + ": " + caseId + NL +
      t.ref_note + NL + NL +
      "— " + t.signoff
    );
  };

  const blocks = [];
  const textParts = [];

  if (otherBody && lang !== "en") {
    blocks.push(section({ code: lang, name, body: otherBody, caseId, br, isLast: false }));
    textParts.push(plain(lang, otherBody) + NL + NL + strings(lang).in_english);
    blocks.push(`<div style="border-top:1px solid ${br.rule};margin:22px 0;"></div>`);
  }

  blocks.push(section({ code: "en", name, body: englishBody, caseId, br, isLast: true }));
  textParts.push(plain("en", englishBody));

  if (isAutoReply) {
    blocks.push(
      `<p style="margin:18px 0 0;padding:12px 16px;background:${br.cream};` +
        `border-radius:6px;color:${br.muted};font-size:13px;">${escapeHtml(en.auto_note)}</p>`
    );
    textParts.push(en.auto_note);
  }

  return {
    subject: `Re: ${topic} — ${productName} [${caseId}]`,
    text: textParts.join(NL + NL + RULE + NL + NL),
    html: emailShell({
      title: strings(lang).received,
      preheader: strings(lang).ref_label + ": " + caseId,
      bodyHtml: blocks.join(""),
      brand: br,
      lang,
    }),
  };
}

// ---- Contact form: forward to the owner (Spencer) --------------------------
export function contactOwnerEmail({
  name,
  email,
  topic,
  ip,
  caseId,
  triageNote,
  message,
  autoReplied,
  spam = false,
  productName = "Easy-Post Desktop",
  productId = "easy-post",
}) {
  const br = brandFor(productId);
  const text =
    `A message was sent from the ${productName} contact form.\n\n` +
    `Case:  ${caseId}\nName:  ${name}\nEmail: ${email}\nTopic: ${topic}\nIP:    ${ip}\n\n` +
    `Triage: ${triageNote}\n\n` +
    "-----------------------------------------\n\n" +
    message +
    "\n";
  const row = (k, v, mono) =>
    `<tr>
      <td style="padding:3px 12px 3px 0;color:${br.muted};font-size:13px;white-space:nowrap;vertical-align:top;">${escapeHtml(
      k
    )}</td>
      <td style="padding:3px 0;color:${br.ink};font-size:13px;${
      mono ? `font-family:${MONO};` : ""
    }">${escapeHtml(v)}</td>
    </tr>`;
  const bodyHtml =
    heading(`New ${productName} enquiry`, br) +
    refBox("Case", caseId, spam ? "Likely spam — no acknowledgement sent." : autoReplied ? "Customer already received an automated first reply." : "Awaiting your reply.", br) +
    `<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 16px;">
      ${row("Name", name)}
      ${row("Email", email, true)}
      ${row("Topic", topic)}
      ${row("IP", ip, true)}
    </table>` +
    `<p style="margin:0 0 8px;color:${br.muted};font-size:13px;"><strong>Triage:</strong> ${escapeHtml(
      triageNote
    )}</p>` +
    `<div style="border-top:1px solid ${br.rule};margin:16px 0;"></div>` +
    `<div style="color:${br.body};">${paraFromText(message)}</div>`;
  return {
    // Product goes in the subject because two products now land in the same
    // inbox and the case reference does not distinguish them.
    subject:
      (spam ? "[spam?] " : autoReplied ? "[auto-replied] " : "[needs reply] ") +
      `[${productName}] [${caseId}] ${topic} — ${name}`,
    text,
    html: emailShell({
      title: `New ${productName} enquiry`,
      preheader: `${caseId} · ${productName} · ${topic} · ${name}`,
      bodyHtml,
      brand: br,
    }),
  };
}
