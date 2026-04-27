/**
 * XV Webbing Lead Agent — Slack Bot (Cloudflare Worker)
 *
 * Slash commands (/leads ...):
 *   list            — show current search queries
 *   add <query>     — add a new search
 *   remove <query>  — remove a search
 *   run             — trigger a scrape right now
 *   status          — show last run status
 *   approve         — send this week's emails
 *   skip            — skip emailing this week
 *   ask <question>  — chat with Llama 3.3 70B
 *   help            — show commands
 *
 * Environment variables (set in Cloudflare Worker → Settings → Variables):
 *   SLACK_SIGNING_SECRET
 *   GITHUB_TOKEN
 *   GITHUB_OWNER
 *   GITHUB_REPO
 *   WORKFLOW_FILE
 *   NVIDIA_API_KEY        (required for /leads ask)
 */

const QUERIES_PATH = "queries.txt";
const NVIDIA_MODEL = "meta/llama-3.3-70b-instruct";

addEventListener("fetch", (event) => {
  event.respondWith(handleRequest(event));
});

async function handleRequest(event) {
  const request = event.request;
  if (request.method !== "POST") {
    return new Response("XV Lead Bot — POST slash commands here.", { status: 200 });
  }

  const bodyText = await request.text();

  const valid = await verifySlack(request, bodyText, SLACK_SIGNING_SECRET);
  if (!valid) return new Response("Invalid signature", { status: 401 });

  const params = new URLSearchParams(bodyText);
  const text = (params.get("text") || "").trim();
  const parts = text.split(/\s+/);
  const cmd = parts[0] || "help";
  const arg = parts.slice(1).join(" ").trim();
  const responseUrl = params.get("response_url") || "";

  try {
    let responseText;
    switch (cmd.toLowerCase()) {
      case "list":    responseText = await listQueries(); break;
      case "add":     responseText = await addQuery(arg); break;
      case "remove":  responseText = await removeQuery(arg); break;
      case "run":     responseText = await runWorkflow(); break;
      case "status":  responseText = await runStatus(); break;
      case "approve": responseText = await approveEmails(); break;
      case "skip":    responseText = await skipEmails(); break;
      case "ask":     responseText = askLlm(arg, responseUrl, event); break;
      default:        responseText = helpText(); break;
    }
    return slackJson(responseText);
  } catch (err) {
    return slackJson(":warning: Error: " + (err.message || String(err)));
  }
}

// ——— Slack helpers ———

function slackJson(text) {
  return new Response(
    JSON.stringify({ response_type: "in_channel", text }),
    { headers: { "Content-Type": "application/json" } }
  );
}

async function verifySlack(request, body, secret) {
  const ts = request.headers.get("x-slack-request-timestamp");
  const sig = request.headers.get("x-slack-signature");
  if (!ts || !sig) return false;
  if (Math.abs(Date.now() / 1000 - Number(ts)) > 300) return false;

  const base = "v0:" + ts + ":" + body;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(base));
  const hex = Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  const expected = "v0=" + hex;
  if (expected.length !== sig.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
  return diff === 0;
}

// ——— GitHub API ———

