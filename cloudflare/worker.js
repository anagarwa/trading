/**
 * Cloudflare Worker — Kite OAuth Token Exchange
 *
 * This Worker is set as the Kite OAuth redirect URL. When you complete the
 * Zerodha login, Kite redirects your browser here with ?request_token=...
 * The Worker then triggers GitHub Actions (token_exchange.yml) to exchange
 * the short-lived request_token for a long-lived access_token and store it
 * as a GitHub Secret — all without any credentials in client-side code.
 *
 * ─── Required Cloudflare Worker secrets ─────────────────────────────────────
 *  FINE_GRAINED_PAT   GitHub fine-grained PAT scoped to this repo only.
 *                     Required permissions: Actions → Read and write.
 *                     Set in: Cloudflare Dashboard → Workers → <worker> → Settings → Variables
 *
 *  GITHUB_OWNER       Your GitHub username (e.g. "anagarwa")
 *  GITHUB_REPO        Your repository name  (e.g. "trading")
 *
 * ─── How to set secrets ──────────────────────────────────────────────────────
 *  Option A — Cloudflare Dashboard:
 *    Workers → <worker> → Settings → Variables → Add variable (select Encrypt)
 *
 *  Option B — wrangler CLI:
 *    wrangler secret put FINE_GRAINED_PAT
 *    wrangler secret put GITHUB_OWNER
 *    wrangler secret put GITHUB_REPO
 *
 * ─── Kite Developer Console ──────────────────────────────────────────────────
 *  Set your app's redirect URL to:
 *    https://<your-worker-subdomain>.workers.dev/
 *
 * ─── Debugging ───────────────────────────────────────────────────────────────
 *  All console.log / console.error calls below appear in:
 *    Cloudflare Dashboard → Workers → <worker> → Logs  (real-time)
 *
 *  To see them in Chrome DevTools → Console while testing in a browser, open:
 *    https://<your-worker-subdomain>.workers.dev/?request_token=test
 *  The Worker returns an HTML page; all server-side logs reflect above.
 *  For live streaming logs in the terminal: wrangler tail
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    console.log("=== Kite Token Exchange Worker ===");
    console.log(`[worker] Method: ${request.method}`);
    console.log(`[worker] URL path: ${url.pathname}`);
    console.log(`[worker] Query string: ${url.search || "(none)"}`);
    console.log(`[worker] User-Agent: ${request.headers.get("User-Agent") || "(none)"}`);

    // ── Reject non-GET methods ─────────────────────────────────────────────────
    if (request.method !== "GET") {
      console.warn(`[worker] Rejected method: ${request.method} — only GET is expected (Kite redirects via browser)`);
      return new Response("Method Not Allowed", { status: 405 });
    }

    // ── Extract request_token from URL query params ───────────────────────────
    const requestToken = url.searchParams.get("request_token");
    const actionParam  = url.searchParams.get("action");   // Kite may also send action=login

    console.log(`[worker] request_token present: ${!!requestToken}`);
    console.log(`[worker] action param: ${actionParam || "(none)"}`);

    if (requestToken) {
      // Log only first/last 4 chars — never log the full token
      const preview = `${requestToken.slice(0, 4)}...${requestToken.slice(-4)}`;
      console.log(`[worker] request_token preview: ${preview} (length: ${requestToken.length})`);
    }

    if (!requestToken) {
      console.error("[worker] FAIL — No request_token in URL.");
      console.error("[worker] Possible causes:");
      console.error("  1. User cancelled the Kite login.");
      console.error("  2. Kite redirect URL in Developer Console does not match this Worker URL.");
      console.error("  3. Kite sent a different parameter name (check the full URL above).");
      return htmlResponse(
        errorHtml(
          "Kite Login Failed",
          "No <code>request_token</code> was found in the redirect URL.",
          "The Kite login may have been cancelled, or the redirect URL in the " +
          "Kite Developer Console does not point to this Worker."
        ),
        400
      );
    }

    // ── Validate Worker secrets are configured ────────────────────────────────
    const { FINE_GRAINED_PAT, GITHUB_OWNER, GITHUB_REPO } = env;

    if (!FINE_GRAINED_PAT) {
      console.error("[worker] FAIL — FINE_GRAINED_PAT is not set as a Worker secret.");
      console.error("[worker] Go to: Cloudflare Dashboard → Workers → <your worker> → Settings → Variables");
      console.error("[worker] Add an encrypted variable named FINE_GRAINED_PAT with a GitHub fine-grained PAT");
      console.error("[worker] The PAT needs 'Actions: Read and write' permission for this repository.");
      return htmlResponse(
        errorHtml(
          "Worker Not Configured",
          "<code>FINE_GRAINED_PAT</code> secret is missing from the Cloudflare Worker.",
          "Go to Cloudflare Dashboard → Workers → your worker → Settings → Variables " +
          "and add FINE_GRAINED_PAT as an encrypted variable."
        ),
        500
      );
    }

    if (!GITHUB_OWNER) {
      console.error("[worker] FAIL — GITHUB_OWNER secret is not set.");
      return htmlResponse(
        errorHtml(
          "Worker Not Configured",
          "<code>GITHUB_OWNER</code> secret is missing from the Cloudflare Worker.",
          "Set it to your GitHub username via Cloudflare Dashboard → Workers → Settings → Variables."
        ),
        500
      );
    }

    if (!GITHUB_REPO) {
      console.error("[worker] FAIL — GITHUB_REPO secret is not set.");
      return htmlResponse(
        errorHtml(
          "Worker Not Configured",
          "<code>GITHUB_REPO</code> secret is missing from the Cloudflare Worker.",
          "Set it to your GitHub repository name via Cloudflare Dashboard → Workers → Settings → Variables."
        ),
        500
      );
    }

    console.log(`[worker] Secrets validated. Target: github.com/${GITHUB_OWNER}/${GITHUB_REPO}`);

    // ── Dispatch GitHub Actions workflow_dispatch ─────────────────────────────
    const dispatchUrl =
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}` +
      `/actions/workflows/token_exchange.yml/dispatches`;

    console.log(`[worker] Calling GitHub Actions dispatch: POST ${dispatchUrl}`);

    const dispatchBody = JSON.stringify({
      ref: "main",
      inputs: {
        request_token: requestToken,
      },
    });

    console.log(`[worker] Dispatch payload ref: "main", inputs.request_token: [token preview above]`);

    let githubResponse;
    try {
      githubResponse = await fetch(dispatchUrl, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${FINE_GRAINED_PAT}`,
          "Accept":        "application/vnd.github.v3+json",
          "Content-Type":  "application/json",
          "User-Agent":    "trading-bot-cloudflare-worker/1.0",
        },
        body: dispatchBody,
      });
    } catch (networkError) {
      console.error(`[worker] FAIL — Network error reaching GitHub API: ${networkError.message}`);
      console.error("[worker] This is unusual for Cloudflare Workers; check if the Worker has internet egress enabled.");
      return htmlResponse(
        errorHtml(
          "Network Error",
          "The Worker could not reach the GitHub API.",
          `Error details: <code>${networkError.message}</code>`
        ),
        502
      );
    }

    console.log(`[worker] GitHub API response status: ${githubResponse.status}`);
    console.log(`[worker] GitHub API response headers: ${JSON.stringify([...githubResponse.headers])}`);

    // ── Success path ──────────────────────────────────────────────────────────
    if (githubResponse.status === 204) {
      console.log("[worker] SUCCESS — GitHub accepted the workflow dispatch (HTTP 204 No Content).");
      console.log("[worker] token_exchange.yml is now running. It will update KITE_ACCESS_TOKEN within ~30 seconds.");
      return htmlResponse(successHtml(), 200);
    }

    // ── Error path — read GitHub's error body for diagnostics ─────────────────
    let errorBody = "(could not read response body)";
    try {
      errorBody = await githubResponse.text();
    } catch (_) { /* ignore */ }

    console.error(`[worker] FAIL — GitHub API returned HTTP ${githubResponse.status}`);
    console.error(`[worker] GitHub error body: ${errorBody}`);

    // Provide targeted hints for the most common error codes
    if (githubResponse.status === 401) {
      console.error("[worker] HINT (401 Unauthorized):");
      console.error("  — FINE_GRAINED_PAT may have expired. Check expiry in GitHub → Settings → Developer settings → PATs.");
      console.error("  — PAT may lack 'Actions: Read and write' permission for this repository.");
      console.error("  — PAT may not be scoped to the correct repository.");
    } else if (githubResponse.status === 404) {
      console.error("[worker] HINT (404 Not Found):");
      console.error(`  — Verify GITHUB_OWNER="${GITHUB_OWNER}" and GITHUB_REPO="${GITHUB_REPO}" are correct.`);
      console.error("  — Verify the file .github/workflows/token_exchange.yml exists on the main branch.");
      console.error("  — Verify the workflow has `on: workflow_dispatch` trigger enabled.");
    } else if (githubResponse.status === 422) {
      console.error("[worker] HINT (422 Unprocessable Entity):");
      console.error("  — The 'ref' (branch name) may be wrong. Currently set to 'main'.");
      console.error("  — Check that your default branch is named 'main' (not 'master').");
    } else if (githubResponse.status === 403) {
      console.error("[worker] HINT (403 Forbidden):");
      console.error("  — FINE_GRAINED_PAT has the correct repo but may be missing Actions:Write permission.");
      console.error("  — GitHub Actions may be disabled for this repository.");
    }

    return htmlResponse(
      errorHtml(
        "GitHub API Error",
        `GitHub returned HTTP <code>${githubResponse.status}</code>.`,
        `<code>${errorBody.slice(0, 400)}</code><br><br>` +
        "Check Cloudflare Worker logs for detailed diagnostics: " +
        "Cloudflare Dashboard → Workers → your worker → Logs."
      ),
      502
    );
  },
};

