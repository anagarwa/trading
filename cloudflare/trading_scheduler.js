/**
 * Cloudflare Worker — Trading Agent Scheduler
 *
 * Replaces GitHub Actions' unreliable cron scheduler with Cloudflare's
 * highly-reliable cron triggers. Fires at the three IST trading session
 * times and dispatches the corresponding GitHub Actions workflow run.
 *
 * ─── Cron schedule (UTC, set in wrangler_scheduler.toml) ────────────────────
 *   5  4  * * 1-5   →  9:35 AM IST  → morning session
 *   35 6  * * 1-5   → 12:05 PM IST  → midday session
 *   30 9  * * 1-5   →  3:00 PM IST  → eod session
 *
 * ─── Required Cloudflare Worker secrets ─────────────────────────────────────
 *  FINE_GRAINED_PAT   GitHub fine-grained PAT — same one already stored in
 *                     the kite-token-exchange worker.
 *                     Required permission: Actions → Read and write (this repo).
 *  GITHUB_OWNER       Your GitHub username  (e.g. "anagarwa")
 *  GITHUB_REPO        Your repository name  (e.g. "trading")
 *
 * ─── Deployment ──────────────────────────────────────────────────────────────
 *  Option A — Dashboard (easiest):
 *    1. Workers & Pages → Create → Create Worker
 *    2. Paste this file, click Deploy
 *    3. Settings → Triggers → Add Cron Trigger — add the three crons above
 *    4. Settings → Variables — add the three encrypted secrets above
 *
 *  Option B — wrangler CLI:
 *    cd cloudflare
 *    wrangler deploy --config wrangler_scheduler.toml
 *    wrangler secret put FINE_GRAINED_PAT --name trading-scheduler
 *    wrangler secret put GITHUB_OWNER     --name trading-scheduler
 *    wrangler secret put GITHUB_REPO      --name trading-scheduler
 *
 * ─── Manual test (HTTP GET) ──────────────────────────────────────────────────
 *  Visit: https://trading-scheduler.<your-subdomain>.workers.dev/?session=morning
 *  Valid values: morning | midday | eod
 *  Returns JSON with the GitHub API response.
 *
 * ─── Debugging ───────────────────────────────────────────────────────────────
 *  All console.log / console.error calls appear in:
 *    Cloudflare Dashboard → Workers → trading-scheduler → Logs
 *    or: wrangler tail --name trading-scheduler
 */

// Map each cron schedule string → trading session name.
// These must exactly match the cron expressions in wrangler_scheduler.toml.
const CRON_TO_SESSION = {
  "5 4 * * 1-5":   "morning",
  "35 6 * * 1-5":  "midday",
  "30 9 * * 1-5":  "eod",
};

