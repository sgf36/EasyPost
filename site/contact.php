<?php
/**
 * Contact form handler for easy-post.spencerfields.com.
 *
 * Deliberately dependency-free: this is shared cPanel hosting, so PHP's own
 * mail() and a handful of cheap server-side checks beat pulling in a library
 * or a third-party form service that would see every customer message.
 *
 * Spam defences, cheapest first:
 *   1. Honeypot   -- a field people never see and bots reliably fill in.
 *   2. Timing     -- the form is stamped by script on load; a post with no
 *                    stamp never rendered the page, and one submitted within
 *                    a few seconds was not typed by a person.
 *   3. Validation -- required fields, real email, hard length caps.
 *   4. Injection  -- newlines are stripped from anything that reaches a mail
 *                    header. This is the one that actually matters: without
 *                    it the form becomes an open relay for spam.
 *   5. Link flood -- messages stuffed with URLs are the classic payload.
 *   6. Rate limit -- per-IP, to blunt anything that gets past the above.
 *
 * There is no CAPTCHA. The layers above stop commodity bots without making a
 * paying customer prove their humanity; if targeted spam ever gets through,
 * that is the point to add one.
 */

declare(strict_types=1);

const MAIL_TO            = 'AppleApps@spencerfields.com';
const MAIL_FROM          = 'noreply@easy-post.spencerfields.com';
const MIN_FILL_SECONDS   = 3;
const MAX_FILL_AGE       = 86400;   // a stamp older than a day is stale
const MAX_LINKS          = 3;
const RATE_WINDOW        = 3600;    // per IP, per hour
const RATE_MAX           = 5;

/** Anything reaching a mail header must not be able to introduce one. */
function header_safe(string $value): string
{
    return trim(str_replace(["\r", "\n", "%0a", "%0d", "\0"], ' ', $value));
}

function client_ip(): string
{
    $ip = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
    return filter_var($ip, FILTER_VALIDATE_IP) ? $ip : 'unknown';
}

/**
 * Simple file-backed throttle. Best effort by design: on shared hosting the
 * temp directory is per-account, and a failure to read or write it must never
 * block a genuine message.
 */
function rate_limited(string $ip): bool
{
    $path = sys_get_temp_dir() . '/epd-contact-' . hash('sha256', $ip) . '.txt';
    $now = time();
    $stamps = [];

    if (is_readable($path)) {
        $raw = @file_get_contents($path);
        if ($raw !== false) {
            foreach (explode(',', $raw) as $s) {
                $s = (int) $s;
                if ($s > $now - RATE_WINDOW) {
                    $stamps[] = $s;
                }
            }
        }
    }

    if (count($stamps) >= RATE_MAX) {
        return true;
    }

    $stamps[] = $now;
    @file_put_contents($path, implode(',', $stamps), LOCK_EX);
    return false;
}

function render(string $heading, string $body, bool $ok): void
{
    http_response_code($ok ? 200 : 400);
    $cls = $ok ? 'ok' : 'bad';
    $heading = htmlspecialchars($heading, ENT_QUOTES, 'UTF-8');
    $body = htmlspecialchars($body, ENT_QUOTES, 'UTF-8');
    echo <<<HTML
<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{$heading} — Easy-Post Desktop</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,600;1,600&family=Inter:wght@400;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="site-head">
  <div class="wrap">
    <a class="brand" href="index.html">Easy-Post Desktop</a>
    <nav class="site-nav">
      <a href="index.html">Overview</a>
      <a href="pricing.html">Pricing</a>
      <a href="terms.html">Terms</a>
      <a href="privacy.html">Privacy</a>
      <a href="refunds.html">Refunds</a>
      <a href="index.html#support">Support</a>
    </nav>
  </div>
</header>
<section class="legal">
  <div class="wrap prose">
    <h1>{$heading}</h1>
    <div class="form-status {$cls}">{$body}</div>
    <p><a href="index.html#support">Back to support</a></p>
  </div>
</section>
</body>
</html>
HTML;
    exit;
}

// ---------------------------------------------------------------- handling

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'POST') {
    header('Location: index.html#support', true, 302);
    exit;
}

// 1. Honeypot.
if (trim((string) ($_POST['website'] ?? '')) !== '') {
    // Answer as though it worked. A bot told it failed simply retries.
    render('Thank you', 'Your message has been sent.', true);
}

// 2. Timing.
$ts = (int) ($_POST['ts'] ?? 0);           // milliseconds, set by script
$elapsed = $ts > 0 ? (time() - intdiv($ts, 1000)) : -1;
if ($elapsed < MIN_FILL_SECONDS || $elapsed > MAX_FILL_AGE) {
    render(
        'Message not sent',
        'This form could not be verified as a genuine submission. Please reload the page and try again, or write to us directly using the details on the support section.',
        false
    );
}

// 3. Validation.
$name    = header_safe((string) ($_POST['name'] ?? ''));
$email   = header_safe((string) ($_POST['email'] ?? ''));
$topic   = header_safe((string) ($_POST['topic'] ?? 'Something else'));
$message = trim((string) ($_POST['message'] ?? ''));

$errors = [];
if ($name === '' || mb_strlen($name) > 100) {
    $errors[] = 'a name';
}
if (!filter_var($email, FILTER_VALIDATE_EMAIL) || mb_strlen($email) > 150) {
    $errors[] = 'a valid email address';
}
if (mb_strlen($message) < 10 || mb_strlen($message) > 4000) {
    $errors[] = 'a message between 10 and 4000 characters';
}
if ($errors !== []) {
    render('Message not sent', 'Please provide ' . implode(', ', $errors) . '.', false);
}

$allowedTopics = [
    'Question before buying', 'Licence activation', 'Refund request',
    'Bug report', 'Something else',
];
if (!in_array($topic, $allowedTopics, true)) {
    $topic = 'Something else';
}

// 5. Link flood.
if (preg_match_all('~https?://~i', $message) > MAX_LINKS) {
    render(
        'Message not sent',
        'Your message contains too many links to be delivered. Please remove some and try again.',
        false
    );
}

// 6. Rate limit.
$ip = client_ip();
if (rate_limited($ip)) {
    render(
        'Message not sent',
        'Several messages have already been sent from this connection recently. Please try again later.',
        false
    );
}

// ------------------------------------------------------------------- send

$subject = header_safe('[Easy-Post Desktop] ' . $topic . ' — ' . $name);

$body = "A message was sent from the Easy-Post Desktop contact form.\n\n"
      . "Name:    {$name}\n"
      . "Email:   {$email}\n"
      . "Topic:   {$topic}\n"
      . "IP:      {$ip}\n"
      . 'Time:    ' . gmdate('Y-m-d H:i:s') . " UTC\n\n"
      . "-----------------------------------------\n\n"
      . $message . "\n";

$headers = implode("\r\n", [
    'From: Easy-Post Desktop <' . MAIL_FROM . '>',
    // Reply goes to the sender, while From stays on our own domain so the
    // message is not rejected as a forgery by the receiving server.
    'Reply-To: ' . header_safe($name) . ' <' . $email . '>',
    'Content-Type: text/plain; charset=UTF-8',
    'X-Mailer: easy-post.spencerfields.com',
]);

$sent = @mail(MAIL_TO, $subject, $body, $headers, '-f' . MAIL_FROM);

if (!$sent) {
    render(
        'Message not sent',
        'Something went wrong sending your message. Please try again shortly, or use the direct email address shown in the support section.',
        false
    );
}

render(
    'Thank you',
    'Your message has been sent. You will normally hear back within one business day.',
    true
);
