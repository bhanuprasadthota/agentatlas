const els = {
  apiKey: document.getElementById('apiKey'),
  tenantId: document.getElementById('tenantId'),
  reviewer: document.getElementById('reviewer'),
  suiteName: document.getElementById('suiteName'),
  registryScope: document.getElementById('registryScope'),
  dashboardCards: document.getElementById('dashboardCards'),
  dashboardCategories: document.getElementById('dashboardCategories'),
  reviewQueue: document.getElementById('reviewQueue'),
  auditTrail: document.getElementById('auditTrail'),
  routeDiff: document.getElementById('routeDiff'),
  diffSite: document.getElementById('diffSite'),
  diffUrl: document.getElementById('diffUrl'),
  diffTask: document.getElementById('diffTask'),
  refreshAll: document.getElementById('refreshAll'),
  loadDiff: document.getElementById('loadDiff'),
};

function headers() {
  const output = {};
  if (els.apiKey.value.trim()) output['X-API-Key'] = els.apiKey.value.trim();
  if (els.tenantId.value.trim()) output['X-Tenant-ID'] = els.tenantId.value.trim();
  return output;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...headers(),
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || 'Request failed');
  return payload;
}

function renderTable(container, rows, columns) {
  if (!rows.length) {
    container.innerHTML = '<p>No data.</p>';
    return;
  }
  const head = columns.map(col => `<th>${col.label}</th>`).join('');
  const body = rows.map(row => `<tr>${columns.map(col => `<td>${col.render ? col.render(row) : (row[col.key] ?? '')}</td>`).join('')}</tr>`).join('');
  container.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

async function loadDashboard() {
  const suiteName = encodeURIComponent(els.suiteName.value.trim() || 'warm_start_reliability');
  const data = await api(`/v1/benchmarks/dashboard?suite_name=${suiteName}&limit=10`);
  els.dashboardCards.innerHTML = `
    <div class="card"><div class="metric">${data.run_count}</div><div>Runs</div></div>
    <div class="card"><div class="metric">${data.latest_status}</div><div>Latest Status</div></div>
    <div class="card"><div class="metric">${(data.warm_hit_rate_trend.at(-1) ?? 0)}</div><div>Warm Hit Rate</div></div>
    <div class="card"><div class="metric">${(data.healthy_count_trend.at(-1) ?? 0)}</div><div>Healthy Workflows</div></div>
  `;
  const categoryRows = Object.entries(data.categories || {}).map(([name, value]) => ({ name, ...value }));
  renderTable(els.dashboardCategories, categoryRows, [
    { key: 'name', label: 'Category' },
    { key: 'workflow_count', label: 'Workflows' },
    { key: 'healthy_count', label: 'Healthy' },
    { key: 'degraded_count', label: 'Degraded' },
    { key: 'failed_count', label: 'Failed' },
    { key: 'warm_hit_rate', label: 'Warm Hit Rate' },
  ]);
}

async function loadQueue() {
  const scope = encodeURIComponent(els.registryScope.value);
  const queue = await api(`/v1/review/queue?registry_scope=${scope}&limit=50`);
  renderTable(els.reviewQueue, queue.queue || [], [
    { key: 'site', label: 'Site' },
    { key: 'route_key', label: 'Route' },
    { key: 'review_reason', label: 'Reason' },
    { key: 'confidence', label: 'Confidence' },
    { key: 'playbook_id', label: 'Actions', render: row => `
      <button data-action="approve" data-playbook="${row.playbook_id}">Approve</button>
      <button class="warn" data-action="reject" data-playbook="${row.playbook_id}">Reject</button>
    ` },
  ]);
}

async function loadAudit() {
  const scope = encodeURIComponent(els.registryScope.value);
  const data = await api(`/v1/review/audit?registry_scope=${scope}&limit=50`);
  renderTable(els.auditTrail, data.audit || [], [
    { key: 'timestamp', label: 'Time' },
    { key: 'site', label: 'Site' },
    { key: 'reviewer', label: 'Reviewer' },
    { key: 'reviewer_role', label: 'Role' },
    { key: 'action', label: 'Action' },
    { key: 'notes', label: 'Notes' },
  ]);
}

async function loadDiff() {
  const payload = {
    site: els.diffSite.value.trim(),
    url: els.diffUrl.value.trim(),
    task_key: els.diffTask.value.trim() || 'generic_extract',
  };
  const data = await api('/v1/review/diff', { method: 'POST', body: JSON.stringify(payload) });
  els.routeDiff.innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const playbookId = button.dataset.playbook;
  const approved = button.dataset.action === 'approve';
  try {
    await api('/v1/review/promote', {
      method: 'POST',
      body: JSON.stringify({
        playbook_id: playbookId,
        reviewer: els.reviewer.value.trim(),
        approved,
        notes: approved ? 'Approved from admin UI.' : 'Rejected from admin UI.',
      }),
    });
    await Promise.all([loadQueue(), loadAudit()]);
  } catch (error) {
    alert(error.message);
  }
});

els.refreshAll.addEventListener('click', async () => {
  try { await Promise.all([loadDashboard(), loadQueue(), loadAudit()]); }
  catch (error) { alert(error.message); }
});
els.loadDiff.addEventListener('click', async () => {
  try { await loadDiff(); }
  catch (error) { alert(error.message); }
});
