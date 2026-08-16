window.NetraLive = {
  init() {
    const methodsEl = document.getElementById('liveMethods');
    const brandSelect = document.getElementById('cctvBrand');
    const ipInput = document.getElementById('cctvIp');
    const portInput = document.getElementById('cctvPort');
    const userInput = document.getElementById('cctvUser');
    const passInput = document.getElementById('cctvPass');
    const rtspInput = document.getElementById('rtspInput');
    const webcamIndex = document.getElementById('webcamIndex');
    const rtspPreview = document.getElementById('rtspPreview');

    const connectBtn = document.getElementById('liveConnectBtn');
    const disconnectBtn = document.getElementById('liveDisconnectBtn');
    const startBtn = document.getElementById('liveStartBtn');
    const stopBtn = document.getElementById('liveStopBtn');

    const liveMsg = document.getElementById('liveMsg');
    const liveTag = document.getElementById('liveTag');
    const liveImg = document.getElementById('liveStreamImg');
    const liveError = document.getElementById('liveError');

    const metaStatus = document.getElementById('metaStatus');
    const metaSource = document.getElementById('metaSource');
    const metaProcessing = document.getElementById('metaProcessing');
    const metaFps = document.getElementById('metaFps');
    const metaFrames = document.getElementById('metaFrames');
    const metaRes = document.getElementById('metaRes');
    const metaModel = document.getElementById('metaModel');

    let method = 'brand';
    let connected = false;
    let monitoring = false;
    let statusTimer = null;
    let frameTimer = null;
    let streamToken = 0;

    function setMethod(next) {
      method = next;
      methodsEl.querySelectorAll('.live-method').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.method === next);
      });
      document.querySelectorAll('[data-live-form]').forEach((form) => {
        form.hidden = form.dataset.liveForm !== next;
      });
      updatePreview();
    }

    methodsEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.live-method');
      if (!btn) return;
      setMethod(btn.dataset.method);
    });

    function redactUrl(url) {
      return url.replace(/\/\/([^:@/]+):([^@/]+)@/, '//***:***@');
    }

    function buildClientPreview() {
      const brand = brandSelect.value;
      const ip = (ipInput.value || '').trim();
      const port = portInput.value || '554';
      const user = encodeURIComponent(userInput.value || '');
      const pass = encodeURIComponent(passInput.value || '');
      if (!ip) return 'Enter IP to preview RTSP URL';
      const auth = user || pass ? `${user}:${pass}@` : '';
      if (brand === 'hikvision') {
        return redactUrl(`rtsp://${auth}${ip}:${port}/Streaming/Channels/101`);
      }
      return redactUrl(
        `rtsp://${auth}${ip}:${port}/cam/realmonitor?channel=1&subtype=0`
      );
    }

    function updatePreview() {
      if (method !== 'brand') return;
      rtspPreview.textContent = buildClientPreview();
    }

    [brandSelect, ipInput, portInput, userInput, passInput].forEach((el) => {
      el.addEventListener('input', updatePreview);
      el.addEventListener('change', updatePreview);
    });

    function showError(message) {
      if (!message) {
        liveError.hidden = true;
        liveError.textContent = '';
        return;
      }
      liveError.hidden = false;
      liveError.textContent = message;
    }

    function detailMessage(errBody, fallback) {
      if (!errBody) return fallback;
      if (typeof errBody.detail === 'string') return errBody.detail;
      if (errBody.detail && errBody.detail.message) {
        const code = errBody.detail.code ? ` [${errBody.detail.code}]` : '';
        return errBody.detail.message + code;
      }
      return fallback;
    }

    async function api(path, options = {}) {
      const res = await fetch(path, {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
      });
      let body = null;
      try {
        body = await res.json();
      } catch (_) {
        body = null;
      }
      if (!res.ok) {
        const msg = detailMessage(body, `Request failed (${res.status})`);
        const err = new Error(msg);
        err.code = body && body.detail && body.detail.code;
        throw err;
      }
      return body;
    }

    function applyStatus(live) {
      if (!live) return;
      connected = !!live.connected;
      monitoring = !!live.monitoring;

      metaStatus.textContent = live.connection_status || (connected ? 'connected' : 'disconnected');
      metaSource.textContent = live.current_source || '—';
      metaProcessing.textContent = live.processing_status || 'idle';
      metaFps.textContent = live.fps != null ? String(live.fps) : '—';
      metaFrames.textContent = live.frame_count != null ? String(live.frame_count) : '0';
      metaRes.textContent = live.resolution || '—';
      metaModel.textContent = live.model_status || '—';

      connectBtn.disabled = connected;
      disconnectBtn.disabled = !connected;
      startBtn.disabled = !connected || monitoring;
      stopBtn.disabled = !monitoring;

      if (live.error) showError(live.error);
      else if (!connected) showError('');

      if (connected) {
        liveMsg.style.display = live.has_frame ? 'none' : 'flex';
        liveMsg.textContent = monitoring
          ? 'AI MONITORING'
          : 'LIVE PREVIEW — press Start Monitoring for AI';
        liveTag.classList.toggle('show', true);
        liveTag.textContent = monitoring ? 'AI LIVE' : 'LIVE';
      } else {
        liveMsg.style.display = 'flex';
        liveMsg.textContent = 'NO SIGNAL — choose a method and connect';
        liveTag.classList.remove('show');
        stopFramePoll();
      }

      if (live.job_id) {
        window.NetraAnalysis.setState({
          job_id: live.job_id,
          source: 'live',
          status: live.processing_status || live.connection_status,
          message: monitoring
            ? 'Live frames flowing through shared AI frame_processor. Click Stop Monitoring to close the report.'
            : connected
              ? 'Live preview streaming. Start monitoring to run inference and log detections.'
              : 'Live source idle.',
        });
      }
    }

    function startFramePoll() {
      stopFramePoll();
      streamToken += 1;
      const token = streamToken;
      liveImg.hidden = false;

      const tick = () => {
        if (token !== streamToken || !connected) return;
        // Cache-bust so the browser always paints the newest JPEG.
        liveImg.src = `/live/frame?t=${Date.now()}`;
      };

      liveImg.onload = () => {
        if (token !== streamToken) return;
        liveMsg.style.display = 'none';
      };
      liveImg.onerror = () => {
        /* next tick retries */
      };

      tick();
      frameTimer = setInterval(tick, 80);
    }

    function stopFramePoll() {
      streamToken += 1;
      if (frameTimer) {
        clearInterval(frameTimer);
        frameTimer = null;
      }
      liveImg.removeAttribute('src');
      liveImg.hidden = true;
    }

    function startPolling() {
      stopPolling();
      statusTimer = setInterval(async () => {
        try {
          const live = await api('/live/status');
          applyStatus(live);
        } catch (_) {
          /* ignore transient poll errors */
        }
      }, 1000);
    }

    function stopPolling() {
      if (statusTimer) {
        clearInterval(statusTimer);
        statusTimer = null;
      }
    }

    function connectPayload() {
      if (method === 'webcam') {
        return {
          method: 'webcam',
          webcam_index: Number(webcamIndex.value || 0),
        };
      }
      if (method === 'custom') {
        return {
          method: 'custom',
          brand: 'custom',
          rtsp_url: (rtspInput.value || '').trim(),
        };
      }
      return {
        method: 'brand',
        brand: brandSelect.value,
        ip: (ipInput.value || '').trim(),
        port: Number(portInput.value || 554),
        username: userInput.value || '',
        password: passInput.value || '',
        channel: 1,
        subtype: 0,
      };
    }

    connectBtn.addEventListener('click', async () => {
      showError('');
      connectBtn.disabled = true;
      connectBtn.textContent = 'CONNECTING…';
      liveMsg.style.display = 'flex';
      liveMsg.textContent = 'ACQUIRING SIGNAL…';
      try {
        const body = await api('/live/connect', {
          method: 'POST',
          body: JSON.stringify(connectPayload()),
        });
        applyStatus(body.live);
        startFramePoll();
        startPolling();
      } catch (err) {
        showError(err.message || 'Connection failed');
        liveMsg.textContent = 'NO SIGNAL — connection failed';
        applyStatus({
          connected: false,
          monitoring: false,
          connection_status: 'error',
          processing_status: 'idle',
          current_source: '—',
          frame_count: 0,
          fps: 0,
          resolution: '—',
          error: err.message,
        });
        window.NetraAnalysis.setState(null, { error: err.message });
      } finally {
        connectBtn.textContent = 'CONNECT';
        connectBtn.disabled = connected;
      }
    });

    disconnectBtn.addEventListener('click', async () => {
      try {
        const body = await api('/live/disconnect', { method: 'POST', body: '{}' });
        applyStatus(body.live);
      } catch (err) {
        showError(err.message);
      }
      stopFramePoll();
      stopPolling();
    });

    startBtn.addEventListener('click', async () => {
      try {
        const body = await api('/live/start', { method: 'POST', body: '{}' });
        applyStatus(body.live);
        startFramePoll();
        startPolling();
        if (body.live && body.live.job_id && window.NetraAnalysis) {
          window.NetraAnalysis.startPolling({
            job_id: body.live.job_id,
            source: 'live',
            status: 'processing',
            message: 'Monitoring started — detections log until you click Stop Monitoring.',
          });
        }
      } catch (err) {
        showError(err.message);
      }
    });

    stopBtn.addEventListener('click', async () => {
      stopBtn.disabled = true;
      stopBtn.textContent = 'STOPPING…';
      try {
        const liveStatus = await api('/live/status');
        if (window.NetraAnalysis && liveStatus.job_id) {
          window.NetraAnalysis._currentJob = {
            job_id: liveStatus.job_id,
            source: 'live',
            status: 'processing',
          };
          await window.NetraAnalysis.stopCurrentAnalysis();
        } else {
          await api('/live/stop', { method: 'POST', body: '{}' });
        }
        applyStatus(await api('/live/status'));
        liveMsg.style.display = 'flex';
        liveMsg.textContent = 'MONITORING STOPPED — preview only (no new detections)';
      } catch (err) {
        showError(err.message);
      } finally {
        stopBtn.textContent = 'STOP MONITORING';
      }
    });

    setMethod('brand');
    updatePreview();

    api('/live/status')
      .then((live) => {
        applyStatus(live);
        if (live.connected) {
          startFramePoll();
          startPolling();
        }
      })
      .catch(() => {});
  },
};
