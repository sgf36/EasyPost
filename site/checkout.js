/* Paddle overlay checkout for the pricing page.
 *
 * Progressive enhancement by design. Every Buy button is an <a> whose href is
 * a mailto, so the page works with no JavaScript, with JavaScript blocked, or
 * with Paddle.js unreachable. This file only ever *upgrades* that click into
 * an overlay checkout — it never replaces the fallback, so there is no state
 * in which a buyer clicks Buy and nothing happens.
 *
 * To go live: paste the Paddle client-side token below. It is a public,
 * embeddable value (it begins "live_") and is safe in source control — it is
 * NOT the API key, which must never appear in client-side code. Get it from
 * Paddle > Developer tools > Authentication > Client-side tokens.
 *
 * Until a real token is set, the constant stays at PLACEHOLDER and every
 * button silently keeps its mailto behaviour.
 */
(function () {
  "use strict";

  var CLIENT_TOKEN = "PLACEHOLDER";

  // Paddle rejects an unrecognised token at Initialize() and the overlay never
  // opens, so refuse to touch the buttons unless a real one is present.
  if (!CLIENT_TOKEN || CLIENT_TOKEN === "PLACEHOLDER") return;

  var buttons = document.querySelectorAll("[data-paddle-price]");
  if (!buttons.length) return;

  var script = document.createElement("script");
  script.src = "https://cdn.paddle.com/paddle/v2/paddle.js";

  // A failed CDN load leaves the mailto intact, which is the whole point.
  script.onerror = function () {
    /* fallback stays */
  };

  script.onload = function () {
    if (!window.Paddle) return;
    window.Paddle.Initialize({ token: CLIENT_TOKEN });

    Array.prototype.forEach.call(buttons, function (el) {
      el.addEventListener("click", function (event) {
        var priceId = el.getAttribute("data-paddle-price");
        if (!priceId) return;

        // Only now do we take over the click. Anything thrown below restores
        // the mailto by simply not having called preventDefault yet.
        event.preventDefault();

        window.Paddle.Checkout.open({
          items: [{ priceId: priceId, quantity: 1 }],
          settings: {
            displayMode: "overlay",
            theme: "light",
            // The licence key is delivered by email from the webhook worker,
            // so the buyer is told to expect that rather than left guessing.
            successUrl: window.location.origin + "/thank-you.html"
          }
        });
      });
    });
  };

  document.head.appendChild(script);
})();
