/**
 * XV Webbing Lead Agent — Slack Bot (Cloudflare Worker)
 *
 * Slash commands supported (/leads ...):
 *   list            — show current search queries
 *   add <query>     — add a new search (e.g. /leads add electrician Tampa)
 *   remove <query>  — remove a search by exact match
 *   run             — trigger a scrape right now
 *   status          — show status of the most recent run
 *   help            — show this help
 *
 * Required environment variables (set in Cloudflare Worker dashboard → Settings → Variables):
 *   SLACK_SIGNING_SECRET  — from Slack app "Basic Information" page
 *   GITHUB_TOKEN          — fine-grained PAT with repo contents:write + actions:write
 *   GITHUB_OWNER          — e.g. xvwebbing-commits
 *   GITHUB_REPO           — e.g. XV-leads-agent
 *   WORKFLOW_FILE         — e.g. scrape.yml
 */

const QUERIES_PATH = "queries.txt";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("XV Lead Bot — POST slash commands here.", { status: 200 });
    }

    const bodyText = await request.text();

    // Verify Slack signature
    const valid = await verifySlack(request, bodyText, env.SLACK_SIGNING_SECRET);
    if (!valid) return new Response("Invalid signature", { status: 401 });

    const params = new URLSearchParams(bodyText);
    const text = (params.get("text") || "").trim();
    const [cmd, ...rest] = text.split(/\s+/);
    const arg = rest.join(" ").trim();

    try {
      switch ((cmd || "help").toLowerCase()) {
        case "list":       return slackJson(await listQueries(env));
        case "add":        return slackJson(await addQuery(env, arg));
        case "remove":     return slackJson(await removeQuery(env, arg));
        case "run":        return slackJson(await runWorkflow(env));
        case "status":     return slackJson(await runStatus(env));
        case "help":
        default:           return slackJson(helpText());
      }
    } catch (err) {
      return slackJson(`:warning: Error: ${err.message || err}`);
    }
  },
};

// ——— Slack helpers ———

function slackJson(text) {
  return new Response(JSON.stringify({ response_type: "in_channel", text }), {
    headers: { "Content-Type": "application/json" },
  });
}

async function verifySlack(request, body, signingSecret) {
  const ts = request.headers.get("x-slack-request-timestamp");
  const sig = request.headers.get("x-slack-signature");
  if (!ts || !sig) return false;
  // reject requests older than 5 min (replay protection)
  if (Math.abs(Date.now() / 1000 - Number(ts)) > 300) return false;

  const base = `v0:${ts}:${body}`;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(signingSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(base));
  const hex = Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  const expected = `v0=${hex}`;

  // constant-time compare
  if (expected.length !== sig.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
  return diff === 0;
}

// ——— GitHub API ———

async function ghFetch(env, path, init = {}) {
  const res = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "xv-lead-bot",
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`GitHub ${res.status}: ${body.slice(0, 200)}`);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

async function getQueriesFile(env) {
  const data = await ghFetch(
    env,
    `/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/${QUERIES_PATH}`,
  );
  // atob + decode UTF-8
  const bytes = Uint8Array.from(atob(data.content.replace(/\n/g, "")), (c) => c.charCodeAt(0));
  const content = new TextDecoder().decode(bytes);
  return { sha: data.sha, content };
}

async function writeQueriesFile(env, newContent, message) {
  const { sha } = await getQueriesFile(env);
  const b64 = btoa(new TextEncoder().encode(newContent).reduce((s, b) => s + String.fromCharCode(b), ""));
  return ghFetch(env, `/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/${QUERIES_PATH}`, {
    method: "PUT",
    body: JSON.stringify({ message, content: b64, sha }),
  });
}

// ——— Commands ———

async function listQueries(env) {
  const { content } = await getQueriesFile(env);
  const lines = content.split("\n").map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return "No queries configured.";
  return `:mag: *Current queries* (${lines.length}):\n` + lines.map((l, i) => `${i + 1}. ${l}`).join("\n");
}

async function addQuery(env, q) {
  if (!q) return "Usage: `/leads add <query>` e.g. `/leads add electrician Tampa`";
  const { content } = await getQueriesFile(env);
  const lines = content.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.some((l) => l.toLowerCase() === q.toLowerCase())) return `Already in the list: \`${q}\``;
  lines.push(q);
  const newContent = lines.join("\n") + "\n";
  await writeQueriesFile(env, newContent, `slack: add "${q}"`);
  return `:white_check_mark: Added: \`${q}\`  (${lines.length} queries total)`;
}

async function removeQuery(env, q) {
  if (!q) return "Usage: `/leads remove <query>`";
  const { content } = await getQueriesFile(env);
  const lines = content.split("\n").map((l) => l.trim()).filter(Boolean);
  const idx = lines.findIndex((l) => l.toLowerCase() === q.toLowerCase());
  if (idx === -1) return `Not found: \`${q}\`. Use \`/leads list\` to see current queries.`;
  lines.splice(idx, 1);
  const newContent = lines.join("\n") + (lines.length ? "\n" : "");
  await writeQueriesFile(env, newContent, `slack: remove "${q}"`);
  return `:wastebasket: Removed: \`${q}\`  (${lines.length} queries total)`;
}

async function runWorkflow(env) {
  await ghFetch(
    env,
    `/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${env.WORKFLOW_FILE}/dispatches`,
    { method: "POST", body: JSON.stringify({ ref: "main" }) },
  );
  return `:rocket: Scrape triggered — running now. Check <https://github.com/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions|GitHub Actions> for progress. You'll get a notification when it's done.`;
}

async function runStatus(env) {
  const data = await ghFetch(
    env,
    `/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/runs?per_page=1`,
  );
  const run = data.workflow_runs && data.workflow_runs[0];
  if (!run) return "No runs yet.";
  const icon = run.status === "completed"
    ? (run.conclusion === "success" ? ":white_check_mark:" : ":x:")
    : ":hourglass_flowing_sand:";
  const started = new Date(run.created_at).toLocaleString("en-US", { timeZone: "America/New_York" });
  return `${icon} *Last run*: ${run.status}${run.conclusion ? ` (${run.conclusion})` : ""}\n` +
         `   Started: ${started} ET\n` +
         `   <${run.html_url}|View full run>`;
}

function helpText() {
  return [
    "*XV Lead Agent commands:*",
    "`/leads list` — show current search queries",
    "`/leads add <query>` — add a new search (e.g. `/leads add electrician Tampa`)",
    "`/leads remove <query>` — remove a search by exact match",
    "`/leads run` — trigger a scrape right now",
    "`/leads status` — show status of the most recent run",
    "`/leads help` — this message",
  ].join("\n");
}
