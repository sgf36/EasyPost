/* Google Analytics 4, consent-gated for UK/EU (PECR + UK GDPR).
 *
 * GA sets cookies and is not strictly necessary, so under PECR it needs the
 * visitor's consent before it runs. Nothing GA-related loads until they accept:
 * Google Consent Mode v2 defaults every storage type to "denied", and only an
 * explicit Accept flips analytics_storage to "granted" and injects the tag.
 * Decline sets no cookies and sends nothing to Google. The choice is remembered
 * in localStorage, so the banner appears once.
 *
 * This is loaded on every page. It draws its own banner, so pages need only the
 * one <script> tag — no per-page markup and no CSS dependency.
 */
(function () {
  "use strict";

  var GA_ID = "G-M84XMVB826";
  var KEY = "epd-analytics-consent";

  var choice = null;
  try { choice = localStorage.getItem(KEY); } catch (e) { /* private mode */ }

  window.dataLayer = window.dataLayer || [];
  function gtag() { dataLayer.push(arguments); }

  // Deny everything until the visitor decides.
  gtag("consent", "default", {
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
    analytics_storage: "denied",
  });

  function enableAnalytics() {
    gtag("consent", "update", { analytics_storage: "granted" });
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + GA_ID;
    document.head.appendChild(s);
    gtag("js", new Date());
    gtag("config", GA_ID);
  }

  function remember(value) {
    try { localStorage.setItem(KEY, value); } catch (e) { /* private mode */ }
  }

  if (choice === "granted") { enableAnalytics(); return; }
  if (choice === "denied") { return; }

  // No choice yet — offer one.
  function showBanner() {
    if (document.getElementById("epd-cookie-bar")) return;

    var bar = document.createElement("div");
    bar.id = "epd-cookie-bar";
    bar.setAttribute("role", "dialog");
    bar.setAttribute("aria-label", "Cookie choice");
    bar.style.cssText =
      "position:fixed;left:0;right:0;bottom:0;z-index:9999;background:#191a1c;" +
      "color:#f7f4ed;padding:1rem 1.25rem;font:400 .92rem/1.5 Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;" +
      "display:flex;flex-wrap:wrap;gap:.75rem 1.25rem;align-items:center;" +
      "justify-content:center;box-shadow:0 -2px 14px rgba(0,0,0,.25)";

    var msg = document.createElement("span");
    msg.style.maxWidth = "46rem";
    msg.innerHTML =
      "This site uses Google Analytics to understand how it is used. " +
      "Analytics cookies load only if you accept. See the " +
      '<a href="privacy.html" style="color:#8fd0c4;text-decoration:underline">privacy policy</a>.';

    function button(label, primary) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.style.cssText =
        "font:600 .9rem Inter,sans-serif;padding:9px 20px;border-radius:4px;cursor:pointer;" +
        (primary
          ? "background:#1f5c54;color:#fff;border:1px solid #1f5c54;"
          : "background:transparent;color:#f7f4ed;border:1px solid #6b6b6b;");
      return b;
    }

    var accept = button("Accept", true);
    var decline = button("Decline", false);

    accept.addEventListener("click", function () {
      remember("granted");
      enableAnalytics();
      bar.remove();
    });
    decline.addEventListener("click", function () {
      remember("denied");
      bar.remove();
    });

    var actions = document.createElement("span");
    actions.style.cssText = "display:flex;gap:.6rem;flex-wrap:wrap";
    actions.appendChild(accept);
    actions.appendChild(decline);

    bar.appendChild(msg);
    bar.appendChild(actions);
    document.body.appendChild(bar);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", showBanner);
  } else {
    showBanner();
  }
})();
