// Bearer-token session for the VE-JIT static UIs (mirrored into console/).
// The token lives in sessionStorage only; it is never persisted to disk or
// placed in a URL. The server verifies every request independently.
(function () {
  const KEY = 'vejit.access_token';
  let pending = null;

  function getToken() {
    try { return sessionStorage.getItem(KEY) || ''; } catch (_) { return ''; }
  }
  function setToken(token) {
    try { sessionStorage.setItem(KEY, token); } catch (_) {}
    render();
  }
  function clearToken() {
    try { sessionStorage.removeItem(KEY); } catch (_) {}
    render();
  }
  function headers() {
    const token = getToken();
    return token ? { Authorization: 'Bearer ' + token } : {};
  }

  function ensureDialog() {
    let overlay = document.getElementById('vejitLogin');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'vejitLogin';
    overlay.className = 'vejit-login-overlay';
    overlay.innerHTML =
      '<div class="vejit-login-card">' +
      '<h3>VE-JIT Sign in</h3>' +
      '<input id="vejitUser" autocomplete="username" placeholder="Username" />' +
      '<input id="vejitPass" type="password" autocomplete="current-password" placeholder="Password" />' +
      '<p id="vejitError" class="vejit-login-error"></p>' +
      '<div class="vejit-login-actions">' +
      '<button id="vejitSubmit">Sign in</button>' +
      '<button id="vejitCancel" class="vejit-secondary">Cancel</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    return overlay;
  }

  function login() {
    if (pending) return pending;
    const overlay = ensureDialog();
    const user = document.getElementById('vejitUser');
    const pass = document.getElementById('vejitPass');
    const error = document.getElementById('vejitError');
    const submit = document.getElementById('vejitSubmit');
    const cancel = document.getElementById('vejitCancel');
    error.textContent = '';
    overlay.style.display = 'flex';
    user.focus();

    pending = new Promise(function (resolve) {
      function finish(ok) {
        overlay.style.display = 'none';
        pass.value = '';
        pending = null;
        resolve(ok);
      }
      submit.onclick = async function () {
        if (!user.value || !pass.value) {
          error.textContent = 'Enter a username and password.';
          return;
        }
        submit.disabled = true;
        try {
          const base = String(window.VEJIT_API_BASE || '').replace(/\/+$/, '');
          const response = await fetch(base + '/api/v1/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: user.value, password: pass.value }),
          });
          const body = await response.json();
          if (!response.ok) {
            error.textContent = 'Sign-in failed: ' + JSON.stringify(body.detail || body);
            return;
          }
          setToken(body.access_token);
          finish(true);
        } catch (err) {
          error.textContent = 'Sign-in error: ' + String(err);
        } finally {
          submit.disabled = false;
        }
      };
      cancel.onclick = function () { finish(false); };
      user.onkeydown = pass.onkeydown = function (e) {
        if (e.key === 'Enter') submit.click();
      };
    });
    return pending;
  }

  function render() {
    const el = document.getElementById('authButton');
    if (!el) return;
    el.textContent = getToken() ? 'Signed in' : 'Sign in';
    el.onclick = getToken()
      ? function () { if (confirm('Sign out?')) { clearToken(); location.reload(); } }
      : function () { login().then(function (ok) { if (ok) location.reload(); }); };
  }

  document.addEventListener('DOMContentLoaded', render);
  window.VEJIT_AUTH = { getToken, setToken, clearToken, headers, login, render };
})();
