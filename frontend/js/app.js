/* app.js — final NETRA role-aware navigation and source workspace */
(function () {
  const $ = (id) => document.getElementById(id);
  const hub = $('hub');
  const workspace = $('workspace');
  const backBtn = $('backBtn');
  const headerContextKicker = $('headerContextKicker');
  const headerContextTitle = $('headerContextTitle');
  const wsSourceNum = $('wsSourceNum');
  const wsSourceName = $('wsSourceName');
  const wsSourceDesc = $('wsSourceDesc');
  const sideNav = $('sideNav');
  let driveHooks = null;
  let initialized = false;

  const sourceMeta = {
    live: { num: 'SOURCE / 01', name: 'Live CCTV', desc: 'Hikvision, Dahua, CP Plus, custom RTSP, or local webcam — one AI pipeline.', color: 'var(--live)', panel: 'live' },
    upload: { num: 'SOURCE / 02', name: 'Upload Video', desc: 'Send a video file straight from your device for analysis.', color: 'var(--upload)', panel: 'upload' },
    drive: { num: 'SOURCE / 03', name: 'Fetch from Drive', desc: 'Authorize Drive access and pull a file directly by API.', color: 'var(--drive)', panel: 'drive' },
  };
  const viewMeta = {
    dashboard: { kicker: 'Operations', title: 'Dashboard overview' },
    alert: { kicker: 'Operations', title: 'Active scan alerts' },
    history: { kicker: 'Operations', title: 'Analysis history' },
    adminactivity: { kicker: 'Administration', title: 'System activity' },
    adminscans: { kicker: 'Administration', title: 'Report history' },
    adminoperators: { kicker: 'Administration', title: 'Operator management' },
  };

  function enforceRoleVisibility(role) {
    const isAdmin = role === 'administrator';
    document.querySelectorAll('.admin-only').forEach((el) => { el.hidden = !isAdmin; });
    document.querySelectorAll('.operator-only').forEach((el) => { el.hidden = isAdmin; });
  }

  function setHeaderContext(kicker, title) {
    if (headerContextKicker) headerContextKicker.textContent = kicker;
    if (headerContextTitle) headerContextTitle.textContent = title;
  }

  function showView(view) {
    const name = String(view || '').toLowerCase();
    const allowed = ['dashboard', 'alert', 'history', 'adminactivity', 'adminscans', 'adminoperators'];
    const target = allowed.includes(name) ? name : 'dashboard';
    const meta = viewMeta[target] || viewMeta.dashboard;

    document.querySelectorAll('[data-view-panel]').forEach((panel) => {
      const on = String(panel.dataset.viewPanel || '').toLowerCase() === target;
      panel.hidden = !on;
      panel.classList.toggle('active', on);
      panel.style.display = on ? 'block' : 'none';
    });

    document.querySelectorAll('.side-nav-item').forEach((btn) => {
      const on = String(btn.dataset.view || '').toLowerCase() === target;
      btn.classList.toggle('active', on);
      btn.setAttribute('aria-current', on ? 'page' : 'false');
    });

    setHeaderContext(meta.kicker, meta.title);

    if (target === 'dashboard') {
      hub?.classList.remove('hidden');
      workspace?.classList.remove('active');
      backBtn?.classList.remove('show');
      window.NetraHistory?.loadDashboard?.();
    } else if (target === 'alert') {
      window.NetraAlerts?.refreshRecent?.();
    } else if (target === 'history') {
      window.NetraHistory?.refresh?.();
    } else if (target === 'adminactivity') {
      window.NetraHistory?.loadAdmin?.();
    } else if (target === 'adminscans') {
      window.NetraHistory?.loadAdminScans?.();
    } else if (target === 'adminoperators') {
      window.NetraHistory?.loadOperators?.();
    }
  }

  function selectSource(source) {
    const meta = sourceMeta[source];
    if (!meta) return;
    showView('dashboard');
    hub?.classList.add('hidden');
    workspace?.classList.add('active');
    workspace?.style.setProperty('--accent', meta.color);
    if (wsSourceNum) wsSourceNum.textContent = meta.num;
    if (wsSourceName) wsSourceName.textContent = meta.name;
    if (wsSourceDesc) wsSourceDesc.textContent = meta.desc;
    document.querySelectorAll('#workspace .ws-grid').forEach((grid) => {
      grid.style.display = grid.dataset.panel === meta.panel ? 'grid' : 'none';
    });
    backBtn?.classList.add('show');
    setHeaderContext('Operations', `${meta.name} workspace`);
    window.NetraAlerts?.setCurrentJob?.(null);
    driveHooks?.onSourceSelected?.(source);
  }

  function bindNavigation() {
    sideNav?.addEventListener('click', (event) => {
      const btn = event.target.closest('.side-nav-item');
      if (!btn || btn.hidden) return;
      event.preventDefault();
      showView(btn.dataset.view);
    });

    document.querySelectorAll('.card[data-source]').forEach((card) => {
      const open = () => selectSource(card.dataset.source);
      card.addEventListener('click', open);
      card.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
    });

    backBtn?.addEventListener('click', () => showView('dashboard'));
    $('dashOpenHistory')?.addEventListener('click', () => showView('history'));
  }

  function init(user) {
    if (initialized) return;
    initialized = true;
    enforceRoleVisibility(user?.role);
    bindNavigation();
    try {
      window.NetraLive?.init?.();
      window.NetraUpload?.init?.();
      if (window.NetraDrive) driveHooks = window.NetraDrive.init({ selectSource });
      window.NetraHistory?.init?.(user);
      window.NetraAlerts?.bind?.();
    } catch (err) {
      console.error('NETRA module initialization failed', err);
    }
    showView(user?.role === 'administrator' ? 'adminActivity' : 'dashboard');
  }

  window.NetraApp = { showView, selectSource, init, enforceRoleVisibility };
})();
