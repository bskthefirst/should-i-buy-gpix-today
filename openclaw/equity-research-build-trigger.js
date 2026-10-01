/**
 * OpenClaw CONDITION TRIGGER for equity-research-build.
 *
 * Keep this as --trigger-script (not --script). The job payload is still
 * agentTurn: that is what writes reports/*.pdf. Replacing the payload with
 * a Pages-brief script would restore Telegram text and kill PDF generation.
 *
 * QuickJS-WASI has no Intl, tools, or exec. Use read() + Date.parse.
 * JSON.parse of the research index previously threw
 * "Bad control character in string literal" when `read` wrapped the file;
 * fall back to regex extraction so a wrapper cannot withhold the desk.
 */
const INDEX_PATH =
  "/Users/billkim/.openclaw/workspace-researcher/data/research/index.json";

function asText(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    const textItem = value.find((item) => item && item.type === "text");
    if (textItem && typeof textItem.text === "string") return textItem.text;
    return "";
  }
  if (typeof value.text === "string") return value.text;
  return "";
}

function looksLikeIndex(value) {
  return (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    typeof value.generated_at === "string"
  );
}

function parseIndex(raw) {
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  const sliced = start >= 0 && end > start ? raw.slice(start, end + 1) : raw;
  try {
    return JSON.parse(
      sliced.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, " ")
    );
  } catch (_) {
    const generatedAt = (raw.match(/"generated_at"\s*:\s*"([^"]*)"/) || [])[1] || "";
    const countMatch = raw.match(/"candidate_count"\s*:\s*([0-9]+)/);
    return {
      generated_at: generatedAt,
      ok: /"ok"\s*:\s*true/.test(raw),
      candidate_count: countMatch ? Number(countMatch[1]) : null,
    };
  }
}

const res = await read({ path: INDEX_PATH });
const content = res && (res.content ?? (res.result && res.result.content) ?? res);

let index;
if (looksLikeIndex(content)) {
  index = content;
} else {
  const raw = asText(content) || asText(res) || String(content || "");
  if (!String(raw).trim()) {
    throw new Error("research index read returned no text");
  }
  index = parseIndex(String(raw));
}

const generatedAt = String(index.generated_at ?? "");
const generatedMs = Date.parse(generatedAt);
const ageMs = Date.now() - generatedMs;
const timestampValid = generatedMs === generatedMs;
const fresh = timestampValid && ageMs >= -300000 && ageMs <= 10800000;
const fire = index.ok === true && fresh;

json({
  fire,
  message: fire
    ? "Research index ready (" + generatedAt + ")"
    : "Research model skipped: index is stale or unhealthy (" +
      (generatedAt || "missing") +
      ")",
  state: {
    generated_at: generatedAt || null,
    age_minutes: timestampValid ? Math.round(ageMs / 60000) : null,
    ok: index.ok === true,
    candidate_count: index.candidate_count ?? null,
  },
});
