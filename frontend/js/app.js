/* app.js — hub navigation; wires each input module */

(function () {
  const hub = document.getElementById('hub');
  const workspace = document.getElementById('workspace');
  const backBtn = document.getElementById('backBtn');
  const wsSourceNum = document.getElementById('wsSourceNum');
  const wsSourceName = document.getElementById('wsSourceName');
  const wsSourceDesc = document.getElementById('wsSourceDesc');

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

  function selectSource(source) {
    const meta = sourceMeta[source];
    hub.classList.add('hidden');
    workspace.classList.add('active');
    workspace.style.setProperty('--accent', meta.color);
    wsSourceNum.textContent = meta.num;
    wsSourceName.textContent = meta.name;
    wsSourceDesc.textContent = meta.desc;

    document.querySelectorAll('#workspace .ws-grid').forEach((g) => {
      g.style.display = g.dataset.panel === meta.panel ? 'grid' : 'none';
    });

    backBtn.classList.add('show');
    if (driveHooks && driveHooks.onSourceSelected) driveHooks.onSourceSelected(source);
  }

  document.querySelectorAll('.card').forEach((card) => {
    const open = () => selectSource(card.dataset.source);
    card.addEventListener('click', open);
    card.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') open();
    });
  });

  backBtn.addEventListener('click', () => {
    hub.classList.remove('hidden');
    workspace.classList.remove('active');
    backBtn.classList.remove('show');
  });

  window.NetraLive.init();
  window.NetraUpload.init();
  const driveHooks = window.NetraDrive.init({ selectSource });
})();
