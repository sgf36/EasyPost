/* Live status for the Summer 2026 launch discount.
 *
 * Reads GET /promo on the licence Worker and either annotates the banner with
 * how many of the 26 have been claimed, or removes the banner outright once the
 * offer is exhausted or expired. Progressive enhancement: if the request fails
 * for any reason the static banner is left exactly as the page shipped it, so a
 * blip never turns the offer into a blank space.
 *
 * The number is advisory. Paddle enforces the 26-redemption limit at checkout
 * regardless of what is shown here, so a briefly stale count can never let a
 * 27th customer through.
 */
(function () {
  "use strict";

  var ENDPOINT = "https://easypost-license-webhook.sgf36.workers.dev/promo";
  var banners = document.querySelectorAll(".promo");
  if (!banners.length) return;

  fetch(ENDPOINT, { method: "GET" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d) return; // soft failure — leave the static banner untouched

      if (!d.active || d.remaining <= 0) {
        // Sold out or past its date: take the whole banner down.
        Array.prototype.forEach.call(banners, function (b) { b.remove(); });
        return;
      }

      // Still running: say how many have gone.
      Array.prototype.forEach.call(banners, function (b) {
        if (b.querySelector(".promo-count")) return;
        var tag = document.createElement("span");
        tag.className = "promo-count";
        tag.style.whiteSpace = "nowrap";
        tag.style.fontWeight = "600";
        tag.style.color = "#17443e";
        tag.textContent = d.used + " of " + d.limit + " claimed";
        b.appendChild(tag);
      });
    })
    .catch(function () { /* leave the static banner */ });
})();
