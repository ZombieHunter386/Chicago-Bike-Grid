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
export async function fetchGapAnalysis(home, dest, tier) {
  const submit = await postJson("/gap-analysis", { home, dest, tier });
  if (submit.status === "ready") return submit.result;
  if (submit.status !== "running") throw { status: 500, body: submit };

  const jobId = submit.job_id;
  for (let i = 0; i < 40; i += 1) {
    await new Promise((r) => setTimeout(r, 1500));
    const resp = await fetch(
      `/gap-analysis/status?job=${encodeURIComponent(jobId)}`,
    );
    const json = await resp.json().catch(() => ({}));
    if (resp.ok && json.status === "ready") return json.result;
    if (json.status === "error") throw { status: 500, body: json };
  }
  throw { status: 504, body: { error: "gap-analysis timed out after 60s" } };
}
