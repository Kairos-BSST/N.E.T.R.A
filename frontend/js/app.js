/* app.js — final NETRA role-aware navigation and source workspace */
(function () {
  const $ = (id) => document.getElementById(id);
  const hub = $('hub');
  const workspace = $('workspace');
  const wsSourceNum = $('wsSourceNum');
  const wsSourceName = $('wsSourceName');
  const wsSourceDesc = $('wsSourceDesc');
  const sideNav = $('sideNav');
  const sideNavToggle = $('sideNavToggle');
  let driveHooks = null;
  let initialized = false;

  const sourceMeta = {
    live: { num: 'SOURCE / 01', name: 'Live CCTV', desc: 'Hikvision, Dahua, CP Plus, custom RTSP, or local webcam — one AI pipeline.', color: 'var(--live)', panel: 'live' },
    upload: { num: 'SOURCE / 02', name: 'Upload Video', desc: 'Send a video file straight from your device for analysis.', color: 'var(--upload)', panel: 'upload' },
    drive: { num: 'SOURCE / 03', name: 'Fetch from Drive', desc: 'Authorize Drive access and pull a file directly by API.', color: 'var(--drive)', panel: 'drive' },
  };
  function enforceRoleVisibility(role) {
    const isAdmin = role === 'administrator';
    document.querySelectorAll('.admin-only').forEach((el) => { el.hidden = !isAdmin; });
    document.querySelectorAll('.operator-only').forEach((el) => { el.hidden = isAdmin; });
  }

  function initials(name) {
    return String(name || '')
      .trim()
      .split(/[\s._-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0].toUpperCase())
      .join('') || '—';
  }

  function setUserAvatar(name) {
    const value = initials(name);
    const sideAvatar = $('sideUserAvatar');
    if (sideAvatar) sideAvatar.textContent = value;
  }

  function bindSidebarToggle() {
    if (!sideNavToggle || !sideNav) return;
    sideNavToggle.addEventListener('click', () => {
      const collapsed = sideNav.classList.toggle('collapsed');
      sideNavToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      sideNavToggle.setAttribute('title', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
      sideNavToggle.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
    });
  }

  function bindSidebarKeyboardNav() {
    if (!sideNav) return;
    sideNav.addEventListener('keydown', (event) => {
      const items = Array.from(sideNav.querySelectorAll('.side-nav-item')).filter((el) => !el.hidden && el.offsetParent !== null);
      const current = document.activeElement;
      const index = items.indexOf(current);
      if (index === -1) return;
      let nextIndex = null;
      if (event.key === 'ArrowDown') nextIndex = (index + 1) % items.length;
      else if (event.key === 'ArrowUp') nextIndex = (index - 1 + items.length) % items.length;
      else if (event.key === 'Home') nextIndex = 0;
      else if (event.key === 'End') nextIndex = items.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      items[nextIndex].focus();
    });
  }

  function showView(view) {
    const name = String(view || '').toLowerCase();
    const allowed = ['dashboard', 'alert', 'history', 'poi', 'adminactivity', 'adminscans', 'adminoperators'];
    const target = allowed.includes(name) ? name : 'dashboard';

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

    if (target === 'dashboard') {
      hub?.classList.remove('hidden');
      workspace?.classList.remove('active');
      window.NetraHistory?.loadDashboard?.();
    } else if (target === 'alert') {
      window.NetraAlerts?.refreshRecent?.();
      document.getElementById('navAlert')?.classList.remove('has-alert');
    } else if (target === 'history') {
      window.NetraHistory?.refresh?.();
    } else if (target === 'poi') {
      window.NetraPoi?.load?.();
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
    window.NetraAlerts?.setCurrentJob?.(null);
    driveHooks?.onSourceSelected?.(source);
  }

  function bindNavigation() {
    sideNav?.addEventListener('click', (event) => {
      const btn = event.target.closest('.side-nav-item');
      if (!btn || btn.hidden || btn.id === 'sideNavLogoutBtn') return;
      event.preventDefault();
      showView(btn.dataset.view);
    });

    document.querySelectorAll('.card[data-source]').forEach((card) => {
      const open = () => selectSource(card.dataset.source);
      card.addEventListener('click', open);
      card.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
    });

    $('dashOpenHistory')?.addEventListener('click', () => showView('history'));
  }

  function init(user) {
    if (initialized) return;
    initialized = true;
    enforceRoleVisibility(user?.role);
    bindNavigation();
    bindSidebarToggle();
    bindSidebarKeyboardNav();
    setUserAvatar(user?.username);
    try {
      window.NetraLive?.init?.();
      window.NetraUpload?.init?.();
      if (window.NetraDrive) driveHooks = window.NetraDrive.init({ selectSource });
      window.NetraHistory?.init?.(user);
      window.NetraPoi?.bind?.();
      window.NetraAlerts?.bind?.();
    } catch (err) {
      console.error('NETRA module initialization failed', err);
    }
    showView(user?.role === 'administrator' ? 'adminActivity' : 'dashboard');
  }

  window.NetraApp = { showView, selectSource, init, enforceRoleVisibility };
})();
