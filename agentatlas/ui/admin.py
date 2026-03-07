def render_admin_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentAtlas Admin</title>
  <link rel="stylesheet" href="/static/admin.css">
</head>
<body>
  <header>
    <h1>AgentAtlas Admin</h1>
    <p>Review queue, tenant benchmark health, audit trail, and private/public route diffs.</p>
  </header>
  <main>
    <section class="stack">
      <div class="panel stack">
        <div>
          <label for="apiKey">API Key</label>
          <input id="apiKey" placeholder="X-API-Key">
        </div>
        <div>
          <label for="tenantId">Tenant ID</label>
          <input id="tenantId" placeholder="tenant-a">
        </div>
        <div>
          <label for="reviewer">Reviewer</label>
          <input id="reviewer" placeholder="ops@agentatlas.ai">
        </div>
        <div>
          <label for="suiteName">Benchmark Suite</label>
          <input id="suiteName" value="warm_start_reliability">
        </div>
        <div>
          <label for="registryScope">Registry Scope</label>
          <select id="registryScope">
            <option value="public">public</option>
            <option value="private">private</option>
            <option value="auto" selected>auto</option>
          </select>
        </div>
        <button id="refreshAll">Refresh Dashboard</button>
      </div>
      <div class="panel stack">
        <div>
          <label for="diffSite">Diff Site</label>
          <input id="diffSite" placeholder="github.com">
        </div>
        <div>
          <label for="diffUrl">Diff URL</label>
          <input id="diffUrl" placeholder="https://github.com/login">
        </div>
        <div>
          <label for="diffTask">Task Key</label>
          <input id="diffTask" value="generic_extract">
        </div>
        <button id="loadDiff" class="secondary">Load Route Diff</button>
      </div>
    </section>
    <section class="grid">
      <div class="panel">
        <h2>Benchmark Dashboard</h2>
        <div id="dashboardCards" class="cards"></div>
        <div id="dashboardCategories"></div>
      </div>
      <div class="panel">
        <h2>Review Queue</h2>
        <div id="reviewCards" class="cards"></div>
        <div id="reviewQueue"></div>
      </div>
      <div class="panel">
        <h2>Audit Trail</h2>
        <div id="auditTrail"></div>
      </div>
      <div class="panel">
        <h2>Route Diff</h2>
        <div id="routeDiff"></div>
      </div>
    </section>
  </main>
  <script src="/static/admin.js"></script>
</body>
</html>"""
