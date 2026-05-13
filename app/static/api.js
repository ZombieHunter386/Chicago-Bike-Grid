// Thin fetch wrappers for backend endpoints. All POSTs use JSON bodies
// (spec §3.8: no coordinates in query strings).
//
// Throws { status, body } on non-2xx so the UI can branch on status codes.

async function postJson(path, body) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok) throw { status: resp.status, body: json };
  return json;
}

export async function geocode(address) {
  return postJson("/geocode", { address });
}

// Type-ahead suggestions (up to 5 results). Distinct from geocode()
// because /geocode/suggest returns {results: [...]} and never 404s on
// empty input — short queries just return an empty list.
export async function geocodeSuggest(address) {
  const resp = await postJson("/geocode/suggest", { address });
  return Array.isArray(resp.results) ? resp.results : [];
}

export async function fetchRoutes(home, dest, tier) {
  return postJson("/routes", { home, dest, tier });
}

export async function fetchPois(near, category) {
  return postJson("/pois", { near, category });
}

export async function fetchTreatment(slug) {
  const resp = await fetch(`/treatments/${encodeURIComponent(slug)}`);
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok) throw { status: resp.status, body: json };
  return json;
}

// Submit gap-analysis. If cache hit → {status: "ready", result}. If miss →
// {status: "running", job_id}; we then poll /gap-analysis/status every
// 1500ms until status="ready" or "error", capped at 60s (40 polls).
//
// Transparent retry on 429: the backend's rolling 60/min window can briefly
// reject bursts (rapid tier toggle, multi-dest add). Rather than fail the
// dest entirely, wait long enough for the window to free up and retry once.
// If still rate-limited, propagate the 429 so the UI surfaces it.
export async function fetchGapAnalysis(home, dest, tier) {
  let submit;
  try {
    submit = await postJson("/gap-analysis", { home, dest, tier });
  } catch (err) {
    if (err && err.status === 429) {
      // 6s is enough to age out the oldest request in a tight burst. Cheaper
      // than 60s + a slot opens as soon as one prior request rolls off.
      await new Promise((r) => setTimeout(r, 6000));
      submit = await postJson("/gap-analysis", { home, dest, tier });
    } else {
      throw err;
    }
  }
  if (submit.status === "ready") return submit.result;
  if (submit.status !== "running") throw { status: 500, body: submit };

  const jobId = submit.job_id;
  // 120s budget — long cross-town trips at parent/kid tier can enumerate
  // 20+ named streets, and although per-road marginals run in parallel on
  // the backend (gap_analysis._marginal_pool), the total wall-clock for a
  // big corridor still adds up. The previous 60s cap timed out genuine
  // long trips and surfaced as "savings stopped calculating" in the UI.
  for (let i = 0; i < 80; i += 1) {
    await new Promise((r) => setTimeout(r, 1500));
    const resp = await fetch(
      `/gap-analysis/status?job=${encodeURIComponent(jobId)}`,
    );
    const json = await resp.json().catch(() => ({}));
    if (resp.ok && json.status === "ready") return json.result;
    if (json.status === "error") throw { status: 500, body: json };
  }
  throw { status: 504, body: { error: "gap-analysis timed out after 120s" } };
}
