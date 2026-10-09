'use strict';
(() => {
  const root = document.querySelector('main');
  const login = document.querySelector('#login');
  const review = document.querySelector('#review');
  const message = document.querySelector('#message');
  const decisions = document.querySelector('#decisions');
  const fields = document.querySelector('#fields');
  const base = `/oauth/actions/${encodeURIComponent(root.dataset.action)}`;
  let token = null, intent = null, busy = false, generation = 0;
  const labels = {name:'Company',contact_person:'Contact person',contact_title:'Contact title',
    email:'Email',phone:'Phone',phone_label:'Phone label',secondary_phone:'Secondary phone',
    secondary_phone_label:'Secondary phone label',address:'Address',city:'City',state:'State',
    zip:'Postal code',notes:'Notes',type:'Business type',lead_status:'Lead status',lead_source:'Lead source'};
  const reset = () => {
    generation += 1; token = null; intent = null;
    fields.replaceChildren(); document.querySelector('#identity').textContent = '';
    decisions.hidden = true; review.hidden = true; login.hidden = false;
    login.password.value = ''; login.email.focus();
  };
  const locked = value => {
    busy = value;
    root.querySelectorAll('button').forEach(button => { button.disabled = value; });
  };
  const send = async (url, body, authenticated = true) => {
    const started = generation;
    const response = await fetch(url, {method:'POST', credentials:'same-origin', cache:'no-store',
      headers:{'Content-Type':'application/json', ...(authenticated ? {Authorization:`Bearer ${token}`} : {})},
      body:JSON.stringify(body)});
    const result = await response.json().catch(() => null);
    if (started !== generation) throw new Error('This page changed. Sign in again to check the status.');
    if (!response.ok || !result) {
      if (response.status === 401) {
        reset();
        throw new Error(authenticated ? 'Sign in again to check this proposal.' :
          root.dataset.staging === 'true' ? 'Use your staging test email and password.' : 'Check your PathSix email and password.');
      }
      throw new Error(response.status >= 500 || !result ? 'PathSix is temporarily unavailable.' :
        typeof result.error === 'string' ? result.error : 'Unable to complete this request.');
    }
    return result;
  };
  const display = result => {
    intent = null; decisions.hidden = true; fields.replaceChildren();
    document.querySelector('#expiry').textContent = '';
    document.querySelector('#guidance').hidden = result.status !== 'pending';
    if (result.status === 'pending' && result.intent && result.lead) {
      intent = result.intent;
      Object.entries(labels).forEach(([key,label]) => {
        const value = result.lead[key];
        if (value === null || value === undefined || value === '') return;
        const term = document.createElement('dt'); term.textContent = label;
        const detail = document.createElement('dd'); detail.textContent = String(value);
        fields.append(term,detail);
      });
      document.querySelector('#expiry').textContent = `Proposal expires ${new Date(result.expires_at).toLocaleString()}.`;
      decisions.hidden = false;
      message.textContent = 'Review all details before creating the lead.';
    } else if (result.status === 'committed' && result.lead) {
      message.textContent = `Lead created: ${result.lead.name} (ID ${result.lead.id}). Return to ChatGPT to continue.`;
    } else if (result.status === 'cancelled') {
      message.textContent = 'Proposal cancelled. No lead was created by this proposal.';
    } else if (result.status === 'expired') {
      message.textContent = 'This proposal expired without creating a lead. Ask ChatGPT to prepare a new proposal.';
    } else throw new Error('Unexpected response. Refresh the status before continuing.');
  };
  const load = async () => display(await send(base+'/preview', {}));
  login.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return; locked(true); message.textContent = '';
    try {
      const result = await send('/api/login', {email:login.email.value,password:login.password.value}, false);
      if (typeof result.token !== 'string') throw new Error('Unable to confirm sign-in.');
      token = result.token; login.password.value = '';
      await load();
      document.querySelector('#identity').textContent = `${result.user.email} · ${result.tenant.name}`;
      login.hidden = true; review.hidden = false;
      (decisions.hidden ? message : document.querySelector('#cancel')).focus();
    } catch (error) { reset(); message.textContent = error.message || 'Unable to reach PathSix.'; }
    finally { locked(false); }
  });
  const decide = async approved => {
    if (busy || !intent) return;
    const reviewed = intent; intent = null; decisions.hidden = true; locked(true);
    try { display(await send(base+'/decision', {intent:reviewed,approved})); }
    catch (error) {
      message.textContent = `${error.message || 'The connection was interrupted.'} Refresh status to find out whether the action completed before trying again.`;
    } finally { locked(false); message.focus(); }
  };
  document.querySelector('#confirm').addEventListener('click', () => decide(true));
  document.querySelector('#cancel').addEventListener('click', () => decide(false));
  document.querySelector('#refresh').addEventListener('click', async () => {
    if (busy) return; intent = null; decisions.hidden = true; locked(true);
    try { await load(); } catch (error) { message.textContent = error.message || 'Unable to reach PathSix.'; }
    finally { locked(false); }
  });
  document.querySelector('#switch').addEventListener('click', () => { if (!busy) { reset(); message.textContent = ''; } });
  window.addEventListener('pagehide', reset);
})();
