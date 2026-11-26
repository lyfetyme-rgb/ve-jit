// Store Console — same-origin by default; config.js can inject an API base for static hosting.
// eslint-disable-next-line no-undef
const API_BASE = (typeof window !== 'undefined' && window.VEJIT_API_BASE) ? window.VEJIT_API_BASE.replace(/\/+$/, '') : '';

function api(path) {
  if (/^https?:\/\//i.test(path)) return path;
  return API_BASE + path;
}

async function json(path, options) {
  const response = await fetch(api(path), options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail ? JSON.stringify(body.detail) : JSON.stringify(body));
  return body;
}

const state = { tenants: [], stores: [], tenantId: '', storeId: '', actor: 'store-manager', view: 'overview' };
const $ = (id) => document.getElementById(id);

async function boot() {
  const catalog = await json('/api/v1/console/catalog');
  state.tenants = catalog.tenants;
  state.stores = catalog.stores;
  $('tenantSelect').innerHTML = state.tenants.map(t => `<option value="${t.tenant_id}">${t.name} (${t.vertical})</option>`).join('');
  state.tenantId = state.tenants[0]?.tenant_id || '';
  $('tenantSelect').value = state.tenantId;
  applyTenant();
  wire();
  await refreshAll();
}

function applyTenant() {
  const stores = state.stores.filter(s => s.tenant_id === state.tenantId);
  $('storeSelect').innerHTML = stores.map(s => `<option value="${s.store_id}">${s.name}</option>`).join('');
  state.storeId = stores[0]?.store_id || '';
  updateArch();
}

function updateArch() {
  const store = state.stores.find(s => s.store_id === state.storeId);
  $('storeArch').textContent = store ? `${store.architecture} · ${store.channel}` : '—';
}

function wire() {
  $('tenantSelect').addEventListener('change', (e) => { state.tenantId = e.target.value; applyTenant(); refreshAll(); });
  $('storeSelect').addEventListener('change', (e) => { state.storeId = e.target.value; updateArch(); refreshAll(); });
  $('actorInput').addEventListener('change', (e) => { state.actor = e.target.value || 'store-manager'; });
  document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    tab.classList.add('active');
    state.view = tab.dataset.view;
    $('view-' + state.view).classList.add('active');
    if (state.view === 'sales') loadOrders();
  }));
  $('refreshInventory').addEventListener('click', loadInventory);
  $('refreshRecs').addEventListener('click', loadRecommendations);
  $('refreshAudit').addEventListener('click', loadAudit);
  $('addLine').addEventListener('click', addSaleLine);
  $('submitSale').addEventListener('click', submitSale);
}

async function refreshAll() {
  await Promise.all([loadOverview(), loadInventory(), loadRecommendations(), loadAudit(), loadContext()]);
}

function scoped() { return `tenant_id=${encodeURIComponent(state.tenantId)}&store_id=${encodeURIComponent(state.storeId)}`; }

async function loadOverview() {
  const d = await json('/api/v1/console/overview?' + scoped());
  $('kpis').innerHTML = [
    ['SKUs', d.skus], ['On hand units', d.on_hand_units], ['Low stock', d.low_stock_skus],
    ['Open recs', d.open_recommendations], ['Orders', d.orders], ['Sales total', '$' + d.sales_total.toFixed(2)],
  ].map(([label, value]) => `<div class="kpi"><span>${label}</span><strong>${value}</strong></div>`).join('');
  $('govRule').textContent = d.governance_rule;
}

async function loadInventory() {
  const d = await json('/api/v1/console/inventory?' + scoped());
  const tbody = $('inventoryTable').querySelector('tbody');
  tbody.innerHTML = d.items.map(i => `<tr><td>${i.sku}</td><td>${i.name}</td><td>${i.on_hand}</td><td>${i.reorder_point}</td><td><span class="pill ${i.status.toLowerCase()}">${i.status}</span></td></tr>`).join('') || '<tr><td colspan="5">No items</td></tr>';
}

