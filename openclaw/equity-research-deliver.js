/**
 * OpenClaw cron script payload for the 08:20 ET Research Desk delivery.
 *
 * Reads state written by equity-research-build.js (or fetches the published
 * brief). Never uses Intl — QuickJS-WASI code-mode does not define it.
 * `notify` must be the delivered text string (not a boolean).
 *
 *   openclaw cron edit equity-research-deliver \
 *     --tz America/New_York \
 *     --cron "20 8 * * 1-5" \
 *     --clear-trigger \
 *     --script openclaw/equity-research-deliver.js
 */

const BRIEF_URL =
  "https://bskthefirst.github.io/should-i-buy-gpix-today/research-brief.json";

function httpText(result) {
  if (result == null) return "";
  if (typeof result === "string") return result;
  const details = result.details || result.result || result;
  if (typeof details === "string") return details;
  if (details && typeof details.text === "string") return details.text;
  if (details && typeof details.body === "string") return details.body;
  if (details && typeof details.content === "string") return details.content;
  try {
    return JSON.stringify(details);
  } catch (_) {
    return String(result);
  }
}

async function fetchBrief() {
  const attempts = [
    ["web_fetch", { url: BRIEF_URL }],
    ["http", { method: "GET", url: BRIEF_URL }],
    ["exec", { command: `curl -fsSL ${BRIEF_URL}` }],
  ];
  let lastErr = null;
  for (const [tool, args] of attempts) {
    try {
      const res = await tools.call(tool, args);
      const raw = httpText(res);
      const start = raw.indexOf("{");
      const end = raw.lastIndexOf("}");
      if (start < 0 || end < start) {
        lastErr = "no JSON object in tool response";
        continue;
      }
      return JSON.parse(raw.slice(start, end + 1));
    } catch (err) {
      lastErr = err && err.message ? err.message : String(err);
    }
  }
  throw new Error("could not fetch research-brief.json: " + lastErr);
}

function isFresh(brief, nowMs) {
  if (!brief || brief.status !== "ready" || brief.deliverable !== true) {
    return false;
  }
  if (brief.thesis_status && brief.thesis_status !== "verified") {
    return false;
  }
  const generatedMs =
    typeof brief.generated_at_ms === "number"
      ? brief.generated_at_ms
      : Date.parse(brief.generated_at);
  if (!Number.isFinite(generatedMs)) return false;
  const maxAge =
    typeof brief.fresh_max_age_ms === "number"
      ? brief.fresh_max_age_ms
      : 18 * 60 * 60 * 1000;
  const age = nowMs - generatedMs;
  return age >= 0 && age <= maxAge;
}

function withheld(asOfEt, reason) {
  const when = asOfEt ? asOfEt + " ET" : "08:20 ET";
  return (
    "RESEARCH DESK — REPORT WITHHELD\n\n" +
    "The " +
    when +
    " report was not delivered because the available report is " +
    (reason || "stale or unfinished") +
    ". No stale or unverified thesis was substituted."
  );
}

const prior = trigger && trigger.state ? trigger.state : null;
const nowMs = Date.now();

if (prior && prior.lastBuildOk === false) {
  return {
    notify: withheld(prior.asOfEt, prior.reason || "stale or unfinished"),
    state: {
      delivered: false,
      reason: prior.reason || "stale_or_unfinished",
      asOfEt: prior.asOfEt || null,
      checkedAtMs: nowMs,
    },
  };
}

let text = prior && prior.lastBuildOk ? prior.text : null;
let asOfEt = prior && prior.asOfEt;

if (!text) {
  let brief;
  try {
    brief = await fetchBrief();
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    return {
      notify: withheld(null, "unreachable (" + msg + ")"),
      state: {
        delivered: false,
        reason: "fetch_failed",
        checkedAtMs: nowMs,
      },
    };
  }
  if (!isFresh(brief, nowMs)) {
    return {
      notify: withheld(brief.as_of_et, "stale or unfinished"),
      state: {
        delivered: false,
        reason: "stale_or_unfinished",
        asOfEt: brief.as_of_et || null,
        checkedAtMs: nowMs,
      },
    };
  }
  text = brief.text;
  asOfEt = brief.as_of_et;
}

return {
  notify: text,
  state: {
    delivered: true,
    asOfEt: asOfEt || null,
    deliveredAtMs: nowMs,
  },
};
