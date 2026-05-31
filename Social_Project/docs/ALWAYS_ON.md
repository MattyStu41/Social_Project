# Always-On Tier Guidance

JACK's scheduler runs in-process via APScheduler. That means the host has
to keep the Python process alive 24/7 for scheduled posts to publish on time.

## The trade-off

| Tier | Cost (per Replit pricing — verify on their pricing page) | 24/7? | Effect on JACK |
|---|---|---|---|
| Replit free tier (Repl) | $0 | **No** — sleeps after idle | Scheduled posts only publish while you have the editor open. |
| Replit Hacker / Core (Repl) | A few USD / mo | Mostly | Repls run longer but can still sleep. Not recommended for scheduling. |
| Replit Deployments — Reserved VM | Single-digit USD / mo at the smallest tier, scales up with CPU/RAM | **Yes** | Process is pinned. APScheduler fires on time. **This is the right choice for JACK.** |

**The headline rule:** if you do not run on a tier that pins the process,
scheduled posts will not publish while the host is asleep. JACK can't work
around this — by design it does not depend on an external scheduler, so the
process *must* be the one that wakes itself up.

Cost figures fluctuate; check https://replit.com/pricing for the current
table before committing.

## What "always on" buys you

* **Scheduled posts publish on time.** This is the main job.
* **Token refresh runs on its 6-hour interval.** Without always-on, you can
  miss the refresh window and tokens silently expire.
* **Substack RSS poller fires every 15 minutes.**
* **Analytics pull-back jobs at +1h, +24h, +72h, +7d fire as queued.** A
  restart between the publish and the scheduled fire time loses the job;
  the operator can re-trigger from `/analytics`.

## What if you decline always-on?

If the cost is unacceptable and you're willing to operate manually:

* Open the dashboard before every scheduled time. The scheduler will pick up
  due posts within `SCHEDULER_INTERVAL_SECONDS` (default 30s).
* Hit **Publish now** for anything that should have gone out while the host
  was asleep.
* Expect to reconnect platforms periodically because token refresh did not
  run on time.

This is an honest description, not an endorsement — for a daily distribution
engine, manual operation defeats the point.

## Alternative: external cron + Vercel

JACK is not designed for this and the README has a section explaining why.
Briefly: a serverless host tearing down the runtime between requests means
APScheduler can never own the schedule, and an external cron service hitting
a `/cron` endpoint adds a paid third-party dependency the operator's brief
prohibits.
