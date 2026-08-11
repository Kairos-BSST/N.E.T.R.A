/* alerts.js — current-scan detection frames only */
window.NetraAlerts = {
  _pollTimer: null,
  _alerts: [],
  _selectedId: null,
  _currentJobId: null,

  setCurrentJob(jobId) {
    const next = jobId || null;
    if (this._currentJobId === next) return;
    this._currentJobId = next;
    this._selectedId = null;
    this.refreshRecent();
    if (!this._pollTimer) this._pollTimer = setInterval(() => this.refreshRecent(), 2000);
  },

  clearCurrent() {
    this._currentJobId = null;
    this._alerts = [];
    this._selectedId = null;
    this.renderRecent([]);
    const viewer = document.getElementById('alertFrameViewer');
    if (viewer) viewer.style.display = 'none';
  },

  _normalizeAlert(raw) {
    if (raw.alert_id && raw.context) return raw;
    return {
      alert_id: raw.event_id || raw.alert_id,
      kind: 'netra.event',
      fired_at: raw.wall_clock_time,
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
      stream: { job_id: raw.job_id, original_name: raw.original_name, source: raw.source },
      context: { snapshot_path: raw.snapshot_url, snapshot_url: raw.snapshot_url, clip_path: raw.clip_url },
      rule: { name: 'Detection event' },
    };
  },

  async refreshRecent() {
    const status = document.getElementById('alertsStatus');
    if (!this._currentJobId) {
      this.renderRecent([]);
      if (status) status.textContent = 'No active scan. Start a scan to view its detection alerts.';
      return;
    }
    try {
      const res = await fetch(`/alerts/recent?limit=100&job_id=${encodeURIComponent(this._currentJobId)}`);
      if (!res.ok) return;
      const data = await res.json();
      const alerts = (data.alerts || []).map((a) => this._normalizeAlert(a)).filter((a) => {
        const t = String((a.event || {}).type || '').toLowerCase();
        return t !== 'plate' && ((a.context || {}).snapshot_path || (a.context || {}).snapshot_url);
      });
      this._alerts = alerts;
      this.renderRecent(alerts);
      if (this._selectedId) {
        const still = alerts.find((a) => a.alert_id === this._selectedId);
        if (still) this.showFrame(still, { silent: true });
      }
    } catch (_) { /* transient */ }
  },

  showFrame(alert, opts = {}) {
    const viewer = document.getElementById('alertFrameViewer');
    const empty = document.getElementById('alertFrameEmpty');
    const body = document.getElementById('alertFrameBody');
    const img = document.getElementById('alertFrameImage');
    const meta = document.getElementById('alertFrameMeta');
    const actions = document.getElementById('alertFrameActions');
    if (!viewer || !img || !meta) return;
    const ev = alert.event || {}, ctx = alert.context || {}, stream = alert.stream || {};
    const snap = ctx.snapshot_path || ctx.snapshot_url;
    this._selectedId = alert.alert_id || null;
    document.querySelectorAll('#alertsRecent .alert-frame-card').forEach((card) => card.classList.toggle('active-alert', card.dataset.alertId === this._selectedId));
    viewer.style.display = 'block';
    if (!snap) {
      if (empty) { empty.style.display = 'block'; empty.textContent = '—'; }
      if (body) body.style.display = 'none';
      return;
    }
    if (empty) empty.style.display = 'none';
    if (body) body.style.display = 'block';
    img.src = snap;
    img.alt = `${(ev.type || 'alert').toUpperCase()} evidence frame`;
    const conf = ev.confidence != null ? `${(Number(ev.confidence) * 100).toFixed(1)}%` : '—';
    meta.innerHTML = `<strong>${(ev.type || 'ALERT').toUpperCase()}</strong> · ${ev.label || 'detection'} · ${conf}<br/><span class="muted">${stream.original_name || stream.source || stream.job_id || '—'}</span><br/><span class="muted">${ev.video_timestamp || alert.fired_at || '—'}${ev.location ? ' · ' + ev.location : ''}</span>`;
    if (actions) {
      const bits = [`<a class="btn btn-ghost" href="${snap}" target="_blank" rel="noopener">OPEN FULL IMAGE</a>`];
      if (ctx.clip_path || ctx.clip_url) bits.push(`<a class="btn btn-ghost" href="${ctx.clip_path || ctx.clip_url}" target="_blank" rel="noopener">OPEN CLIP</a>`);
      actions.innerHTML = bits.join('');
    }
    if (!opts.silent) viewer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  },

  renderRecent(alerts) {
    const list = document.getElementById('alertsRecent');
    if (!list) return;
    const status = document.getElementById('alertsStatus');
    if (status) status.textContent = alerts.length ? `${alerts.length} detection frame${alerts.length === 1 ? '' : 's'} from this scan` : (this._currentJobId ? 'No detection frames for this scan.' : 'No active scan.');
    if (!alerts.length) { list.innerHTML = '<p class="report-empty">No detection frames for this scan.</p>'; return; }
    list.innerHTML = '';
    alerts.forEach((a) => {
      const ev = a.event || {}, ctx = a.context || {}, snap = ctx.snapshot_path || ctx.snapshot_url;
      if (!snap) return;
      const conf = ev.confidence != null ? `${(Number(ev.confidence) * 100).toFixed(0)}%` : '';
      const card = document.createElement('button');
      card.type = 'button'; card.className = 'alert-frame-card' + (a.alert_id === this._selectedId ? ' active-alert' : ''); card.dataset.alertId = a.alert_id || '';
      card.innerHTML = `<img src="${snap}" alt="${ev.type || 'alert'} frame" /><div class="alert-frame-card-meta"><span class="report-tag">${(ev.type || 'ALERT').toUpperCase()}</span><span>${ev.label || ''}${conf ? ' · ' + conf : ''}</span><span class="muted">${ev.video_timestamp || '—'}</span></div>`;
      card.addEventListener('click', () => this.showFrame(a));
      list.appendChild(card);
    });
  },

  bind() {},
};