// ── HTML helper functions ──────────────────────────────────────────────────────

function htmlResponse(html, status) {
  return new Response(html, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

function successHtml() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Trading Bot — Authorised</title>
  ${sharedStyles()}
</head>
<body>
<div class="card">
  <div class="icon">✅</div>
  <h1 class="success">Bot is active for today!</h1>
  <p>Your Kite access token has been securely passed to GitHub Actions.</p>
  <p style="margin-top:12px">
    The token exchange workflow is now running and will update
    <code>KITE_ACCESS_TOKEN</code> within ~30 seconds.
  </p>
  <p style="margin-top:16px;font-size:13px">You can safely close this tab.</p>
</div>
</body>
</html>`;
}

function errorHtml(title, reason, hint) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Trading Bot — Error</title>
  ${sharedStyles()}
</head>
<body>
<div class="card">
  <div class="icon">🚨</div>
  <h1 class="error">${title}</h1>
  <p style="margin-top:8px">${reason}</p>
  <p style="margin-top:12px;font-size:13px">${hint}</p>
  <p style="margin-top:20px;font-size:12px;color:#6e7681">
    Detailed logs: Cloudflare Dashboard → Workers → your worker → Logs
  </p>
</div>
</body>
</html>`;
}

function sharedStyles() {
  return `<style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0d1117; color: #c9d1d9;
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
    }
    .card {
      background: #161b22; border: 1px solid #30363d; border-radius: 12px;
      padding: 40px 48px; max-width: 520px; width: 100%; text-align: center;
    }
    .icon  { font-size: 56px; margin-bottom: 16px; }
    h1     { font-size: 22px; font-weight: 600; margin-bottom: 10px; color: #f0f6fc; }
    p      { font-size: 15px; line-height: 1.6; color: #8b949e; margin-bottom: 8px; }
    .success { color: #3fb950; }
    .error   { color: #f85149; }
    code {
      background: #0d1117; border: 1px solid #30363d; border-radius: 4px;
      padding: 2px 6px; font-size: 13px; color: #79c0ff; word-break: break-all;
    }
  </style>`;
}
