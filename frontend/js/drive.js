window.NetraDrive = {
  init(hooks = {}) {
    const driveConnectBtn = document.getElementById('driveConnectBtn');
    const driveConnect = document.getElementById('driveConnect');
    const driveGrid = document.getElementById('driveGrid');
    const metaAccount = document.getElementById('metaAccount');
    const metaDriveFile = document.getElementById('metaDriveFile');
    const metaDriveStatus = document.getElementById('metaDriveStatus');

    function formatDriveSize(bytes) {
      if (!bytes) return '—';
      bytes = Number(bytes);
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
      return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    async function loadDriveFiles() {
      driveConnect.style.display = 'none';
      driveGrid.classList.add('show');
      metaAccount.textContent = 'Connected';
      driveGrid.innerHTML = '<p class="side-note">Loading files…</p>';

      try {
        const res = await fetch('/drive/files');
        if (!res.ok) throw new Error((await res.json()).detail || 'Failed to list files');
        const data = await res.json();
        const files = data.files || [];

        if (files.length === 0) {
          driveGrid.innerHTML = '<p class="side-note">No video files found in this Drive account.</p>';
          return;
        }

        driveGrid.innerHTML = files
          .map(
            (f) => `
          <div class="drive-file">
            <div>
              <div class="drive-fname">${f.name}</div>
              <div class="drive-fmeta">${formatDriveSize(f.size)} · ${f.mimeType}</div>
            </div>
            <button class="drive-fetch-btn" data-id="${f.id}" data-name="${f.name}">FETCH FILE</button>
          </div>`,
          )
          .join('');

        driveGrid.querySelectorAll('.drive-fetch-btn').forEach((btn) => {
          btn.addEventListener('click', () => fetchDriveFile(btn));
        });
      } catch (err) {
        driveGrid.innerHTML = `<p class="side-note">Could not load Drive files: ${err.message}</p>`;
      }
    }

    async function fetchDriveFile(btn) {
      const fileId = btn.dataset.id;
      const fileName = btn.dataset.name;

      driveGrid.querySelectorAll('.drive-fetch-btn').forEach((b) => {
        b.classList.remove('fetching');
        b.disabled = false;
        b.textContent = 'FETCH FILE';
      });
      btn.classList.add('fetching');
      btn.disabled = true;
      btn.textContent = 'FETCHING…';
      metaDriveFile.textContent = fileName;
      metaDriveStatus.textContent = 'Fetching…';

      try {
        const res = await fetch('/drive/fetch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_id: fileId, file_name: fileName }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || 'Fetch failed');
        const data = await res.json();

        btn.textContent = 'FETCHED ✓';
        metaDriveStatus.textContent = data.analysis ? 'Fetched + queued for analysis' : 'Fetched';
        if (data.analysis) window.NetraAnalysis.startPolling(data.analysis);
      } catch (err) {
        btn.disabled = false;
        btn.classList.remove('fetching');
        btn.textContent = 'RETRY';
        metaDriveStatus.textContent = 'Error: ' + err.message;
        window.NetraAnalysis.setState(null, { error: err.message });
      }
    }

    driveConnectBtn.addEventListener('click', () => {
      window.location.href = '/auth/google/login';
    });

    (async function checkDriveConnectionOnLoad() {
      const params = new URLSearchParams(window.location.search);
      const authResult = params.get('drive_auth');

      if (authResult === 'denied' || authResult === 'error') {
        if (hooks.selectSource) hooks.selectSource('drive');
        metaDriveStatus.textContent =
          'Authorization failed: ' + (params.get('reason') || 'unknown error');
        history.replaceState({}, '', window.location.pathname);
        return;
      }

      try {
        const res = await fetch('/auth/google/status');
        const data = await res.json();
        if (data.connected) {
          if (authResult === 'success' && hooks.selectSource) hooks.selectSource('drive');
          if (
            document.getElementById('workspace').classList.contains('active') ||
            authResult === 'success'
          ) {
            loadDriveFiles();
          } else {
            driveConnect.dataset.preconnected = 'true';
          }
        }
      } catch (e) {
        /* backend not reachable yet */
      }

      if (authResult === 'success') {
        history.replaceState({}, '', window.location.pathname);
      }
    })();

    return {
      onSourceSelected(source) {
        if (source === 'drive' && driveConnect.dataset.preconnected === 'true') {
          loadDriveFiles();
        }
      },
    };
  },
};
