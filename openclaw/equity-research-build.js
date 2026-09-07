/**
 * OpenClaw cron script payload for "equity-research-build".
 *
 * WHY THIS EXISTS
 * The previous trigger/payload used `Intl.DateTimeFormat` for ET clocks.
 * OpenClaw code-mode runs in QuickJS-WASI where `Intl` is undefined, so the
 * job failed with:
 *   ReferenceError: Intl is not defined at (openclaw-code-mode:user.js:…)
 * After five failures the 08:20 ET desk withheld the report as stale.
 *
 * Do NOT use Intl, toLocaleString, or toLocaleDateString here.
 *
 * Install (from the gateway host, with this repo checked out):
 *   openclaw cron edit equity-research-build \
 *     --tz America/New_York \
 *     --cron "15 7 * * 1-5" \
 *     --clear-trigger \
 *     --script openclaw/equity-research-build.js
 *
 * Prefer a plain cron + --tz over a condition-trigger that formats dates.
 * `notify` must be a string (delivered text), not a boolean.
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

let brief;
try {
  brief = await fetchBrief();
} catch (err) {
  const msg = err && err.message ? err.message : String(err);
  return {
    notify:
      'Cron "equity-research-build" failed: could not load research brief (' +
      msg +
      ").",
    state: {
      lastBuildOk: false,
      reason: "fetch_failed",
      checkedAtMs: Date.now(),
    },
  };
}

const nowMs = Date.now();
if (!isFresh(brief, nowMs)) {
  return {
    notify:
      'Cron "equity-research-build" — brief not ready (stale or unfinished). ' +
      "Research Desk will withhold rather than invent a thesis.",
    state: {
      lastBuildOk: false,
      reason: "stale_or_unfinished",
      asOfEt: brief.as_of_et || null,
      checkedAtMs: nowMs,
    },
  };
}

// Silent success: persist verified brief text for the 08:20 deliver job.
return {
  state: {
    lastBuildOk: true,
    asOfEt: brief.as_of_et,
    generatedAtMs: brief.generated_at_ms || Date.parse(brief.generated_at),
    thesisStatus: brief.thesis_status,
    text: brief.text,
    checkedAtMs: nowMs,
  },
};
