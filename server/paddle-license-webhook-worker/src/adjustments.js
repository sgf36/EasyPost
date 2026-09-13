/**
 * What a Paddle adjustment means for the licence it touches.
 *
 * Paddle raises an adjustment for every change to a paid transaction: a refund
 * request that may yet be rejected, a goodwill credit, a partial refund, a
 * chargeback, and the reversal of a chargeback Paddle went on to win. Until
 * 2026-09-13 the Worker revoked the key on every one of them, the moment it was
 * created, so a customer whose refund was refused or whose dispute was lost by
 * the buyer's bank was locked out for good.
 *
 * The rule now is that a key dies only once money has actually gone back to the
 * buyer, and comes back to life when Paddle says that money has returned to us.
 * This module decides which of those an adjustment is; it touches no database,
 * so the decision can be tested on its own.
 *
 * Field names follow Paddle's adjustment entity (developer.paddle.com,
 * api-reference/adjustments): action, status, type, items[].type,
 * transaction_id, subscription_id.
 */

// Reasons written to revocations.reason. The customer sees this word in the
// activation error, so it is a plain noun rather than an event name: the old
// code stored the verb "created", which told nobody anything.
export const REASON_REFUNDED = "refunded";
export const REASON_CHARGEBACK = "chargeback";

// Written by the pre-2026-09-13 code for ANY adjustment. A row with this reason
// may be a genuine refund or may be one of the lock-outs described above, so it
// is the only reason an automatic restore is allowed to clear besides the one
// the restoring event itself undoes.
export const REASON_LEGACY = "created";

// Revocations that later evidence may overturn. Everything else, including
// "refunded" and any reason written by hand ("withdrawn"), is final and is
// never replaced by a weaker reason or cleared by a webhook.
export const RECOVERABLE_REASONS = [REASON_LEGACY, REASON_CHARGEBACK];

/**
 * True when the adjustment returns the whole transaction.
 *
 * `type: "full"` is Paddle's own statement of that. Older payloads may carry
 * only items, so an adjustment whose every item is itself `full` counts too. A
 * `partial`, `tax` or `proration` item anywhere means some of the licence was
 * kept, and a partial refund is a goodwill gesture, not a cancellation.
 */
function isFullAdjustment(data) {
  if (data.type === "full") return true;
  if (data.type && data.type !== "partial") return false;
  const items = Array.isArray(data.items) ? data.items : [];
  return items.length > 0 && items.every((i) => i && i.type === "full");
}

/**
 * The licence orders an adjustment reaches.
 *
 * A perpetual key is signed with the transaction id; an annual key with the
 * subscription id, so it survives renewals. Paddle copies the transaction's
 * subscription_id onto the adjustment, so both are available here without a
 * second API call. Acting on both is harmless: an order id that no key carries
 * blocks nothing.
 */
function ordersFor(data) {
  const orders = [];
  if (data.transaction_id) orders.push(String(data.transaction_id));
  if (data.subscription_id) orders.push(String(data.subscription_id));
  return orders;
}

/**
 * Returns one of:
 *   { verdict: "revoke",  reason, orders }
 *   { verdict: "restore", clear: [reasons], orders }
 *   { verdict: "ignore",  why }
 */
export function classifyAdjustment(data) {
  const action = String(data.action || "");
  const status = String(data.status || "");
  const orders = ordersFor(data);
  if (orders.length === 0) return { verdict: "ignore", why: "no-transaction-id" };

  // Paddle contested a chargeback and won: the money is ours again, so the
  // chargeback's revocation is lifted. Paddle signals this twice — a new
  // chargeback_reverse adjustment, and the original chargeback moving to
  // "reversed" — and either is enough; restoring twice is a no-op.
  if ((action === "chargeback_reverse" && status === "approved")
      || (action === "chargeback" && status === "reversed")) {
    return { verdict: "restore", clear: RECOVERABLE_REASONS, orders };
  }

  // Paddle refused the refund, so nothing was paid back. New code never revokes
  // for a pending refund, so the only revocation this can be undoing is one the
  // old code wrote on adjustment.created.
  if (status === "rejected") {
    return { verdict: "restore", clear: [REASON_LEGACY], orders };
  }

  if (status !== "approved") return { verdict: "ignore", why: `status-${status || "unknown"}` };

  if (action === "refund") {
    if (!isFullAdjustment(data)) return { verdict: "ignore", why: "partial-refund" };
    return { verdict: "revoke", reason: REASON_REFUNDED, orders };
  }

  // A chargeback is the buyer repudiating the purchase through their bank, so
  // the licence goes whatever the amount. chargeback_warning is deliberately
  // NOT here: Paddle may follow it with a chargeback (which revokes) or a
  // warning reversal, and revoking on the warning would need a matching restore
  // whose ordering against the chargeback is not documented.
  if (action === "chargeback") {
    return { verdict: "revoke", reason: REASON_CHARGEBACK, orders };
  }

  // credit, credit_reverse, chargeback_warning, chargeback_warning_reverse.
  return { verdict: "ignore", why: `action-${action || "unknown"}` };
}
