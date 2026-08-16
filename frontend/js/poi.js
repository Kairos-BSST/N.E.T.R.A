/* poi.js — Person-of-interest enrollment (max 2 face images) + gallery */
window.NetraPoi = {
  _pois: [],

  bind() {
    const form = document.getElementById('poiEnrollForm');
    form?.addEventListener('submit', (e) => {
      e.preventDefault();
      this.enroll();
    });
    document.getElementById('poiRefreshBtn')?.addEventListener('click', () => this.load());
    document.getElementById('poiChooseBtn')?.addEventListener('click', () => {
      document.getElementById('poiImages')?.click();
    });
    document.getElementById('poiImages')?.addEventListener('change', () => this._preview());
  },

  async load() {
    const status = document.getElementById('poiStatus');
    try {
      const res = await fetch('/poi');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this._pois = data.pois || [];
      this.render();
      if (status) {
        const models = data.face_models || {};
        status.textContent = `Gallery: ${this._pois.length} people · face models: ${models.status || '—'}`;
      }
    } catch (err) {
      if (status) status.textContent = `Could not load POI gallery: ${err.message}`;
    }
  },

  _preview() {
    const input = document.getElementById('poiImages');
    const box = document.getElementById('poiPreview');
    const label = document.getElementById('poiFileLabel');
    if (!input || !box) return;
    box.innerHTML = '';
    const files = Array.from(input.files || []).slice(0, 2);
    if (label) {
      label.textContent = files.length ? files.map((f) => f.name).join(', ') : 'No photos selected';
    }
    files.forEach((file) => {
      const url = URL.createObjectURL(file);
      const img = document.createElement('img');
      img.src = url;
      img.alt = file.name;
      img.className = 'poi-preview-img';
      box.appendChild(img);
    });
  },

  async enroll() {
    const errEl = document.getElementById('poiFormError');
    const okEl = document.getElementById('poiFormSuccess');
    if (errEl) { errEl.hidden = true; errEl.textContent = ''; }
    if (okEl) { okEl.hidden = true; okEl.textContent = ''; }

    const name = (document.getElementById('poiName')?.value || '').trim();
    const notes = (document.getElementById('poiNotes')?.value || '').trim();
    const input = document.getElementById('poiImages');
    const files = Array.from(input?.files || []);

    if (name.length < 2) {
      if (errEl) { errEl.textContent = 'Enter a name (at least 2 characters).'; errEl.hidden = false; }
      return;
    }
    if (!files.length) {
      if (errEl) { errEl.textContent = 'Upload 1 or 2 face images.'; errEl.hidden = false; }
      return;
    }
    if (files.length > 2) {
      if (errEl) { errEl.textContent = 'Upload at most 2 images at a time.'; errEl.hidden = false; }
      return;
    }

    const body = new FormData();
    body.append('name', name);
    body.append('notes', notes);
    files.slice(0, 2).forEach((f) => body.append('images', f, f.name));

    try {
      const res = await fetch('/poi', { method: 'POST', body });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data.detail;
        throw new Error(typeof detail === 'string' ? detail : (detail?.[0]?.msg || 'Enrollment failed.'));
      }
      if (okEl) {
        okEl.textContent = `Saved ${data.poi?.name || name} with ${data.faces?.length || 0} image(s).`;
        okEl.hidden = false;
      }
      document.getElementById('poiEnrollForm')?.reset();
      const preview = document.getElementById('poiPreview');
      if (preview) preview.innerHTML = '';
      const label = document.getElementById('poiFileLabel');
      if (label) label.textContent = 'No photos selected';
      await this.load();
    } catch (err) {
      if (errEl) { errEl.textContent = err.message || String(err); errEl.hidden = false; }
    }
  },

  async remove(poiId) {
    if (!poiId || !confirm('Delete this person-of-interest and stored images?')) return;
    const res = await fetch(`/poi/${encodeURIComponent(poiId)}`, { method: 'DELETE' });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.detail || 'Could not delete.');
      return;
    }
    await this.load();
  },

  render() {
    const rows = document.getElementById('poiRows');
    if (!rows) return;
    rows.innerHTML = '';
    if (!this._pois.length) {
      rows.innerHTML = '<tr><td colspan="4" class="muted">No persons enrolled yet. Upload 1–2 face photos above.</td></tr>';
      return;
    }
    this._pois.forEach((poi) => {
      const tr = document.createElement('tr');
      const faces = (poi.faces || []).map((f) => (
        `<img class="poi-thumb" src="${this.esc(f.image_url)}" alt="${this.esc(f.file_name || 'face')}" />`
      )).join('');
      tr.innerHTML = `
        <td><strong>${this.esc(poi.name)}</strong><small>${this.esc(poi.id)}</small></td>
        <td><div class="poi-thumbs">${faces || '—'}</div></td>
        <td>${this.esc(poi.notes || '—')}</td>
        <td><button class="btn btn-ghost poi-delete" type="button">DELETE</button></td>`;
      tr.querySelector('.poi-delete')?.addEventListener('click', () => this.remove(poi.id));
      rows.appendChild(tr);
    });
  },

  esc(v) {
    return String(v ?? '').replace(/[&<>'"]/g, (c) => (
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c])
    ));
  },
};
