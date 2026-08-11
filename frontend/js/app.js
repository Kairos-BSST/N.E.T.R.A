/* app.js — hub navigation + History / Alert side menu */

(function () {
  function $(id) { return document.getElementById(id); }

  const hub = $('hub');
  const workspace = $('workspace');
  const backBtn = $('backBtn');
  const wsSourceNum = $('wsSourceNum');
  const wsSourceName = $('wsSourceName');
  const wsSourceDesc = $('wsSourceDesc');
  const sideNav = $('sideNav');

  const sourceMeta = {
    live: {
      num: 'SOURCE / 01',
      name: 'Live CCTV',
      desc: 'Hikvision, Dahua, CP Plus, custom RTSP, or local webcam — one AI pipeline.',
      color: 'var(--live)',
      panel: 'live',
    },
    upload: {
      num: 'SOURCE / 02',
      name: 'Upload Video',
      desc: 'Send a video file straight from your device for analysis.',
      color: 'var(--upload)',
      panel: 'upload',
    },
    drive: {
      num: 'SOURCE / 03',
      name: 'Fetch from Drive',
      desc: 'Authorize Drive access and pull a file directly by API.',
      color: 'var(--drive)',
      panel: 'drive',
    },
  };

  function showView(view) {
    const name = String(view || 'history').toLowerCase();

    document.querySelectorAll('[data-view-panel]').forEach((panel) => {
      const on = (panel.getAttribute('data-view-panel') || '') === name;
      if (on) {
        panel.hidden = false;
        panel.removeAttribute('hidden');
        panel.classList.add('active');
        panel.style.display = 'block';
      } else {
        panel.hidden = true;
        panel.setAttribute('hidden', '');
        panel.classList.remove('active');
        panel.style.display = 'none';
      }
    });

    document.querySelectorAll('.side-nav-item').forEach((btn) => {
      const on = (btn.getAttribute('data-view') || '') === name;
      btn.classList.toggle('active', on);
      btn.setAttribute('aria-current', on ? 'page' : 'false');
    });

    if (name === 'alert') {
      if (window.NetraAlerts && typeof window.NetraAlerts.refreshRecent === 'function') {
        window.NetraAlerts.refreshRecent().then(() => {
          const first = (window.NetraAlerts._alerts || [])[0];
          if (first) window.NetraAlerts.showFrame(first);
        }).catch(() => {});
      }
    }
  }

  function selectSource(source) {
    const meta = sourceMeta[source];
    if (!meta) return;
    showView('history');
    if (hub) hub.classList.add('hidden');
    if (workspace) {
      workspace.classList.add('active');
      workspace.style.setProperty('--accent', meta.color);
    }
    if (wsSourceNum) wsSourceNum.textContent = meta.num;
    if (wsSourceName) wsSourceName.textContent = meta.name;
    if (wsSourceDesc) wsSourceDesc.textContent = meta.desc;

    document.querySelectorAll('#workspace .ws-grid').forEach((g) => {
      g.style.display = g.dataset.panel === meta.panel ? 'grid' : 'none';
    });

    if (backBtn) backBtn.classList.add('show');
    if (driveHooks && driveHooks.onSourceSelected) driveHooks.onSourceSelected(source);
  }

  // Event delegation — survives re-renders and is hard to miss-click.
  if (sideNav) {
    sideNav.addEventListener('click', (e) => {
      const btn = e.target.closest('.side-nav-item, [data-view]');
      if (!btn || !sideNav.contains(btn)) return;
      e.preventDefault();
      e.stopPropagation();
      const view = btn.getAttribute('data-view');
      if (view) showView(view);
    });
  }

  // Direct bindings as backup
  const navHistory = $('navHistory');
  const navAlert = $('navAlert');
  if (navHistory) {
    navHistory.addEventListener('click', (e) => {
      e.preventDefault();
      showView('history');
    });
  }
  if (navAlert) {
    navAlert.addEventListener('click', (e) => {
      e.preventDefault();
      showView('alert');
    });
  }

  document.querySelectorAll('.card').forEach((card) => {
    const open = () => selectSource(card.dataset.source);
    card.addEventListener('click', open);
    card.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') open();
    });
  });

  if (backBtn) {
    backBtn.addEventListener('click', () => {
      showView('history');
      if (hub) hub.classList.remove('hidden');
      if (workspace) workspace.classList.remove('active');
      backBtn.classList.remove('show');
    });
  }

  let driveHooks = null;
  try {
    if (window.NetraLive) window.NetraLive.init();
    if (window.NetraUpload) window.NetraUpload.init();
    if (window.NetraDrive) driveHooks = window.NetraDrive.init({ selectSource });
  } catch (err) {
    console.error('Netra module init failed', err);
  }

  window.NetraApp = { showView, selectSource };
  showView('history');
})();
