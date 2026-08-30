/* The app banner that Safari will not draw for you.
 *
 * On iOS Safari, Apple draws its own Smart App Banner from the
 * <meta name="apple-itunes-app"> tag in the head, and it is better than
 * anything reproducible here: it knows whether the app is installed and says
 * "Open" instead of "View". So on iOS Safari this script does nothing at all
 * and gets out of the way.
 *
 * Every other visitor gets nothing from Apple, which is most of them — this is
 * a desktop shipping application and the desktop is where the product lives.
 * So the banner below is drawn for them instead, pointing at whichever store
 * can actually serve the machine they are on:
 *
 *   macOS          the Mac App Store
 *   Windows        the Microsoft Store
 *   iOS, no Safari the App Store, for the iPhone companion
 *   anything else  nothing, because nothing is published for it
 *
 * That last case is the one worth stating plainly. The Android companion is in
 * closed testing and has no public listing, so an Android visitor is shown no
 * banner rather than a link that would take them to a page they cannot install
 * from.
 *
 * Both variants ship in the HTML rather than being written here, so their text
 * goes through the translation pipeline like everything else. This file only
 * decides which one is revealed.
 */
(function () {
  "use strict";

  /* Which store, if any, this device can install from.
   *
   * Split out and exposed as `window.easypostAppBannerTarget` so the decision
   * can be exercised against a table of real user-agent strings rather than
   * only against whichever machine happens to be previewing the page. Every
   * branch below has been wrong at least once and none of them fails loudly.
   *
   * Rule out before matching, and read the user-agent string ahead of
   * navigator.platform: `platform` is deprecated, browsers lie about it, and a
   * device emulator rewrites the user agent while leaving it saying "Win32" --
   * which is exactly how an early draft offered the Microsoft Store to an
   * Android phone.
   */
  function pickTarget(ua, platform, maxTouchPoints) {
    ua = ua || "";
    platform = platform || "";

    // Nothing is published for either, and the Android companion is in closed
    // testing with no public listing. A link would reach a page they cannot
    // install from, which is worse than no banner.
    if (/Android|CrOS/.test(ua)) return null;

    // iPadOS reports itself as MacIntel and is distinguished only by the touch
    // points. Without this an iPad is offered the Mac App Store.
    var isIOS = /iPhone|iPod|iPad/.test(ua) ||
                (platform === "MacIntel" && (maxTouchPoints || 0) > 1);

    // Chrome, Firefox and Edge on iOS all carry "Safari" in the user agent and
    // none of them draws a Smart App Banner, so they are excluded by name.
    var isSafari = /Safari/.test(ua) && !/Chrome|Chromium|CriOS|FxiOS|EdgiOS/.test(ua);

    if (isIOS) return isSafari ? null : "ios";  // Safari's own banner is better
    if (/Macintosh|Mac OS X/.test(ua) || /^Mac/.test(platform)) return "mac";
    if (/Windows/.test(ua) || /^Win/.test(platform)) return "windows";
    return null;                                 // Linux, or anything unknown
  }

  window.easypostAppBannerTarget = pickTarget;

  var banner = document.getElementById("app-banner");
  if (!banner) return;

  var KEY = "easypost.appbanner.dismissed";

  // A dismissal is a preference, not state worth protecting. Private windows
  // and browsers with site data switched off throw on the accessor itself, so
  // every read and write is wrapped and a failure simply shows the banner.
  function dismissed() {
    try { return window.localStorage.getItem(KEY) === "1"; } catch (e) { return false; }
  }
  function remember() {
    try { window.localStorage.setItem(KEY, "1"); } catch (e) { /* nothing to do */ }
  }

  if (dismissed()) return;

  var target = pickTarget(navigator.userAgent, navigator.platform,
                          navigator.maxTouchPoints);
  if (!target) return;

  // Two elements per target — the wording and the button. Both ship hidden, so
  // a device with no store never briefly shows another device's copy.
  var parts = banner.querySelectorAll('[data-store="' + target + '"]');
  if (!parts.length) return;
  Array.prototype.forEach.call(parts, function (el) { el.hidden = false; });

  var close = banner.querySelector(".app-banner-close");
  if (close) {
    close.addEventListener("click", function () {
      banner.hidden = true;
      remember();
    });
  }

  banner.hidden = false;
})();