export default {

  // ── Scheduled handler (cron trigger) ──────────────────────────────────────
  async scheduled(event, env, ctx) {
    console.log("=== Trading Scheduler (cron) ===");
    console.log(`[scheduler] Cron expression triggered: "${event.cron}"`);
    console.log(`[scheduler] Scheduled time (UTC): ${new Date(event.scheduledTime).toISOString()}`);

    const session = CRON_TO_SESSION[event.cron];

    if (!session) {
      console.error(`[scheduler] FAIL — No session mapped for cron "${event.cron}".`);
      console.error("[scheduler] Check that CRON_TO_SESSION keys exactly match wrangler_scheduler.toml triggers.");
      return;
    }

    console.log(`[scheduler] Mapped "${event.cron}" → session="${session}"`);
    await dispatchTradingWorkflow(session, env);
  },

  // ── Fetch handler (manual HTTP trigger for testing) ───────────────────────
  async fetch(request, env, ctx) {
    const url     = new URL(request.url);
    const session = url.searchParams.get("session");

    console.log("=== Trading Scheduler (HTTP) ===");
    console.log(`[scheduler] Manual trigger request. session param="${session || "(none)"}"`);

    const valid = ["morning", "midday", "eod"];

    if (!session || !valid.includes(session)) {
      const msg = `Missing or invalid ?session= param. Valid values: ${valid.join(" | ")}`;
      console.warn(`[scheduler] ${msg}`);
      return new Response(JSON.stringify({ error: msg }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    }

    const result = await dispatchTradingWorkflow(session, env);
    return new Response(JSON.stringify(result), {
      status: result.ok ? 200 : 502,
      headers: { "Content-Type": "application/json" },
    });
  },
};

// ── Core dispatch function ─────────────────────────────────────────────────

async function dispatchTradingWorkflow(session, env) {
  const { FINE_GRAINED_PAT, GITHUB_OWNER, GITHUB_REPO } = env;

  // ── Validate secrets ───────────────────────────────────────────────────────
  if (!FINE_GRAINED_PAT) {
    console.error("[scheduler] FAIL — FINE_GRAINED_PAT secret is not set.");
    console.error("[scheduler] Go to: Cloudflare Dashboard → Workers → trading-scheduler → Settings → Variables");
    return { ok: false, error: "FINE_GRAINED_PAT secret missing from Worker" };
  }
  if (!GITHUB_OWNER) {
    console.error("[scheduler] FAIL — GITHUB_OWNER secret is not set.");
    return { ok: false, error: "GITHUB_OWNER secret missing from Worker" };
  }
  if (!GITHUB_REPO) {
    console.error("[scheduler] FAIL — GITHUB_REPO secret is not set.");
    return { ok: false, error: "GITHUB_REPO secret missing from Worker" };
  }

  const dispatchUrl =
    `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}` +
    `/actions/workflows/trading.yml/dispatches`;

  console.log(`[scheduler] Dispatching session="${session}" to ${dispatchUrl}`);

  const body = JSON.stringify({
    ref: "main",
    inputs: { run_type: session },
  });

  let response;
  try {
    response = await fetch(dispatchUrl, {
      method:  "POST",
      headers: {
        "Authorization": `Bearer ${FINE_GRAINED_PAT}`,
        "Accept":        "application/vnd.github.v3+json",
        "Content-Type":  "application/json",
        "User-Agent":    "trading-bot-cloudflare-scheduler/1.0",
      },
      body,
    });
  } catch (networkErr) {
    console.error(`[scheduler] FAIL — Network error reaching GitHub API: ${networkErr.message}`);
    return { ok: false, error: `Network error: ${networkErr.message}` };
  }

  console.log(`[scheduler] GitHub API response: HTTP ${response.status}`);

  // HTTP 204 = GitHub accepted the workflow dispatch
  if (response.status === 204) {
    console.log(`[scheduler] SUCCESS — GitHub accepted dispatch for session="${session}".`);
    console.log("[scheduler] trading.yml is now queued in GitHub Actions.");
    return { ok: true, session, status: 204 };
  }

  // Error — read body for diagnostics
  let errorBody = "(could not read response body)";
  try { errorBody = await response.text(); } catch (_) {}

  console.error(`[scheduler] FAIL — GitHub returned HTTP ${response.status}`);
  console.error(`[scheduler] GitHub error body: ${errorBody}`);

  if (response.status === 401) {
    console.error("[scheduler] HINT (401 Unauthorized):");
    console.error("  — FINE_GRAINED_PAT may have expired. Check GitHub → Settings → Developer settings → PATs.");
    console.error("  — PAT must have 'Actions: Read and write' for this repository.");
  } else if (response.status === 404) {
    console.error("[scheduler] HINT (404 Not Found):");
    console.error(`  — Verify GITHUB_OWNER="${GITHUB_OWNER}" and GITHUB_REPO="${GITHUB_REPO}" are correct.`);
    console.error("  — Verify .github/workflows/trading.yml exists on the main branch.");
    console.error("  — Verify trading.yml has 'on: workflow_dispatch' trigger.");
  } else if (response.status === 422) {
    console.error("[scheduler] HINT (422 Unprocessable Entity):");
    console.error("  — Branch 'main' may not exist. Check your default branch name.");
  } else if (response.status === 403) {
    console.error("[scheduler] HINT (403 Forbidden):");
    console.error("  — PAT may be missing Actions:Write permission.");
    console.error("  — GitHub Actions may be disabled for this repository.");
  }

  return { ok: false, session, status: response.status, error: errorBody.slice(0, 400) };
}
