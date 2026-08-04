/* analysis.js — shared model-analysis placeholder UI */

window.NetraAnalysis = {
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
    if (status === 'processing') {
      analysisPillText.textContent = 'LIVE AI PROCESSING';
    } else if (status === 'connected') {
      analysisPillText.textContent = 'SOURCE CONNECTED';
    } else {
      analysisPillText.textContent = 'QUEUED FOR MODELS';
    }
    analysisMsg.textContent = job.message
      || 'Accepted for analysis via the shared frame_processor pipeline.';
    const shortId = (job.job_id || '—').slice(0, 8);
    analysisMeta.textContent = `job ${shortId}… · source ${job.source || '—'} · status ${job.status || '—'}`;
  },
};
