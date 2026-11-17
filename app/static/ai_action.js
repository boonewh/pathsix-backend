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
  // Wording comes from the server per action kind; these defaults match create_lead.
  let pres = {noun:'lead', title:'Create this lead?', confirm:'Create lead', done:'Lead created', receipt:'lead'};
  const heading = (eyebrow, title, intro, outcome = '', kind = '') => {
    document.querySelector('#eyebrow').textContent = eyebrow;
    document.querySelector('#title').textContent = title;
    document.querySelector('#intro').textContent = intro;
    const box = document.querySelector('#outcome');
    box.textContent = outcome; box.className = kind; box.hidden = !outcome;
  };
  const awaiting = () => heading('REVIEW BEFORE SAVING', pres.title,
    `Review the details proposed by your AI app. Nothing is saved to your CRM until you choose ${pres.confirm}.`);
  const reset = () => {
    generation += 1; token = null; intent = null; awaiting();
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
      if (response.status === 404) throw new Error('This review link doesn’t match a proposal for this account. ' +
        'Ask your AI app to show the review link again, and open it exactly as given. If you have more than one PathSix account, check that you signed in with the right one.');
      throw new Error(response.status >= 500 || !result ? 'PathSix is temporarily unavailable.' :
        typeof result.error === 'string' ? result.error : 'Unable to complete this request.');
    }
    return result;
  };
  const display = result => {
    intent = null; decisions.hidden = true; fields.replaceChildren();
    document.querySelector('#expiry').textContent = '';
    document.querySelector('#guidance').hidden = result.status !== 'pending';
    if (result.presentation) pres = {...pres, ...result.presentation};
    document.querySelector('#confirm').textContent = pres.confirm;
    const record = result[pres.receipt];
    if (result.status === 'pending' && result.intent && Array.isArray(result.presentation?.fields)) {
      intent = result.intent;
      result.presentation.fields.forEach(([label, value]) => {
        const term = document.createElement('dt'); term.textContent = label;
        const detail = document.createElement('dd'); detail.textContent = String(value);
        fields.append(term,detail);
      });
      document.querySelector('#expiry').textContent = `Proposal expires ${new Date(result.expires_at).toLocaleString()}.`;
      decisions.hidden = false;
      awaiting();
      message.textContent = `Review all details before you choose ${pres.confirm}.`;
    } else if (result.status === 'committed' && record) {
      heading('SAVED', pres.done, 'This proposal is complete. You can close this page and return to your AI app.',
        `✓ ${record.name} was saved to your CRM (${pres.noun} ID ${record.id}).`, 'success');
      message.textContent = '';
    } else if (result.status === 'cancelled') {
      heading('CANCELLED', 'Proposal cancelled', 'Nothing was saved. You can close this page.',
        `No ${pres.noun} was created by this proposal.`, 'neutral');
      message.textContent = '';
    } else if (result.status === 'expired') {
      heading('EXPIRED', 'Proposal expired', 'Nothing was saved. Ask your AI app to prepare a new proposal.',
        `No ${pres.noun} was created by this proposal.`, 'neutral');
      message.textContent = '';
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
    } finally {
      locked(false);
      const outcome = document.querySelector('#outcome');
      (outcome.hidden ? message : outcome).focus();
    }
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
