'use strict';
(() => {
  const root = document.querySelector('main');
  const login = document.querySelector('#login');
  const approval = document.querySelector('#approval');
  const message = document.querySelector('#message');
  let token = null;
  let page = 1;
  const managing = root.dataset.mode === 'manage';
  const staging = root.dataset.staging === 'true';
  const deadline = Date.now() + Number(root.dataset.intentSeconds) * 1000;
  const expiredMessage = 'This connection request has expired. Return to your AI app and choose Connect or Sign in again.';
  let expired = false;
  let deciding = false;
  let generation = 0;
  const showLogin = (focus = true) => {
    generation += 1; token = null; login.password.value = '';
    document.querySelector('#identity').textContent = '';
    if (managing) {
      document.querySelector('#connections').replaceChildren();
      document.querySelector('#more').hidden = true; page = 1;
    }
    approval.hidden = true; login.hidden = expired;
    if (focus && !expired) login.email.focus();
  };
  const restart = text => {
    expired = true; showLogin(false);
    root.removeAttribute('data-intent');
    document.querySelector('#expiry-note')?.setAttribute('hidden', '');
    document.querySelector('#callback-note')?.setAttribute('hidden', '');
    message.textContent = text; message.focus();
  };
  const checkExpiry = () => {
    if (!managing && !expired && !deciding && Date.now() >= deadline) restart(expiredMessage);
    return expired;
  };
  const failure = (text, recovery = null) => Object.assign(new Error(text), {recovery});
  const handleError = error => {
    if (error.recovery === 'stale' || expired) return;
    if (error.recovery === 'restart') restart(error.message);
    else {
      if (error.recovery === 'signin') showLogin();
      message.textContent = error.message;
    }
  };
  const requestJson = async (url, options, action, authenticated = false) => {
    const started = generation;
    let response;
    try {
      response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', ...options,
        headers: {...options.headers, ...(authenticated ? {Authorization: `Bearer ${token}`} : {})}});
    } catch (_) {
      if (started !== generation) throw failure('', 'stale');
      if (action === 'decision') throw failure('We couldn’t confirm the connection. Return to your AI app to check its status before connecting again.', 'restart');
      throw failure('Unable to reach PathSix. Check your connection and try again.');
    }
    const result = await response.json().catch(() => null);
    if (started !== generation) throw failure('', 'stale');
    if (!response.ok) {
      if (action === 'decision' && result?.restart_required) {
        throw failure(result.reason === 'expired_intent' ? expiredMessage :
          'This connection request is no longer valid. Return to your AI app and choose Connect or Sign in again.', 'restart');
      }
      if (response.status === 429) throw failure('Too many attempts. Wait a minute, then try again.');
      if (response.status === 401) {
        if (action === 'login') throw failure(staging ?
          'We couldn’t sign you in. Use your staging test email and password; your live CRM account is separate.' :
          'We couldn’t sign you in. Check your PathSix email and password.');
        throw failure('Your sign-in is no longer valid. Sign in again to continue.', 'signin');
      }
      if (response.status === 403) throw failure('This account can’t complete this action. Check your PathSix permissions or use a different account.');
      if (action === 'decision') throw failure('We couldn’t complete the connection. Return to your AI app and start the connection again.', 'restart');
      if (response.status >= 500 || !result) throw failure('PathSix is temporarily unavailable. Please try again shortly.');
      throw failure('Unable to complete this action. Please try again.');
    }
    if (action === 'decision' && typeof result?.redirect !== 'string') {
      throw failure('We couldn’t confirm the connection. Return to your AI app to check its status before connecting again.', 'restart');
    }
    if (!result) throw failure('PathSix returned an unexpected response. Please try again.');
    return result;
  };
  const send = (url, data, action, authenticated = false) => requestJson(url,
    {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)}, action, authenticated);
  const loadConnections = async () => {
    const result = await requestJson(`/api/ai-connections?page=${page}&per_page=20`, {}, 'connections', true);
    const container = document.querySelector('#connections');
    if (page === 1) container.replaceChildren();
    if (!result.total) container.textContent = 'You haven’t connected an AI client yet.';
    result.connections.forEach(connection => {
      const section = document.createElement('section');
      const title = document.createElement('h2'); title.textContent = connection.client_id;
      const detail = document.createElement('p');
      detail.textContent = `${connection.status} · ${connection.scopes.join(', ')} · Expires ${new Date(connection.expires_at).toLocaleDateString()}`;
      section.append(title, detail);
      if (connection.status === 'active') {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary'; button.textContent = 'Revoke access';
        button.addEventListener('click', async () => {
          button.disabled = true;
          try {
            await requestJson(`/api/ai-connections/${encodeURIComponent(connection.id)}`, {method: 'DELETE'}, 'revoke', true);
            button.remove(); detail.textContent = 'Revoked'; message.textContent = 'Access revoked.';
          } catch (error) { handleError(error); button.disabled = false; }
        });
        section.append(button);
      }
      container.append(section);
    });
    document.querySelector('#more').hidden = page * 20 >= result.total;
  };
  login.addEventListener('submit', async event => {
    event.preventDefault();
    if (checkExpiry()) return;
    const button = login.querySelector('button'); button.disabled = true; message.textContent = '';
    try {
      const result = await send('/api/login', {email: login.email.value, password: login.password.value}, 'login');
      if (checkExpiry()) return;
      token = result.token; login.password.value = '';
      if (managing) { page = 1; await loadConnections(); }
      else await send('/api/ai-connections/preview', {client_id: root.dataset.client, scopes: root.dataset.scopes.split(' ')}, 'preview', true);
      if (checkExpiry()) return;
      document.querySelector('#identity').textContent = `${result.user.email} · ${result.tenant.name}`;
      login.hidden = true; approval.hidden = false;
      if (!managing) document.querySelector('#approve').focus();
    } catch (error) { token = null; handleError(error); }
    finally { button.disabled = false; }
  });
  const decide = async approved => {
    if (checkExpiry() || deciding) return;
    deciding = true;
    const buttons = approval.querySelectorAll('button'); buttons.forEach(button => button.disabled = true);
    message.textContent = '';
    try {
      const result = await send('/oauth/decision', {intent: root.dataset.intent, approved}, 'decision', true);
      token = null; window.location.replace(result.redirect);
    } catch (error) { handleError(error); }
    finally { deciding = false; buttons.forEach(button => button.disabled = false); checkExpiry(); }
  };
  if (managing) document.querySelector('#more').addEventListener('click', async event => {
    event.target.disabled = true; page += 1;
    try { await loadConnections(); } catch (error) { page = Math.max(1, page - 1); handleError(error); }
    finally { event.target.disabled = false; }
  });
  else {
    document.querySelector('#approve').addEventListener('click', () => decide(true));
    document.querySelector('#deny').addEventListener('click', () => decide(false));
  }
  document.querySelector('#switch-account')?.addEventListener('click', () => {
    showLogin(); message.textContent = ''; checkExpiry();
  });
  if (!managing) {
    window.setTimeout(checkExpiry, Math.max(0, deadline - Date.now()));
    document.addEventListener('visibilitychange', checkExpiry);
    window.addEventListener('pageshow', checkExpiry);
  }
  window.addEventListener('pagehide', () => { showLogin(false); login.reset(); });
})();
