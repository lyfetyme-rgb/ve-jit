async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(JSON.stringify(data));
  return data;
}

async function loadHealth() {
  const data = await fetchJson("/health");
  document.getElementById("healthOutput").textContent = JSON.stringify(data, null, 2);
  document.getElementById("healthBadge").textContent = data.status;
}

async function loadQueue() {
  const data = await fetchJson("/api/v1/queue/recommendation");
  document.getElementById("queueOutput").textContent = JSON.stringify(data, null, 2);
}

async function loadJit() {
  const data = await fetchJson("/api/v1/jit/demo");
  document.getElementById("jitOutput").textContent = JSON.stringify(data, null, 2);
}

async function listInventory() {
  const data = await fetchJson("/api/v1/inventory");
  document.getElementById("inventoryOutput").textContent = JSON.stringify(data, null, 2);
}

async function createDemoSku() {
  const payload = {
    sku: "DAIRY-001",
    name: "Whole Milk Gallon",
    quantity: 45,
    reorder_point: 40,
    unit_cost: 2.5
  };
  try {
    await fetchJson("/api/v1/inventory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  } catch (err) {
    // Ignore duplicate SKU conflicts, then refresh.
  }
  await listInventory();
}

async function normalizeDemoEvent() {
  const payload = {
    device_id: "tc52-014",
    sku: "DAIRY-001",
    quantity: 1,
    store_id: "store_001",
    timestamp: new Date().toISOString()
  };
  const data = await fetchJson("/api/v1/iot/normalize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  document.getElementById("iotOutput").textContent = JSON.stringify(data, null, 2);
}

document.getElementById("seedBtn").addEventListener("click", createDemoSku);
document.getElementById("listBtn").addEventListener("click", listInventory);
document.getElementById("iotBtn").addEventListener("click", normalizeDemoEvent);
document.getElementById("planesBtn").addEventListener("click", loadPlanes);
document.getElementById("edgeBtn").addEventListener("click", loadEdge);
document.getElementById("recsBtn").addEventListener("click", loadRecommendations);
document.getElementById("anomaliesBtn").addEventListener("click", loadAnomalies);
loadPci().catch(console.error);

async function loadPci() {
  const data = await fetchJson("/api/v1/compliance/pci-scope");
  const el = document.getElementById("pciOutput");
  if (!el) return;
  el.innerHTML = `
    <div class="pci-badge ok">PCI SCOPE: OUT OF SCOPE</div>
    <div class="pci-row"><span>Processes</span><b>non-PCI SKU / inventory / lane telemetry only</b></div>
    <div class="pci-row"><span>Rejects</span><b>any PAN / CVV / track data (HTTP 422, pre-parser)</b></div>
    <div class="pci-row"><span>Sandbox</span><b>systemd: MemoryMax · NoNewPrivileges · AF_UNIX-only · ProtectSystem=strict</b></div>
    <div class="pci-row"><span>Audit</span><b>append-only hash-chained events + inventory ledger</b></div>
    <div class="pci-row"><span>Policy</span><b>Ed25519-signed bundles · SAFE_MODE on invariant failure</b></div>
    <div class="plane-rule">${data.qsa_note} <a href="docs/PCI_SCOPE.md">Full scope document</a></div>`;
}

async function loadEdge() {
  const data = await fetchJson("/api/v1/edge/status");
  const el = document.getElementById("edgeOutput");
  if (!el) return;
  const c = data.continuity;
  const rows = Object.entries(c.components)
    .map(([k, v]) => `<div class="edge-comp"><span>${k}</span><b>${v.score === null ? "not measured" : v.score}</b></div>`)
    .join("");
  el.innerHTML = `
    <div class="edge-state ${c.state === "ONLINE" ? "ok" : c.state === "SAFE_MODE" ? "bad" : "warn"}">${c.state}</div>
    <div class="edge-value">${c.value.toFixed(1)}<span> / 100 continuity value</span></div>
    <div class="edge-comps">${rows}</div>
    <div class="plane-rule">Outbox pending: ${data.outbox_pending} · Cloud sync: ${data.cloud_sync_configured ? "configured" : "dry-run (not configured)"} · Approved skills: ${data.approved_skills}</div>`;
}

async function loadPlanes() {
  const data = await fetchJson("/api/v1/planes");
  const el = document.getElementById("planesOutput");
  if (!el) return;
  el.innerHTML = data.planes
    .map(
      (p) => `
      <div class="plane">
        <h3>${p.name}</h3>
        <p class="plane-owns">Owns: ${p.owns} — ${p.primary_responsibility}</p>
        <ul>${p.capabilities.map((c) => `<li>${c}</li>`).join("")}</ul>
        <p class="plane-note">${p.executes_business_actions ? "Executes authorized business actions" : "Produces recommendations; never executes actions"}</p>
      </div>`
    )
    .join("")
    + `<p class="plane-rule">${data.governance_rule}</p>`;
}

async function loadRecommendations() {
  const data = await fetchJson("/api/v1/intelligence/recommendations?status=PROPOSED&limit=10");
  const el = document.getElementById("recsOutput");
  if (el) {
    el.textContent = data.recommendations.length
      ? JSON.stringify(data.recommendations.map((r) => ({ uid: r.uid, kind: r.kind, subject: r.subject_id, summary: r.summary, confidence: r.confidence })), null, 2)
      : "No proposed recommendations.";
  }
}

async function loadAnomalies() {
  const data = await fetchJson("/api/v1/intelligence/anomalies");
  const el = document.getElementById("anomaliesOutput");
  if (el) el.textContent = JSON.stringify(data.anomalies.slice(0, 15), null, 2);
}

Promise.all([loadHealth(), loadQueue(), loadJit(), listInventory(), loadPlanes(), loadEdge()]).catch(err => {
  console.error(err);
});

async function loadNetworkInsights() {
  const data = await fetchJson("/api/v1/network/insights");
  const el = document.getElementById("networkOutput");
  if (el) el.textContent = JSON.stringify(data, null, 2);
}

async function loadPercentile() {
  const data = await fetchJson("/api/v1/network/percentile");
  const el = document.getElementById("percentileOutput");
  if (el) el.textContent = JSON.stringify(data, null, 2);
}

loadNetworkInsights().catch(console.error);
loadPercentile().catch(console.error);