async function loadRecommendations() {
  const d = await json('/api/v1/console/recommendations?' + scoped());
  $('recList').innerHTML = d.recommendations.map(r => {
    const canApprove = r.status === 'PROPOSED';
    const canExecute = r.status === 'APPROVED';
    return `<div class="rec"><div><strong>${r.uid}</strong> · ${r.kind} · <span class="pill ${r.status.toLowerCase()}">${r.status}</span></div>
      <p>${r.summary}</p><p class="muted">confidence ${(r.confidence * 100).toFixed(0)}% · approved by ${r.approved_by || '—'} · executed by ${r.executed_by || '—'}</p>
      <div>${canApprove ? `<button data-approve="${r.uid}">Approve</button>` : ''}
      ${canExecute ? `<button data-execute="${r.uid}">Execute (Operations)</button>` : ''}</div></div>`;
  }).join('') || '<p class="muted">No recommendations for this store.</p>';

  $('recList').querySelectorAll('[data-approve]').forEach(b => b.addEventListener('click', async () => {
    await json(`/api/v1/console/recommendations/${b.dataset.approve}/approve`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ actor: state.actor, tenant_id: state.tenantId, note: 'Approved in Store Console' })
    });
    await loadRecommendations();
  }));
  $('recList').querySelectorAll('[data-execute]').forEach(b => b.addEventListener('click', async () => {
    await json(`/api/v1/console/recommendations/${b.dataset.execute}/execute`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ actor: state.actor, tenant_id: state.tenantId, note: 'Executed in Store Console' })
    });
    await Promise.all([loadRecommendations(), loadAudit(), loadInventory()]);
  }));
}

function addSaleLine() {
  const div = document.createElement('div');
  div.className = 'sale-line';
  div.innerHTML = '<input placeholder="SKU" value="" class="sku" /> <input type="number" min="1" value="1" class="qty" />';
  $('saleLines').appendChild(div);
}

async function submitSale() {
  const lines = [...document.querySelectorAll('.sale-line')].map(l => ({
    sku: l.querySelector('.sku').value.trim(), quantity: parseInt(l.querySelector('.qty').value, 10)
  })).filter(l => l.sku && l.quantity > 0);
  if (!lines.length) { $('saleResult').textContent = 'Add at least one line.'; return; }
  try {
    const d = await json('/api/v1/console/orders', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenant_id: state.tenantId, store_id: state.storeId, channel: 'pos', lines,
                             client_order_id: 'console-' + Date.now() })
    });
    $('saleResult').textContent = JSON.stringify(d, null, 2);
    $('saleLines').innerHTML = '';
    addSaleLine();
    await Promise.all([loadInventory(), loadOverview(), loadOrders()]);
  } catch (err) {
    $('saleResult').textContent = String(err.message || err);
  }
}

async function loadOrders() {
  try {
    const d = await json('/api/v1/console/audit?' + scoped());
    $('recentOrders').textContent = JSON.stringify(d.ledger.slice(0, 10), null, 2);
  } catch (err) { $('recentOrders').textContent = String(err.message || err); }
}

async function loadAudit() {
  const d = await json('/api/v1/console/audit?' + scoped());
  $('ledgerList').textContent = JSON.stringify(d.ledger.slice(0, 25), null, 2);
  $('actionList').textContent = JSON.stringify(d.authorized_actions, null, 2);
}

async function loadContext() {
  const [planes, pci, bengal] = await Promise.all([
    json('/api/v1/planes'), json('/api/v1/compliance/pci-scope'), json('/api/v1/assurance/bengal/status')
  ]);
  $('planes').textContent = JSON.stringify(planes.planes.map(p => ({ id: p.id, owns: p.owns, responsibility: p.primary_responsibility })), null, 2);
  $('assurance').textContent = JSON.stringify({
    pci_scope: pci.scope, pci_statement: pci.statement,
    bengal_method: bengal.method_version, bengal_protocol: bengal.protocol_version,
    bengal_observation_only: bengal.observation_only
  }, null, 2);
  $('posture').innerHTML = `<p class="pill ok">PCI ${pci.scope}</p><p class="note">${pci.statement}</p>
    <p class="note">BENGAL ${bengal.method_version} · ${bengal.modes.length} modes · observation only</p>`;
}

addSaleLine();
boot().catch(err => { document.body.innerHTML = '<p class="error">Store Console failed to load: ' + String(err.message || err) + '</p>'; });
