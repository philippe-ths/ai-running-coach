# Per-user coach-report notifications route via per-user Telegram binding

Phase 1 sends every coach-report notification to one deployment-wide recipient
(`TELEGRAM_CHAT_ID`, or `NOTIFY_TO` for email). Multi-user (#67) requires each
notification to reach the **owner of the activity** on **their own channel** and
no one else (#120). This is no longer a simple swap of an env var; it needs a
channel decision. This ADR resolves it.

## The decision: per-user Telegram binding

Each user links **their own Telegram chat**; coach-report notifications and
receipts route to the activity owner's bound chat. Telegram stays the channel.

### Why Telegram, not an HTTP email API

The two candidates from #120:

- **Per-user Telegram binding** — each user links their own chat (needs a linking
  UX). Reuses the channel that already works from the Railway worker, adds no
  dependency, and **preserves the tappable RPE / pain / done keyboard** that the
  whole #296 receipt cadence and the I1b inbound-callback flow are built around.
- **HTTP email API (Resend/Postmark)** — per-user email; would also finally fix
  the coach-report email that SMTP-on-Railway (#127) blocks. But email **cannot
  carry inline tap buttons**, so adopting it would regress the receipt-cadence
  interaction model (taps → links, a separate rebuild), and it adds a vendor
  dependency.

For the friends/beta scale this rollout targets, requiring Telegram is
acceptable, and keeping the tap UX is worth more than email's universality. We
choose **per-user Telegram binding**. The email-API channel is **deferred**, not
rejected forever — it remains the right answer if/when a no-Telegram audience
matters, and the routing seam below is channel-agnostic so adding it later is an
adapter + a per-user email address, not a redesign.

## Design

### Routing (built in this slice — behavior-preserving)
- `users.telegram_chat_id` (nullable) holds each user's bound chat.
- `notifications.resolve_recipient(user)` returns the user's bound chat on the
  active Telegram channel, else `None`.
- The composer (`build_coach_notification` / `build_receipt_notification`) takes a
  `recipient`; `to = recipient or <global recipient>`. The Telegram adapter sends
  to `notification.to or self.chat_id`.
- The pipeline resolves the activity owner and threads `recipient`.
- **Back-compat:** an unbound user (every user today) resolves to `None` and falls
  back to the configured global recipient, so single-user behavior is byte-identical
  until users link. Email is left on the global `NOTIFY_TO` (per-user email is the
  deferred channel).
- **No deliverable channel ⇒ nothing:** if the active channel is unconfigured the
  composer returns `None` and the pipeline skips the send (existing behavior);
  the pipeline never breaks on a missing recipient.
- **At-most-once-per-activity-per-channel** is unchanged: the dedup sentinels live
  on the `Exchange` / `Activity` rows, independent of who the recipient is.

### Linking + inbound (deferred follow-up — depends on #118 / P2.0)
- **Linking UX:** the user taps a deep link that opens the bot with a one-time
  token (`/start <token>`); the bot's webhook captures their `chat.id` and binds
  it to the **authenticated** user. This needs the per-user identity from #118
  (P2.0) to know *which* user is binding.
- **Inbound callback auth:** the Telegram tap webhook today authorizes on the
  single global `TELEGRAM_CHAT_ID`. Multi-user, it must map the incoming
  `chat.id` to a bound user and verify that user owns the tapped activity (the
  tap then writes that user's `CheckIn`). This rewrites a live prod path, so it
  ships as its own reviewed change after P2.0 lands.

## Consequences
- A user with no linked Telegram simply gets no notification (no pipeline break) —
  acceptable for beta; the linking prompt is part of onboarding.
- `project-context.md` updates: `User` gains `telegram_chat_id`; notifications
  route per-user with a global fallback.
- The global `TELEGRAM_CHAT_ID` / `NOTIFY_TO` remain as the back-compat fallback
  and the single-user/local path; they are not removed.
- Until the linking + inbound slice lands, the routing is in place but inert in
  prod (no user has a bound chat yet), so this slice carries no behavior change.
