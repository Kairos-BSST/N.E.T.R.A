/* analysis.js — shared model-analysis UI + report/timeline polling */

window.NetraAnalysis = {
  _pollTimer: null,
  _seenEventIds: new Set(),
  _previewObjectUrl: null,

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
  },

  seekVideoTo(seconds) {
    const video = document.getElementById('analysisVideoPreview');
    if (!video || !video.src) return;
    video.currentTime = Math.max(0, seconds);
    video.play().catch(() => {}); // ignore autoplay-block errors
  },

  setState(job, opts = {}) {
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
    const list = document.getElementById('reportTimeline');
    if (list) list.innerHTML = '';
    const empty = document.getElementById('reportEmpty');
    if (empty) empty.style.display = 'block';
    const btn = document.getElementById('downloadReportBtn');
    if (btn) btn.style.display = 'none';
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

      const meta = this.EVENT_LABELS[ev.type] || { title: (ev.type || 'EVENT').toUpperCase(), color: '#666' };

      const row = document.createElement('div');
      row.className = 'report-row';
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      row.title = `Jump video to ${ev.video_timestamp}`;
      row.innerHTML = `
        ${ev.snapshot_url
          ? `<img class="report-thumb" src="${ev.snapshot_url}" alt="${meta.title} evidence frame" />`
          : '<div class="report-thumb report-thumb-empty"></div>'}
        <div class="report-details">
          <div class="report-row-top">
            <span class="report-tag" style="color:${meta.color};border-color:${meta.color};">${meta.title}</span>
            <span class="report-timestamp">${ev.video_timestamp}</span>
          </div>
          <div class="report-label">${ev.label || ''}${ev.confidence ? ` · ${(ev.confidence * 100).toFixed(1)}%` : ''}</div>
          <div class="report-frame">frame #${ev.frame_number}${ev.location ? ` · ${ev.location}` : ''}</div>
        </div>`;
      const jump = () => this.seekVideoTo(ev.video_time_seconds);
      row.addEventListener('click', jump);
      row.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); jump(); }
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