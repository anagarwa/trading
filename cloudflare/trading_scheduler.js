/**
 * Cloudflare Worker — Trading Agent Scheduler
 *
 * Replaces GitHub Actions' unreliable cron scheduler with Cloudflare's
 * highly-reliable cron triggers. Fires every 2 hours during market hours
 * and dispatches the trading GitHub Actions workflow.
 *
 * main.py auto-detects sell-only mode when IST hour >= 15.
 *
 * ─── Cron schedule (UTC, set in wrangler_scheduler.toml) ────────────────────
 *   50 3  * * 1-5   →  9:20 AM IST
 *   50 5  * * 1-5   → 11:20 AM IST
 *   50 7  * * 1-5   →  1:20 PM IST
 *   50 9  * * 1-5   →  3:20 PM IST  (sell-only — auto-detected by main.py)
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
 *    3. Settings → Triggers → Add Cron Trigger — add the four crons above
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
 *  Visit: https://trading-scheduler.<your-subdomain>.workers.dev/?confirm=yes
 *  Returns JSON with the GitHub API response.
 *
 * ─── Debugging ───────────────────────────────────────────────────────────────
 *  All console.log / console.error calls appear in:
 *    Cloudflare Dashboard → Workers → trading-scheduler → Logs
 *    or: wrangler tail --name trading-scheduler
 */

// All crons dispatch `run_type=run`; sell-only is auto-detected by main.py.
const VALID_CRONS = new Set([
  "50 3 * * 1-5",
  "50 5 * * 1-5",
  "50 7 * * 1-5",
  "50 9 * * 1-5",
]);

export default {

  // ── Scheduled handler (cron trigger) ──────────────────────────────────────
  async scheduled(event, env, ctx) {
    console.log("=== Trading Scheduler (cron) ===");
    console.log(`[scheduler] Cron expression triggered: "${event.cron}"`);
    console.log(`[scheduler] Scheduled time (UTC): ${new Date(event.scheduledTime).toISOString()}`);

    if (!VALID_CRONS.has(event.cron)) {
      console.error(`[scheduler] FAIL — Unexpected cron "${event.cron}".`);
      console.error("[scheduler] Check that wrangler_scheduler.toml triggers match VALID_CRONS.");
      return;
    }

    console.log(`[scheduler] Dispatching run_type="run" for cron "${event.cron}"`);
    await dispatchTradingWorkflow(env);
  },

  // ── Fetch handler (manual HTTP trigger for testing) ───────────────────────
  async fetch(request, env, ctx) {
    const url     = new URL(request.url);
    const confirm = url.searchParams.get("confirm");

    console.log("=== Trading Scheduler (HTTP) ===");
    console.log(`[scheduler] Manual trigger request. confirm="${confirm || "(none)"}"`);

    if (confirm !== "yes") {
      const msg = 'Pass ?confirm=yes to dispatch a trading run.';
      console.warn(`[scheduler] ${msg}`);
      return new Response(JSON.stringify({ error: msg }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    }

    const result = await dispatchTradingWorkflow(env);
    return new Response(JSON.stringify(result), {
      status: result.ok ? 200 : 502,
      headers: { "Content-Type": "application/json" },
    });
  },
};

// ── Core dispatch function ─────────────────────────────────────────────────

async function dispatchTradingWorkflow(env) {
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

  console.log(`[scheduler] Dispatching run_type="run" to ${dispatchUrl}`);

  const body = JSON.stringify({
    ref: "main",
    inputs: { run_type: "run" },
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
    console.log('[scheduler] SUCCESS — GitHub accepted dispatch for run_type="run".');
    console.log("[scheduler] trading.yml is now queued in GitHub Actions.");
    return { ok: true, run_type: "run", status: 204 };
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

  return { ok: false, run_type: "run", status: response.status, error: errorBody.slice(0, 400) };
}
