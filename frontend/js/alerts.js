/* alerts.js - Alert page = gallery of detection frames */

window.NetraAlerts = {
  _pollTimer: null,
  _config: null,
  _count: 0,
  _alerts: [],
  _selectedId: null,

  async load() {
    try {
      const res = await fetch('/alerts/config');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      this._config = await res.json();
      this.renderConfig();
      await this.refreshRecent();
      if (!this._pollTimer) {
        this._pollTimer = setInterval(() => this.refreshRecent(), 2000);
      }
    } catch (err) {
      const status = document.getElementById('alertsStatus');
      if (status) status.textContent = `Alert config unavailable: ${err.message}`;
    }
  },

  renderConfig() {
    const cfg = this._config || {};
    const base = document.getElementById('alertPublicBase');
    if (base) base.value = cfg.public_base_url || 'http://127.0.0.1:8000';

    const webhooks = cfg.webhooks || [];
    const urlInput = document.getElementById('alertWebhookUrl');
    if (urlInput && webhooks[0]) urlInput.value = webhooks[0].url || '';

    const lists = cfg.watchlists || [];
    const plateList = lists.find((w) => (w.type || 'plate') === 'plate') || lists[0];
    const ta = document.getElementById('alertWatchlistValues');
    if (ta && plateList) ta.value = (plateList.values || []).join(', ');
  },

  setBadge(n) {
    const badge = document.getElementById('alertBadge');
    if (!badge) return;
    if (n > 0) {
      badge.style.display = 'inline-block';
      badge.textContent = n > 99 ? '99+' : String(n);
    } else {
      badge.style.display = 'none';
    }
  },

  async savePublicBase() {
    const base = (document.getElementById('alertPublicBase')?.value || '').trim();
    const res = await fetch('/alerts/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ public_base_url: base || 'http://127.0.0.1:8000' }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    this._config = await res.json();
    this.renderConfig();
  },

  async saveWebhook() {
    await this.savePublicBase();
    const url = (document.getElementById('alertWebhookUrl')?.value || '').trim();
    if (!url) {
      alert('Enter a webhook URL');
      return;
    }
    const existing = (this._config?.webhooks || [])[0];
    const body = {
      id: existing?.id,
      name: existing?.name || 'Operator webhook',
      url,
      enabled: true,
      secret: existing?.secret || '',
      event_types: ['weapon', 'violence', 'anomaly'],
      min_confidence: 0,
    };
    const res = await fetch('/alerts/webhooks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    this._config = await res.json();
    this.renderConfig();
  },

  async testWebhook() {
    const url = (document.getElementById('alertWebhookUrl')?.value || '').trim() || undefined;
    const res = await fetch('/alerts/test-webhook', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    setTimeout(() => this.refreshRecent(), 600);
  },

  async saveWatchlist() {
    const raw = document.getElementById('alertWatchlistValues')?.value || '';
    const values = raw
      .split(/[\s,;]+/)
      .map((v) => v.trim())
      .filter(Boolean);
    const existing = (this._config?.watchlists || []).find((w) => (w.type || 'plate') === 'plate')
      || (this._config?.watchlists || [])[0];
    const body = {
      id: existing?.id,
      name: existing?.name || 'Priority plates',
      type: 'plate',
      enabled: true,
      values,
      notes: existing?.notes || '',
    };
    const res = await fetch('/alerts/watchlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    this._config = await res.json();
    this.renderConfig();
  },

  _normalizeAlert(raw) {
    // Already a pipeline alert
    if (raw.alert_id && raw.context) return raw;
    // Analysis event ? frame card shape
    return {
      alert_id: raw.event_id || raw.alert_id,
      kind: 'netra.event',
      fired_at: raw.wall_clock_time,
      latency_ms: null,
      rule: { name: 'Detection event' },
      event: {
        event_id: raw.event_id,
        type: raw.type,
        label: raw.label,
        plate_number: raw.plate_number,
        confidence: raw.confidence,
        frame_number: raw.frame_number,
        video_time_seconds: raw.video_time_seconds,
        video_timestamp: raw.video_timestamp,
        location: raw.location,
        bbox: raw.bbox,
      },
      stream: {
        job_id: raw.job_id,
        original_name: raw.original_name,
        source: raw.source,
      },
      context: {
        snapshot_path: raw.snapshot_url,
        snapshot_url: raw.snapshot_url,
        clip_path: raw.clip_url,
      },
      watchlist_match: null,
    };
  },

  async _loadJobFrames() {
    // Fallback: pull snapshot events from recent analysis jobs so the
    // Alert page always has detection frames to show.
    try {
      const res = await fetch('/analysis/jobs');
      if (!res.ok) return [];
      const data = await res.json();
      const jobs = data.jobs || [];
      const frames = [];
      for (const job of jobs.slice(0, 8)) {
        const id = job.job_id;
        if (!id) continue;
        try {
          const r = await fetch(`/analysis/jobs/${id}/report`);
          if (!r.ok) continue;
          const report = await r.json();
          (report.events || []).forEach((ev) => {
            if (!ev.snapshot_url) return;
            // Alert page shows threat detections only - not number plates.
            if (String(ev.type || '').toLowerCase() === 'plate') return;
            frames.push(this._normalizeAlert({
              ...ev,
              job_id: id,
              original_name: report.original_name,
              source: report.source,
            }));
          });
        } catch (_) { /* skip */ }
      }
      return frames;
    } catch (_) {
      return [];
    }
  },

  async refreshRecent() {
    try {
      const res = await fetch('/alerts/recent?limit=100');
      let alerts = [];
      if (res.ok) {
        const data = await res.json();
        alerts = (data.alerts || []).map((a) => this._normalizeAlert(a));
      }

      // Always merge job detection frames that have snapshots
      const jobFrames = await this._loadJobFrames();
      const seen = new Set(alerts.map((a) => a.alert_id));
      jobFrames.forEach((f) => {
        if (f.alert_id && !seen.has(f.alert_id)) {
          alerts.push(f);
          seen.add(f.alert_id);
        }
      });

      // Prefer items with snapshots; never show plate detections on Alert.
      alerts = alerts.filter((a) => {
        const t = String((a.event || {}).type || '').toLowerCase();
        if (t === 'plate') return false;
        return (a.context || {}).snapshot_path || (a.context || {}).snapshot_url;
      });

      this._alerts = alerts;
      this._count = alerts.length;
      this.setBadge(alerts.length);
      this.renderRecent(alerts);

      if (this._selectedId) {
        const still = alerts.find((a) => a.alert_id === this._selectedId);
        if (still) this.showFrame(still, { silent: true });
      } else if (alerts.length && document.getElementById('viewAlert') && !document.getElementById('viewAlert').hidden) {
        // Auto-open first frame when entering Alert page
        this.showFrame(alerts[0], { silent: true });
      }
    } catch (_) {
      /* ignore transient */
    }
  },

  showFrame(alert, opts = {}) {
    const viewer = document.getElementById('alertFrameViewer');
    const empty = document.getElementById('alertFrameEmpty');
    const body = document.getElementById('alertFrameBody');
    const img = document.getElementById('alertFrameImage');
    const meta = document.getElementById('alertFrameMeta');
    const actions = document.getElementById('alertFrameActions');
    if (!viewer || !img || !meta) return;

    const ev = alert.event || {};
    const ctx = alert.context || {};
    const rule = alert.rule || {};
    const stream = alert.stream || {};
    const snap = ctx.snapshot_path || ctx.snapshot_url;

    this._selectedId = alert.alert_id || null;
    document.querySelectorAll('#alertsRecent .alert-frame-card').forEach((card) => {
      card.classList.toggle('active-alert', card.dataset.alertId === this._selectedId);
    });

    viewer.style.display = 'block';

    if (!snap) {
      if (empty) {
        empty.style.display = 'block';
        empty.textContent = 'This alert has no evidence snapshot.';
      }
      if (body) body.style.display = 'none';
      return;
    }

    if (empty) empty.style.display = 'none';
    if (body) body.style.display = 'block';
    img.src = snap;
    img.alt = `${(ev.type || 'alert').toUpperCase()} evidence frame`;

    const conf = ev.confidence != null ? `${(Number(ev.confidence) * 100).toFixed(1)}%` : '';
    const src = stream.original_name || stream.source || stream.job_id || '';
    meta.innerHTML = `
      <strong>${(ev.type || 'ALERT').toUpperCase()}</strong>  ${ev.label || rule.name || 'detection'}  ${conf}<br/>
      <span class="muted">${rule.name || 'detection'}  ${src}</span><br/>
      <span class="muted">${ev.video_timestamp || alert.fired_at || ''}${ev.location ? '  ' + ev.location : ''}</span>
    `;

    if (actions) {
      const bits = [`<a class="btn btn-ghost" href="${snap}" target="_blank" rel="noopener">OPEN FULL IMAGE</a>`];
      if (ctx.clip_path || ctx.clip_url) {
        bits.push(`<a class="btn btn-ghost" href="${ctx.clip_path || ctx.clip_url}" target="_blank" rel="noopener">OPEN CLIP</a>`);
      }
      actions.innerHTML = bits.join('');
    }

    if (!opts.silent) {
      viewer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  },

  renderRecent(alerts) {
    const list = document.getElementById('alertsRecent');
    if (!list) return;
    const status = document.getElementById('alertsStatus');
    if (status) {
      status.textContent = alerts.length
        ? `${alerts.length} detection frame${alerts.length === 1 ? '' : 's'}  click a card to enlarge`
        : 'No detection frames yet. Run an upload/live analysis first.';
    }
    if (!alerts.length) {
      list.innerHTML = '<p class="report-empty">No frames with detections yet.</p>';
      return;
    }

    const typeColor = {
      weapon: '#9B3B2E',
      violence: '#9B3B2E',
      plate: '#2E6B9B',
      anomaly: '#9B7A2E',
    };

    list.innerHTML = '';
    alerts.forEach((a) => {
      const ev = a.event || {};
      const ctx = a.context || {};
      const snapSrc = ctx.snapshot_path || ctx.snapshot_url;
      if (!snapSrc) return;
      const color = typeColor[(ev.type || '').toLowerCase()] || '#666';
      const conf = ev.confidence != null ? `${(Number(ev.confidence) * 100).toFixed(0)}%` : '';

      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'alert-frame-card' + (a.alert_id === this._selectedId ? ' active-alert' : '');
      card.dataset.alertId = a.alert_id || '';
      card.innerHTML = `
        <img src="${snapSrc}" alt="${(ev.type || 'alert')} frame" />
        <div class="alert-frame-card-meta">
          <span class="report-tag" style="color:${color};border-color:${color};">${(ev.type || 'ALERT').toUpperCase()}</span>
          <span>${ev.label || ''}${conf ? '  ' + conf : ''}</span>
          <span class="muted">${ev.video_timestamp || ''}</span>
        </div>
      `;
      card.addEventListener('click', () => this.showFrame(a));
      list.appendChild(card);
    });
  },

  bind() {
    document.getElementById('alertSaveWebhookBtn')?.addEventListener('click', () => {
      this.saveWebhook().catch((e) => alert(e.message));
    });
    document.getElementById('alertTestWebhookBtn')?.addEventListener('click', () => {
      this.testWebhook().catch((e) => alert(e.message));
    });
    document.getElementById('alertSaveWatchlistBtn')?.addEventListener('click', () => {
      this.saveWatchlist().catch((e) => alert(e.message));
    });
  },
};

document.addEventListener('DOMContentLoaded', () => {
  if (window.NetraAlerts) {
    window.NetraAlerts.bind();
    window.NetraAlerts.load();
  }
});
