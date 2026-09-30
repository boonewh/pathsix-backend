'use strict';
(() => {
  const root = document.querySelector('main');
  const login = document.querySelector('#login');
  const approval = document.querySelector('#approval');
  const message = document.querySelector('#message');
  let token = null;
  let page = 1;
  const managing = root.dataset.mode === 'manage';
  const send = async (url, data, authenticated = false) => {
    const response = await fetch(url, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', ...(authenticated ? {Authorization: `Bearer ${token}`} : {})},
      body: JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error('Unable to continue. Check your sign-in details or restart the connection from your AI client.');
    return result;
  };
  const loadConnections = async () => {
    const response = await fetch(`/api/ai-connections?page=${page}&per_page=20`, {
      headers: {Authorization: `Bearer ${token}`}, cache: 'no-store', credentials: 'same-origin'});
    if (!response.ok) throw new Error('Unable to load connections. Please sign in again.');
    const result = await response.json();
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
            const revoked = await fetch(`/api/ai-connections/${encodeURIComponent(connection.id)}`, {
              method: 'DELETE', headers: {Authorization: `Bearer ${token}`}, cache: 'no-store', credentials: 'same-origin'});
            if (!revoked.ok) throw new Error('Unable to revoke access. Please try again.');
            button.remove(); detail.textContent = 'Revoked'; message.textContent = 'Access revoked.';
          } catch (error) { message.textContent = error.message; button.disabled = false; }
        });
        section.append(button);
      }
      container.append(section);
    });
    document.querySelector('#more').hidden = page * 20 >= result.total;
  };
  login.addEventListener('submit', async event => {
    event.preventDefault();
    const button = login.querySelector('button'); button.disabled = true; message.textContent = '';
    try {
      const result = await send('/api/login', {email: login.email.value, password: login.password.value});
      token = result.token; login.password.value = '';
      if (managing) { page = 1; await loadConnections(); }
      else await send('/api/ai-connections/preview', {client_id: root.dataset.client, scopes: root.dataset.scopes.split(' ')}, true);
      document.querySelector('#identity').textContent = `${result.user.email} · ${result.tenant.name}`;
      login.hidden = true; approval.hidden = false;
      if (!managing) document.querySelector('#approve').focus();
    } catch (error) { token = null; message.textContent = error.message; }
    finally { button.disabled = false; }
  });
  const decide = async approved => {
    const buttons = approval.querySelectorAll('button'); buttons.forEach(button => button.disabled = true);
    message.textContent = '';
    try {
      const result = await send('/oauth/decision', {intent: root.dataset.intent, approved}, true);
      token = null; window.location.replace(result.redirect);
    } catch (error) { message.textContent = error.message; buttons.forEach(button => button.disabled = false); }
  };
  if (managing) document.querySelector('#more').addEventListener('click', async event => {
    event.target.disabled = true; page += 1;
    try { await loadConnections(); } catch (error) { page -= 1; message.textContent = error.message; }
    finally { event.target.disabled = false; }
  });
  else {
    document.querySelector('#approve').addEventListener('click', () => decide(true));
    document.querySelector('#deny').addEventListener('click', () => decide(false));
  }
  window.addEventListener('pagehide', () => { token = null; login.reset(); });
})();
