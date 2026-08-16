window.NetraHistory = {
  user: null,
  historyPage: 1,
  adminPage: 1,
  adminScanPage: 1,
  pageSize: 25,

  init(user) {
    this.user = user;
    if (user.role === 'administrator') {
      this.bindAdmin();
      this.bindAdminScans();
      this.bindOperators();
      this.loadUsers();
      this.loadOperators();
    } else {
      this.bindHistory();
      this.loadHistory();
    }
  },

  qs(id) { return document.getElementById(id); },
  params(prefix, page) {
    const p = new URLSearchParams({ page, page_size: this.pageSize });
    const val = (id) => this.qs(id)?.value || '';
    p.set('search', val(prefix + 'Search'));
    const from = val(prefix + 'From');
    const to = val(prefix + 'To');
    p.set('date_from', from);
    p.set('date_to', to ? to + 'T23:59:59' : '');
    if (prefix === 'history') {
      p.set('source', val('historySource'));
      p.set('status', val('historyStatus'));
      p.set('event_type', val('historyEvent'));
    }
    return p;
  },

  async loadDashboard() {
    if (this.user?.role === 'administrator') return;
    const res = await fetch('/history?page=1&page_size=100');
    if (!res.ok) return;
    const data = await res.json();
    const jobs = data.jobs || [];
    this.qs('dashTotalScans').textContent = data.total ?? jobs.length;
    this.qs('dashCompletedScans').textContent = jobs.filter((j) => j.status === 'completed').length;
    this.qs('dashReportsDownloaded').textContent = jobs.filter((j) => j.report_downloaded).length;
  },

  async loadHistory() {
    const p = this.params('history', this.historyPage);
    const res = await fetch('/history?' + p.toString());
    if (!res.ok) return;
    const data = await res.json();
    const rows = this.qs('historyRows');
    if (!rows) return;
    rows.innerHTML = '';
    (data.jobs || []).forEach((job) => {
      const tr = document.createElement('tr');
      const source = this.sourceLabel(job.source);
      const analysis = job.status || '—';
      const report = job.report_downloaded
        ? '<span class="status-chip status-completed">DOWNLOADED</span>'
        : (job.status === 'completed' ? '<span class="muted">AVAILABLE</span>' : '—');
      const button = job.status === 'completed'
        ? '<button class="btn btn-ghost table-report" type="button">DOWNLOAD REPORT</button>'
        : '<span class="muted">—</span>';
      tr.innerHTML = `<td><strong>${this.esc(job.original_name || job.job_id)}</strong><small>${this.esc(job.job_id)}</small></td><td>${this.esc(source)}</td><td><span class="status-chip status-${this.esc(job.status || '')}">${this.esc(analysis)}</span></td><td>${report}<br>${button}</td><td>${this.formatDate(job.queued_at)}</td>`;
      tr.querySelector('.table-report')?.addEventListener('click', () => this.downloadReport(job.job_id));
      rows.appendChild(tr);
    });
    this.qs('historyCount').textContent = `${data.total || 0} records`;
    this.qs('historyPrev').disabled = this.historyPage <= 1;
    this.qs('historyNext').disabled = this.historyPage * this.pageSize >= (data.total || 0);
  },

  bindHistory() {
    this.qs('historySearchBtn')?.addEventListener('click', () => { this.historyPage = 1; this.loadHistory(); });
    this.qs('historyClearBtn')?.addEventListener('click', () => {
      ['historySearch','historySource','historyStatus','historyEvent','historyFrom','historyTo'].forEach((id) => { const el = this.qs(id); if (el) el.value = ''; });
      this.historyPage = 1; this.loadHistory();
    });
    this.qs('historyPrev')?.addEventListener('click', () => { if (this.historyPage > 1) { this.historyPage--; this.loadHistory(); } });
    this.qs('historyNext')?.addEventListener('click', () => { this.historyPage++; this.loadHistory(); });
  },

  async loadUsers() {
    const res = await fetch('/admin/users');
    if (!res.ok) return;
    const data = await res.json();
    const select = this.qs('adminUser');
    if (!select) return;
    select.innerHTML = '<option value="">All users</option>';
    (data.users || []).filter((u) => u.role === 'operator').forEach((u) => {
      const o = document.createElement('option'); o.value = u.username; o.textContent = u.username; select.appendChild(o);
    });
  },

  async loadAdmin() {
    const p = this.params('admin', this.adminPage);
    p.set('username', this.qs('adminUser')?.value || '');
    p.set('action', this.qs('adminAction')?.value || '');
    const res = await fetch('/admin/audit?' + p.toString());
    if (!res.ok) return;
    const data = await res.json();
    const rows = this.qs('adminRows');
    if (!rows) return;
    rows.innerHTML = '';
    (data.items || []).forEach((item) => {
      const details = this.activityDetails(item);
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${this.formatDate(item.timestamp)}</td><td>${this.esc(item.username || '—')}</td><td><span class="action-chip">${this.esc(this.actionLabel(item.action))}</span></td><td><strong>${this.esc(item.details?.original_name || item.details?.file_name || item.job_id || '—')}</strong><small>${this.esc(item.job_id || item.resource_id || '—')}</small></td><td>${this.esc(details)}</td>`;
      rows.appendChild(tr);
    });
    this.qs('adminCount').textContent = `${data.total || 0} records`;
    this.qs('adminPrev').disabled = this.adminPage <= 1;
    this.qs('adminNext').disabled = this.adminPage * this.pageSize >= (data.total || 0);
  },

  bindAdmin() {
    this.qs('adminSearchBtn')?.addEventListener('click', () => { this.adminPage = 1; this.loadAdmin(); });
    this.qs('adminClearBtn')?.addEventListener('click', () => {
      ['adminSearch','adminUser','adminAction','adminFrom','adminTo'].forEach((id) => { const el = this.qs(id); if (el) el.value = ''; });
      this.adminPage = 1; this.loadAdmin();
    });
    this.qs('adminPrev')?.addEventListener('click', () => { if (this.adminPage > 1) { this.adminPage--; this.loadAdmin(); } });
    this.qs('adminNext')?.addEventListener('click', () => { this.adminPage++; this.loadAdmin(); });
  },

  async loadAdminScans() {
    const p = this.params('adminScan', this.adminScanPage);
    p.set('source', this.qs('adminScanSource')?.value || '');
    p.set('status', this.qs('adminScanStatus')?.value || '');
    const res = await fetch('/admin/scans?' + p.toString());
    if (!res.ok) return;
    const data = await res.json();
    const rows = this.qs('adminScanRows');
    if (!rows) return;
    rows.innerHTML = '';
    (data.jobs || []).forEach((job) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td><strong>${this.esc(job.original_name || job.job_id)}</strong><small>${this.esc(job.job_id)}</small></td><td>${this.esc(job.username || '—')}</td><td>${this.esc(this.sourceLabel(job.source))}</td><td><span class="status-chip status-${this.esc(job.status || '')}">${this.esc(job.status || '—')}</span></td><td>${this.formatDate(job.queued_at)}</td><td><button class="btn btn-ghost table-report" type="button">DOWNLOAD REPORT</button></td>`;
      const btn = tr.querySelector('.table-report');
      btn.disabled = job.status !== 'completed';
      btn.onclick = () => this.downloadReport(job.job_id);
      rows.appendChild(tr);
    });
    this.qs('adminScanCount').textContent = `${data.total || 0} records`;
    this.qs('adminScanPrev').disabled = this.adminScanPage <= 1;
    this.qs('adminScanNext').disabled = this.adminScanPage * this.pageSize >= (data.total || 0);
  },

  bindAdminScans() {
    this.qs('adminScanSearchBtn')?.addEventListener('click', () => { this.adminScanPage = 1; this.loadAdminScans(); });
    this.qs('adminScanClearBtn')?.addEventListener('click', () => {
      ['adminScanSearch','adminScanSource','adminScanStatus','adminScanFrom','adminScanTo'].forEach((id) => { const el = this.qs(id); if (el) el.value = ''; });
      this.adminScanPage = 1; this.loadAdminScans();
    });
    this.qs('adminScanPrev')?.addEventListener('click', () => { if (this.adminScanPage > 1) { this.adminScanPage--; this.loadAdminScans(); } });
    this.qs('adminScanNext')?.addEventListener('click', () => { this.adminScanPage++; this.loadAdminScans(); });
  },

  bindOperators() {
    const form = this.qs('addOperatorForm');
    form?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = this.qs('operatorFormError');
      const ok = this.qs('operatorFormSuccess');
      err.hidden = true; ok.hidden = true;
      try {
        const res = await fetch('/admin/operators', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ username: this.qs('operatorUsername').value.trim(), password: this.qs('operatorPassword').value }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Could not create operator.');
        ok.textContent = `Operator ${data.operator.username} was created.`;
        ok.hidden = false;
        form.reset();
        this.loadOperators();
      } catch (e2) { err.textContent = e2.message; err.hidden = false; }
    });
  },

  async loadOperators() {
    const res = await fetch('/admin/users');
    if (!res.ok) return;
    const data = await res.json();
    const rows = this.qs('operatorRows');
    if (!rows) return;
    rows.innerHTML = '';
    (data.users || []).filter((u) => u.role === 'operator').forEach((u) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${this.esc(u.username)}</td><td>${u.is_active ? '<span class="status-chip status-completed">ACTIVE</span>' : '<span class="muted">INACTIVE</span>'}</td><td>${this.formatDate(u.created_at)}</td>`;
      rows.appendChild(tr);
    });
  },

  async downloadReport(jobId) {
    const res = await fetch(`/analysis/jobs/${encodeURIComponent(jobId)}/report/download`);
    if (!res.ok) { let d = {}; try { d = await res.json(); } catch (_) {} alert(d.detail || 'Could not download report.'); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `NETRA_report_${jobId}.pdf`; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    if (this.user?.role === 'administrator') this.loadAdminScans(); else this.loadHistory();
  },

  actionLabel(action) {
    return ({LOGIN:'LOGIN',LOGOUT:'LOGOUT',VIDEO_UPLOADED:'VIDEO UPLOAD',DRIVE_FETCHED:'DRIVE VIDEO',CCTV_CONNECTED:'CCTV CONNECTED',ANALYSIS_QUEUED:'ANALYSIS STARTED',ANALYSIS_COMPLETED:'ANALYSIS COMPLETED',ANALYSIS_FAILED:'ANALYSIS FAILED',REPORT_DOWNLOADED:'REPORT DOWNLOADED',OPERATOR_CREATED:'OPERATOR CREATED'})[action] || action || '—';
  },
  activityDetails(item) {
    const d = item.details || {};
    if (item.action === 'LOGIN' || item.action === 'LOGOUT') return '—';
    if (item.action === 'VIDEO_UPLOADED') return d.original_name ? `Uploaded file: ${d.original_name}` : '—';
    if (item.action === 'DRIVE_FETCHED') return d.file_name ? `Drive file: ${d.file_name}` : '—';
    if (item.action === 'CCTV_CONNECTED') return d.brand ? `Connected source: ${d.brand}` : '—';
    if (item.action === 'ANALYSIS_QUEUED') return d.original_name ? `Started analysis: ${d.original_name}` : (d.source ? `Source: ${d.source}` : '—');
    if (item.action === 'ANALYSIS_COMPLETED') return d.status ? `Analysis ${d.status}` : '—';
    if (item.action === 'ANALYSIS_FAILED') return d.status ? `Analysis ${d.status}` : '—';
    if (item.action === 'REPORT_DOWNLOADED') return d.sha256 ? `SHA-256: ${d.sha256}` : 'Report downloaded';
    if (item.action === 'OPERATOR_CREATED') return d.username ? `Created operator: ${d.username}` : '—';
    return Object.keys(d).length ? JSON.stringify(d) : '—';
  },
  sourceLabel(source) { return ({upload:'Video upload',drive:'Google Drive',live:'CCTV',webcam:'Webcam'})[source] || source || '—'; },
  refresh() { return this.loadHistory(); },
  formatDate(v) { if (!v) return '—'; const d = new Date(v); return Number.isNaN(d.getTime()) ? v : d.toLocaleString(); },
  esc(v) { return String(v ?? '').replace(/[&<>'"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); },
};