async function ghFetch(path, init) {
  init = init || {};
  const res = await fetch("https://api.github.com" + path, {
    ...init,
    headers: Object.assign({
      Authorization: "Bearer " + GITHUB_TOKEN,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "xv-lead-bot",
    }, init.headers || {}),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error("GitHub " + res.status + ": " + body.slice(0, 200));
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

async function getQueriesFile() {
  const data = await ghFetch(
    "/repos/" + GITHUB_OWNER + "/" + GITHUB_REPO + "/contents/" + QUERIES_PATH
  );
  const bytes = Uint8Array.from(
    atob(data.content.replace(/\n/g, "")),
    (c) => c.charCodeAt(0)
  );
  const content = new TextDecoder().decode(bytes);
  return { sha: data.sha, content };
}

async function writeQueriesFile(newContent, message) {
  const { sha } = await getQueriesFile();
  const b64 = btoa(
    new TextEncoder().encode(newContent)
      .reduce((s, b) => s + String.fromCharCode(b), "")
  );
  return ghFetch(
    "/repos/" + GITHUB_OWNER + "/" + GITHUB_REPO + "/contents/" + QUERIES_PATH,
    {
      method: "PUT",
      body: JSON.stringify({ message, content: b64, sha }),
    }
  );
}

// ——— Commands ———

async function listQueries() {
  const { content } = await getQueriesFile();
  const lines = content.split("\n").map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return "No queries configured.";
  return ":mag: *Current queries* (" + lines.length + "):\n" +
    lines.map((l, i) => (i + 1) + ". " + l).join("\n");
}

async function addQuery(q) {
  if (!q) return "Usage: `/leads add <query>`  e.g. `/leads add electrician Tampa`";
  const { content } = await getQueriesFile();
  const lines = content.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.some((l) => l.toLowerCase() === q.toLowerCase())) {
    return "Already in the list: `" + q + "`";
  }
  lines.push(q);
  await writeQueriesFile(lines.join("\n") + "\n", 'slack: add "' + q + '"');
  return ":white_check_mark: Added: `" + q + "`  (" + lines.length + " queries total)";
}

async function removeQuery(q) {
  if (!q) return "Usage: `/leads remove <query>`";
  const { content } = await getQueriesFile();
  const lines = content.split("\n").map((l) => l.trim()).filter(Boolean);
  const idx = lines.findIndex((l) => l.toLowerCase() === q.toLowerCase());
  if (idx === -1) {
    return "Not found: `" + q + "`. Use `/leads list` to see current queries.";
  }
  lines.splice(idx, 1);
  await writeQueriesFile(
    lines.join("\n") + (lines.length ? "\n" : ""),
    'slack: remove "' + q + '"'
  );
  return ":wastebasket: Removed: `" + q + "`  (" + lines.length + " queries total)";
}

async function runWorkflow() {
  await ghFetch(
    "/repos/" + GITHUB_OWNER + "/" + GITHUB_REPO +
    "/actions/workflows/" + WORKFLOW_FILE + "/dispatches",
    { method: "POST", body: JSON.stringify({ ref: "main" }) }
  );
  return ":rocket: Scrape triggered — running now. You'll get a Slack notification when it's done.";
}

async function runStatus() {
  const data = await ghFetch(
    "/repos/" + GITHUB_OWNER + "/" + GITHUB_REPO + "/actions/runs?per_page=1"
  );
  const run = data.workflow_runs && data.workflow_runs[0];
  if (!run) return "No runs yet.";
  const icon = run.status === "completed"
    ? (run.conclusion === "success" ? ":white_check_mark:" : ":x:")
    : ":hourglass_flowing_sand:";
  const started = new Date(run.created_at).toLocaleString("en-US", {
    timeZone: "America/New_York",
  });
  return icon + " *Last run*: " + run.status +
    (run.conclusion ? " (" + run.conclusion + ")" : "") + "\n" +
    "   Started: " + started + " ET\n" +
    "   <" + run.html_url + "|View full run>";
}

async function approveEmails() {
  await ghFetch(
    "/repos/" + GITHUB_OWNER + "/" + GITHUB_REPO +
    "/actions/workflows/send_emails.yml/dispatches",
    { method: "POST", body: JSON.stringify({ ref: "main" }) }
  );
  return ":white_check_mark: Approved! Sending emails now — you'll get a confirmation when done.";
}

async function skipEmails() {
  return ":no_entry_sign: Skipped. No emails will be sent this week. Leads are still in your sheet.";
}

// ——— /leads ask — chat with Llama 3.3 70B ———
//
// Slack requires a slash-command response within 3 seconds, but the LLM call
// usually takes 5–15s. We reply immediately with "Thinking…", then post the
// real answer back via the response_url after the fetch resolves.

function askLlm(question, responseUrl, event) {
  if (!question) {
    return "Usage: `/leads ask <your question>`  e.g. `/leads ask write me a follow-up email to a plumber`";
  }
  if (typeof NVIDIA_API_KEY === "undefined" || !NVIDIA_API_KEY) {
    return ":warning: Cloudflare Worker is missing `NVIDIA_API_KEY`. Add it in Worker → Settings → Variables.";
  }
  if (!responseUrl) {
    return ":warning: No response_url from Slack — async reply not possible.";
  }

  event.waitUntil(answerInBackground(question, responseUrl));
  return ":thought_balloon: Thinking…";
}

async function answerInBackground(question, responseUrl) {
  try {
    const llmResp = await fetch("https://integrate.api.nvidia.com/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + NVIDIA_API_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: NVIDIA_MODEL,
        messages: [
          {
            role: "system",
            content: "You are a helpful AI assistant for Ryan Krauss, founder of XV Connects (a web-design agency for local trade businesses). Be concise, direct, and useful. Avoid filler. Use plain text — Slack supports basic markdown (asterisks for bold, backticks for code), but not headings or tables.",
          },
          { role: "user", content: question },
        ],
        temperature: 0.7,
        max_tokens: 800,
      }),
    });
    if (!llmResp.ok) {
      const errText = await llmResp.text();
      await postToSlack(responseUrl, ":warning: NVIDIA error " + llmResp.status + ": " + errText.slice(0, 300));
      return;
    }
    const data = await llmResp.json();
    const reply = (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content || "").trim();
    await postToSlack(responseUrl, reply || "(empty response)");
  } catch (err) {
    await postToSlack(responseUrl, ":warning: LLM call failed: " + (err.message || String(err)));
  }
}

async function postToSlack(responseUrl, text) {
  return fetch(responseUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ response_type: "in_channel", text, replace_original: true }),
  });
}

function helpText() {
  return [
    "*XV Lead Agent commands:*",
    "`/leads list` — show current search queries",
    "`/leads add <query>` — add a search (e.g. `/leads add electrician Tampa`)",
    "`/leads remove <query>` — remove a search",
    "`/leads run` — trigger a scrape right now",
    "`/leads status` — show status of the most recent run",
    "`/leads approve` — send emails to this week's leads",
    "`/leads skip` — skip emailing this week",
    "`/leads ask <question>` — chat with Llama 3.3 70B",
    "`/leads help` — this message",
  ].join("\n");
}
