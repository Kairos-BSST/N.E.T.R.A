/* analysis.js — shared model-analysis UI + report/timeline polling */

window.NetraAnalysis = {
  _pollTimer: null,
  _seenEventIds: new Set(),
  _previewObjectUrl: null,
  _events: [],
  _videoInfo: null,
  _activeOverlayEvent: null,

  /* ---- video preview ---- */

  showVideoPreview(file) {
    const block = document.getElementById('videoPreviewBlock');
    const video = document.getElementById('analysisVideoPreview');
    if (!block || !video) return;

    if (this._previewObjectUrl) {
      URL.revokeObjectURL(this._previewObjectUrl);
      this._previewObjectUrl = null;
    }

    this._previewObjectUrl = URL.createObjectURL(file);
    video.src = this._previewObjectUrl;
    block.style.display = 'block';
    this._bindOverlay(video);
    this.clearOverlay();
  },

  _bindOverlay(video) {
    if (video._netraOverlayBound) return;
    video._netraOverlayBound = true;
    const redraw = () => this.drawOverlay(this._activeOverlayEvent);
    video.addEventListener('loadedmetadata', redraw);
    video.addEventListener('seeked', redraw);
    video.addEventListener('timeupdate', () => {
      // Keep box visible near the event timestamp; clear if user scrubbed away.
      const ev = this._activeOverlayEvent;
      if (!ev || ev.video_time_seconds == null) return;
      if (Math.abs(video.currentTime - ev.video_time_seconds) > 1.25) {
        this.clearOverlay();
      }
    });
    window.addEventListener('resize', redraw);
  },

  seekVideoTo(seconds, event) {
    const video = document.getElementById('analysisVideoPreview');
    if (!video || !video.src) return;
    this._activeOverlayEvent = event || null;
    video.currentTime = Math.max(0, seconds);
    video.play().catch(() => {});
    this.drawOverlay(this._activeOverlayEvent);
  },

  clearOverlay() {
    this._activeOverlayEvent = null;
    const canvas = document.getElementById('analysisOverlay');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
  },

  drawOverlay(event) {
    const video = document.getElementById('analysisVideoPreview');
    const canvas = document.getElementById('analysisOverlay');
    if (!video || !canvas) return;

    const rect = video.getBoundingClientRect();
    const cssW = Math.max(1, rect.width);
    const cssH = Math.max(1, rect.height);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    if (!event || !Array.isArray(event.bbox) || event.bbox.length !== 4) return;

    const srcW = (this._videoInfo && this._videoInfo.width) || video.videoWidth || 1;
    const srcH = (this._videoInfo && this._videoInfo.height) || video.videoHeight || 1;
    if (!srcW || !srcH) return;

    // object-fit: contain style letterboxing
    const scale = Math.min(cssW / srcW, cssH / srcH);
    const drawW = srcW * scale;
    const drawH = srcH * scale;
    const ox = (cssW - drawW) / 2;
    const oy = (cssH - drawH) / 2;

    const [x1, y1, x2, y2] = event.bbox;
    const left = ox + x1 * scale;
    const top = oy + y1 * scale;
    const width = (x2 - x1) * scale;
    const height = (y2 - y1) * scale;

    const color = (this.EVENT_LABELS[event.type] || {}).color || '#2E6B9B';
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.strokeRect(left, top, width, height);

    const label = event.plate_number || event.label || event.type || 'DET';
    ctx.font = '600 13px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
    const padX = 6;
    const textW = ctx.measureText(label).width;
    const tagH = 20;
    const tagY = Math.max(0, top - tagH - 2);
    ctx.fillStyle = color;
    ctx.fillRect(left, tagY, textW + padX * 2, tagH);
    ctx.fillStyle = '#fff';
    ctx.fillText(label, left + padX, tagY + 14);
  },

  setState(job, opts = {}) {
    if (window.NetraAlerts) window.NetraAlerts.setCurrentJob(job?.job_id || null);
    const analysisPill = document.getElementById('analysisPill');
    const analysisPillText = document.getElementById('analysisPillText');
    const analysisMsg = document.getElementById('analysisMsg');
    const analysisMeta = document.getElementById('analysisMeta');

    if (!job) {
      analysisPill.classList.remove('active', 'error');
      analysisPillText.textContent = opts.error ? 'ERROR' : 'IDLE — waiting for input';
      if (opts.error) {
        analysisPill.classList.add('error');
        analysisMsg.textContent = opts.error;
      }
      analysisMeta.textContent = 'job — · source —';
      return;
    }

    analysisPill.classList.remove('error');
    analysisPill.classList.add('active');
    const status = (job.status || '').toLowerCase();
    if (status === 'processing' || status === 'starting') {
      analysisPillText.textContent = 'AI PROCESSING' + (job.progress ? ` · ${job.progress.toFixed(0)}%` : '');
    } else if (status === 'connected') {
      analysisPillText.textContent = 'SOURCE CONNECTED';
    } else if (status === 'completed') {
      analysisPillText.textContent = 'ANALYSIS COMPLETE';
    } else if (status === 'failed') {
      analysisPillText.textContent = 'ANALYSIS FAILED';
      analysisPill.classList.add('error');
    } else {
      analysisPillText.textContent = 'QUEUED FOR MODELS';
    }
    analysisMsg.textContent = job.message
      || 'Accepted for analysis via the shared frame_processor pipeline.';
    const shortId = (job.job_id || '—').slice(0, 8);
    analysisMeta.textContent = `job ${shortId}… · source ${job.source || '—'} · status ${job.status || '—'}`;
  },

  /* ---- report / timeline ---- */

  EVENT_LABELS: {
    weapon: { title: 'WEAPON', color: '#9B3B2E' },
    plate: { title: 'PLATE', color: '#2E6B9B' },
    anomaly: { title: 'ANOMALY', color: '#9B7A2E' },
    violence: { title: 'VIOLENCE', color: '#9B3B2E' },
  },

  resetTimeline() {
    this._seenEventIds = new Set();
    this._events = [];
    this._activeOverlayEvent = null;
    this.clearOverlay();
    const list = document.getElementById('reportTimeline');
    if (list) list.innerHTML = '';
    const empty = document.getElementById('reportEmpty');
    if (empty) empty.style.display = 'block';
    const btn = document.getElementById('downloadReportBtn');
    if (btn) btn.style.display = 'none';
    const link = document.getElementById('annotatedVideoLink');
    if (link) link.style.display = 'none';
    const plates = document.getElementById('platesFound');
    if (plates) plates.remove();
    const histEmpty = document.getElementById('historyFrameEmpty');
    const histBody = document.getElementById('historyFrameBody');
    if (histEmpty) {
      histEmpty.style.display = 'block';
      histEmpty.textContent = 'Select an event to view the detection frame.';
    }
    if (histBody) histBody.style.display = 'none';
  },

  showHistoryFrame(ev) {
    const empty = document.getElementById('historyFrameEmpty');
    const body = document.getElementById('historyFrameBody');
    const img = document.getElementById('historyFrameImage');
    const meta = document.getElementById('historyFrameMeta');
    if (!empty || !body || !img || !meta) return;

    document.querySelectorAll('#reportTimeline .report-row').forEach((row) => {
      row.classList.toggle('active-alert', row.dataset.eventId === ev.event_id);
    });

    if (!ev.snapshot_url) {
      empty.style.display = 'block';
      empty.textContent = 'This event has no evidence snapshot.';
      body.style.display = 'none';
      return;
    }

    empty.style.display = 'none';
    body.style.display = 'block';
    img.src = ev.snapshot_url;
    img.alt = `${(ev.type || 'event').toUpperCase()} evidence frame`;

    const conf = ev.confidence ? `${(ev.confidence * 100).toFixed(1)}%` : '—';
    const plate = ev.plate_number ? ` · ${ev.plate_number}` : '';
    meta.innerHTML = `
      <strong>${(ev.type || 'EVENT').toUpperCase()}</strong> · ${ev.label || ''}${plate} · ${conf}<br/>
      <span class="muted">frame #${ev.frame_number || '—'} · ${ev.video_timestamp || ''}</span><br/>
      <span class="muted">${ev.location || ''}</span>
      ${ev.snapshot_url ? `<br/><a href="${ev.snapshot_url}" target="_blank" rel="noopener">Open full image</a>` : ''}
    `;
    body.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  },

  renderEvents(events) {
    const list = document.getElementById('reportTimeline');
    const empty = document.getElementById('reportEmpty');
    if (!list) return;

    if (!events || events.length === 0) return;
    if (empty) empty.style.display = 'none';

    events.forEach((ev) => {
      if (this._seenEventIds.has(ev.event_id)) return;
      this._seenEventIds.add(ev.event_id);
      this._events.push(ev);

      const meta = this.EVENT_LABELS[ev.type] || { title: (ev.type || 'EVENT').toUpperCase(), color: '#666' };
      const plateBit = ev.plate_number ? ` · ${ev.plate_number}` : '';

      const row = document.createElement('div');
      row.className = 'report-row';
      row.dataset.eventId = ev.event_id || '';
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      row.title = `View frame at ${ev.video_timestamp}`;
      row.innerHTML = `
        ${ev.snapshot_url
          ? `<img class="report-thumb" src="${ev.snapshot_url}" alt="${meta.title} evidence frame" />`
          : '<div class="report-thumb report-thumb-empty"></div>'}
        <div class="report-details">
          <div class="report-row-top">
            <span class="report-tag" style="color:${meta.color};border-color:${meta.color};">${meta.title}</span>
            <span class="report-timestamp">${ev.video_timestamp}</span>
          </div>
          <div class="report-label">${ev.label || ''}${plateBit}${ev.confidence ? ` · ${(ev.confidence * 100).toFixed(1)}%` : ''}</div>
          <div class="report-frame">frame #${ev.frame_number}${ev.location ? ` · ${ev.location}` : ''} · click to open frame</div>
        </div>`;
      const open = () => {
        this.showHistoryFrame(ev);
        this.seekVideoTo(ev.video_time_seconds, ev);
      };
      row.addEventListener('click', open);
      row.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
      });
      list.prepend(row); // newest first
    });
  },

  async fetchReport(jobId) {
    const res = await fetch(`/analysis/jobs/${jobId}/report`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  startPolling(job, { intervalMs = 1500 } = {}) {
    this.setState(job);
    this.resetTimeline();

    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }

    if (!job || !job.job_id) return;
    const jobId = job.job_id;

    const tick = async () => {
      try {
        const report = await this.fetchReport(jobId);
        this.setState(report);
        this._videoInfo = report.video_info || null;
        this.renderEvents(report.events);

        const status = (report.status || '').toLowerCase();
        if (status === 'completed' || status === 'failed') {
          clearInterval(this._pollTimer);
          this._pollTimer = null;
          this.renderSummary(report);
        }
      } catch (err) {
        // transient network hiccup — keep polling silently
      }
    };

    tick();
    this._pollTimer = setInterval(tick, intervalMs);
  },

  renderSummary(report) {
    const summaryEl = document.getElementById('reportSummary');
    if (summaryEl) {
      const s = report.summary || {};
      summaryEl.textContent = (!s || Object.keys(s).length === 0) ? '' :
        `${s.frames_processed || 0} frames analysed in ${s.processing_seconds ?? '—'}s · `
        + `${report.event_count || 0} events logged `
        + `(weapon: ${s.weapon_detections ?? 0}, plate: ${s.plate_detections ?? 0}, `
        + `anomaly frames: ${s.anomaly_frames ?? 0}, fight frames: ${s.fight_frames ?? 0}).`;

      const plates = report.plates_found || s.plates_found || [];
      let platesEl = document.getElementById('platesFound');
      if (plates.length) {
        if (!platesEl) {
          platesEl = document.createElement('div');
          platesEl.id = 'platesFound';
          platesEl.className = 'plates-found';
          summaryEl.insertAdjacentElement('afterend', platesEl);
        }
        const nums = plates.map((p) => p.plate_number).filter(Boolean);
        platesEl.innerHTML = `<strong>Plates read:</strong> ${nums.join(', ')}`;
      } else if (platesEl) {
        platesEl.remove();
      }
    }

    const link = document.getElementById('annotatedVideoLink');
    const annotatedUrl = report.annotated_video_url
      || (report.summary && report.summary.annotated_video_url);
    if (link && annotatedUrl) {
      link.href = annotatedUrl;
      link.style.display = 'inline-block';
    }

    this.showDownloadButton(report.job_id);
  },

  showDownloadButton(jobId) {
    let btn = document.getElementById('downloadReportBtn');
    const summaryEl = document.getElementById('reportSummary');
    if (!btn && summaryEl) {
      btn = document.createElement('button');
      btn.id = 'downloadReportBtn';
      btn.type = 'button';
      btn.className = 'btn btn-ghost report-download-btn';
      btn.textContent = 'DOWNLOAD REPORT (PDF)';
      summaryEl.insertAdjacentElement('afterend', btn);
    }
    if (!btn) return;
    btn.style.display = 'inline-flex';
    btn.onclick = () => {
      window.open(`/analysis/jobs/${jobId}/report/download`, '_blank');
    };
  },
};
