/* auth.js — login/session gate. Role is read from the server, never chosen by the user. */
(function () {
  const screen = document.getElementById('loginScreen');
  const shell = document.getElementById('appShell');
  const form = document.getElementById('loginForm');
  const error = document.getElementById('loginError');
  const username = document.getElementById('loginUsername');
  const password = document.getElementById('loginPassword');
  const submitBtn = document.getElementById('loginSubmitBtn');
  const passwordToggle = document.getElementById('loginPasswordToggle');

  function applyRoleVisibility(user) {
    const isAdmin = user.role === 'administrator';
    document.querySelectorAll('.admin-only').forEach((el) => { el.hidden = !isAdmin; });
    document.querySelectorAll('.operator-only').forEach((el) => { el.hidden = isAdmin; });
  }

  function setLoading(loading) {
    if (!submitBtn) return;
    submitBtn.disabled = loading;
    submitBtn.classList.toggle('is-loading', loading);
    const label = submitBtn.querySelector('.login-submit-label');
    const spinner = submitBtn.querySelector('.login-submit-spinner');
    if (label) label.hidden = loading;
    if (spinner) spinner.hidden = !loading;
  }

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
    error.classList.remove('is-visible');
    void error.offsetWidth;
    error.classList.add('is-visible');
  }

  function hideError() {
    error.hidden = true;
    error.classList.remove('is-visible');
  }

  function showApp(user) {
    screen.hidden = true;
    shell.hidden = false;
    const userLabel = document.getElementById('currentUserLabel');
    const roleLabel = document.getElementById('currentRoleLabel');
    if (userLabel) userLabel.textContent = user.username;
    if (roleLabel) roleLabel.textContent = user.role === 'administrator' ? 'ADMINISTRATOR' : 'OPERATOR';
    applyRoleVisibility(user);
    window.NetraAuth = { user, logout };
    window.NetraApp?.enforceRoleVisibility?.(user.role);
    window.NetraApp?.init?.(user);
  }

  function showLogin() {
    screen.hidden = false;
    shell.hidden = true;
    setLoading(false);
    username?.focus();
  }

  async function login(event) {
    event.preventDefault();
    hideError();
    setLoading(true);
    try {
      const res = await fetch('/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.value.trim(), password: password.value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Login failed.');
      password.value = '';
      showApp(data.user);
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function logout() {
    try { await fetch('/auth/logout', { method: 'POST' }); } catch (_) {}
    location.reload();
  }

  passwordToggle?.addEventListener('click', () => {
    const show = password.type === 'password';
    password.type = show ? 'text' : 'password';
    passwordToggle.setAttribute('aria-pressed', String(show));
    passwordToggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    passwordToggle.classList.toggle('is-visible', show);
  });

  form?.addEventListener('submit', login);
  document.getElementById('logoutBtn')?.addEventListener('click', logout);

  fetch('/auth/me')
    .then((res) => res.ok ? res.json() : Promise.reject(new Error('unauthenticated')))
    .then((data) => showApp(data.user))
    .catch(showLogin);
})();
