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

Promise.all([loadHealth(), loadQueue(), loadJit(), listInventory()]).catch(err => {
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
