/* upload.js — local video file upload */

window.NetraUpload = {
  init() {
    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('fileInput');
    const fileRow = document.getElementById('fileRow');
    const metaFile = document.getElementById('metaFile');
    const metaSize = document.getElementById('metaSize');
    const metaUploadStatus = document.getElementById('metaUploadStatus');

    const VIDEO_EXT = /\.(mp4|mov|avi|mkv|webm|flv|wmv|m4v|mpeg|mpg|mpe|3gp|3g2|ts|mts|m2ts|ogv|vob|asf|f4v|mxf|rm|rmvb|divx)$/i;

    dropzone.addEventListener('click', () => fileInput.click());
    ['dragenter', 'dragover'].forEach((evt) =>
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        dropzone.classList.add('drag');
      }),
    );
    ['dragleave', 'drop'].forEach((evt) =>
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        dropzone.classList.remove('drag');
      }),
    );
    dropzone.addEventListener('drop', (e) => {
      const f = e.dataTransfer.files[0];
      if (f) handleFile(f);
    });
    fileInput.addEventListener('change', (e) => {
      const f = e.target.files[0];
      if (f) handleFile(f);
    });

    function formatSize(bytes) {
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
      return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function isVideoFile(file) {
      if (file.type && file.type.startsWith('video/')) return true;
      return VIDEO_EXT.test(file.name || '');
    }

    function handleFile(file) {
      if (!isVideoFile(file)) {
        metaUploadStatus.textContent = 'Rejected';
        window.NetraAnalysis.setState(null, { error: 'Only video files are accepted.' });
        fileRow.innerHTML =
          '<div class="file-row"><div class="file-status" style="color:#9B3B2E;">REJECTED · not a video file</div></div>';
        return;
      }

      metaFile.textContent = file.name;
      metaSize.textContent = formatSize(file.size);
      metaUploadStatus.textContent = 'Uploading…';

      fileRow.innerHTML = `
        <div class="file-row">
          <div class="file-name">${file.name}</div>
          <div class="progress-track"><div class="progress-fill" id="pf"></div></div>
          <div class="file-status" id="fstatus">UPLOADING · 0%</div>
        </div>`;
      const pf = document.getElementById('pf');
      const fstatus = document.getElementById('fstatus');

      const form = new FormData();
      form.append('file', file);

      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/upload');

      xhr.upload.addEventListener('progress', (e) => {
        if (!e.lengthComputable) return;
        const pct = Math.min(100, Math.round((e.loaded / e.total) * 100));
        pf.style.width = pct + '%';
        fstatus.textContent = 'UPLOADING · ' + pct + '%';
      });

      xhr.addEventListener('load', () => {
        let data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (_) {}

        if (xhr.status >= 200 && xhr.status < 300 && data) {
          pf.style.width = '100%';
          fstatus.textContent = 'UPLOADED · QUEUED FOR ANALYSIS';
          metaUploadStatus.textContent = 'Uploaded';
          metaSize.textContent = formatSize(data.size_bytes || file.size);
          window.NetraAnalysis.setState(data.analysis);
          return;
        }

        const detail =
          data && data.detail
            ? typeof data.detail === 'string'
              ? data.detail
              : JSON.stringify(data.detail)
            : 'HTTP ' + xhr.status;
        fstatus.textContent = 'FAILED';
        metaUploadStatus.textContent = 'Error';
        window.NetraAnalysis.setState(null, { error: detail });
      });

      xhr.addEventListener('error', () => {
        fstatus.textContent = 'FAILED · backend unreachable';
        metaUploadStatus.textContent = 'Error';
        window.NetraAnalysis.setState(null, {
          error: 'Could not reach POST /upload. Start the API from backend/ (uvicorn api:app --port 8000).',
        });
      });

      xhr.send(form);
    }
  },
};
