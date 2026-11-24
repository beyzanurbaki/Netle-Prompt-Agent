const inbox = id => document.querySelector('#inbox-' + id);
const refundCases = new Map();
let selectedRefundMail = null;
let inboxMails = [];
let showClosedCases = false;
let inboxRefresh = null;
let inboxPolling = null;

function mailVersion(mail) {
  return mail.thread?.message_id || mail.messages?.at(-1)?.id || mail.id;
}

function needsOrderCheck(mail) {
  return mail.id.startsWith('gmail-') && mail.order_check?.status !== 'verified';
}

function mailCard(label, text, style) {
  const card = document.createElement('div');
  card.className = 'inbox-message ' + style;
  const title = document.createElement('strong');
  title.textContent = label;
  card.append(title, text);
  return card;
}

function isClosedCase(mail) {
  const entry = refundCases.get(mail.id);
  return mail.delivery_state === 'sent' || (entry?.version === mailVersion(mail) && Boolean(entry.sent));
}

function renderInboxList() {
  const visibleMails = inboxMails.filter(mail => isClosedCase(mail) === showClosedCases);
  inbox('title').textContent = showClosedCases ? 'Kapanan iade talepleri' : 'Açık iade talepleri';
  const visible = !document.getElementById('refund-work').hidden;
  inbox('open').setAttribute('aria-pressed', String(visible && !showClosedCases));
  inbox('closed').setAttribute('aria-pressed', String(visible && showClosedCases));
  const empty = inbox('filter-empty');
  empty.hidden = !inboxMails.length || Boolean(visibleMails.length);
  empty.textContent = showClosedCases ? 'Kapanan iade talebi yok.' : 'Açık iade talebi yok.';
  const mail = selectedRefundMail;
  inbox('list').replaceChildren(...visibleMails.map(sample => {
    const saved = refundCases.get(sample.id);
    const entry = saved?.version === mailVersion(sample) ? saved : null;
    const button = document.createElement('button');
    button.className = 'inbox-mail';
    button.type = 'button';
    button.setAttribute('aria-pressed', String(sample.id === mail?.id));
    const subject = document.createElement('strong');
    subject.textContent = sample.subject;
    const state = document.createElement('small');
    const sent = isClosedCase(sample);
    state.textContent = sent ? 'Gönderildi'
      : sample.delivery_state === 'uncertain' ? 'Gönderim belirsiz'
      : needsOrderCheck(sample) ? 'Sipariş kontrolü gerekli'
      : entry?.busy ? 'İşleniyor' : entry?.error ? 'İşlem bekliyor'
      : entry?.hasDraft ? 'Taslak hazır' : entry?.questions ? 'Bilgi bekliyor'
      : entry?.readyForDraft || sample.followup ? 'Yanıt bekliyor' : 'Yeni';
    button.dataset.tone = sent ? 'success'
      : sample.delivery_state === 'uncertain' || needsOrderCheck(sample) || entry?.error ? 'warn'
      : entry?.hasDraft ? 'brand' : entry?.busy || entry?.questions ? 'info' : '';
    if (sent) {
      const check = document.createElement('span');
      check.setAttribute('aria-hidden', 'true');
      check.textContent = '✓ ';
      state.className = 'inbox-mail-sent';
      state.prepend(check);
    }
    button.append(subject, state);
    button.addEventListener('click', () => openRefundMail(sample));
    return button;
  }));
}

function renderInbox() {
  renderInboxList();
  const mail = selectedRefundMail;
  const active = mail && refundCases.get(mail.id);
  inbox('conversation-panel').hidden = !active;
  inbox('response-panel').hidden = !active;
  inbox('case').hidden = !active;
  inbox('draft-empty').hidden = Boolean(active?.hasDraft);
  inbox('draft-panel').hidden = !active?.hasDraft;
  inbox('draft-empty').textContent = !active ? 'Yanıt hazırlamak için bir iade e-postası seç.'
    : active.sent ? 'Bu e-postaya yanıt gönderildi.'
    : needsOrderCheck(mail) && !active.busy ? 'Sipariş bilgilerini kontrol et.'
    : mail.delivery_state === 'uncertain' ? 'Gönderim sonucu belirsiz. Posta kutusunu kontrol et.'
    : active.error ? 'Sohbet panelindeki işlem durumunu kontrol et.'
    : active.busy ? 'Netle çalışıyor…'
    : active.questions ? 'Taslak için sohbet panelindeki soruları yanıtla.'
    : 'Netle önerisi hazırlanıyor…';
  if (!active) {
    for (const field of ['recipient', 'reply-subject', 'draft', 'message']) inbox(field).value = '';
    for (const field of ['thread', 'questions', 'sources']) inbox(field).replaceChildren();
    return;
  }

  inbox('subject').textContent = mail.subject;
  inbox('sender').textContent = mail.sender;
  const order = mail.order_check;
  inbox('order-check').dataset.status = order?.status || '';
  inbox('order-check').hidden = !mail.id.startsWith('gmail-');
  const windowStatus = inbox('return-window');
  const withinWindow = order?.within_return_window;
  windowStatus.hidden = order?.status !== 'verified';
  windowStatus.dataset.window = withinWindow === true ? 'yes' : withinWindow === false ? 'no' : 'unknown';
  windowStatus.textContent = withinWindow === true
    ? '✓ İade ve değişime uygun · 14 günlük süre içinde'
    : withinWindow === false
      ? '✕ İade ve değişime uygun değil · 14 günlük süre aşıldı'
      : '14 günlük süre hesaplanamadı';
  windowStatus.title = 'Bu gösterge yalnızca 14 günlük süreyi değerlendirir. Ayıplı teslimat ayrıca incelenir.';
  inbox('order-title').textContent = order?.status === 'verified' ? 'Sipariş eşleşti'
    : order?.status === 'unmatched' ? 'Sipariş eşleşmedi' : 'Sipariş kontrolü gerekli';
  const record = order?.record;
  const dateLabel = value => value || 'Kayıt yok';
  inbox('order-summary').textContent = [
    order?.message || 'Bu e-postanın sipariş bilgileri doğrulanamadı.',
    ...(record ? [record.order_id + ' · ' + record.product,
      'Satın alma: ' + dateLabel(record.purchase_date),
      'Teslimat: ' + dateLabel(record.delivery_date)] : [])
  ].join('\n');
  inbox('original').replaceChildren(mailCard('Müşteri mesajı', mail.body, 'customer'));
  const quoted = inbox('quoted');
  const quotedBody = inbox('quoted-body');
  if (quotedBody.textContent !== (mail.quoted_body || '')) quoted.open = false;
  quoted.hidden = !mail.quoted_body;
  quotedBody.textContent = mail.quoted_body || '';
  inbox('attachments').hidden = !mail.attachments?.length;
  inbox('attachments').textContent = mail.attachments?.join(', ') || '';
  const earlier = mail.messages?.slice(0, -1) || [];
  inbox('history').hidden = !earlier.length;
  inbox('history-messages').replaceChildren(...earlier.flatMap(previous => {
    const cards = [mailCard('Müşteri mesajı · ' + previous.sender, previous.body, 'customer')];
    if (previous.sent_reply) cards.push(mailCard('Gönderdiğin yanıt', previous.sent_reply, 'sent-reply'));
    return cards;
  }));
  const sentReply = mail.sent_reply || active.sentReply;
  inbox('sent-reply').replaceChildren(...(sentReply
    ? [mailCard('Gönderdiğin yanıt', sentReply, 'sent-reply')] : []));
  const thread = inbox('thread');
  thread.replaceChildren(...active.turns.map(({employee, text}) => {
    const turn = document.createElement('p');
    turn.className = employee ? 'inbox-turn employee' : 'inbox-turn';
    if (employee) {
      turn.textContent = 'Sen\n' + text;
    } else {
      const heading = document.createElement('strong');
      heading.textContent = 'NETLE';
      turn.append(heading, text);
    }
    return turn;
  }));
  thread.scrollTop = thread.scrollHeight;
  inbox('status').textContent = active.error || (active.busy ? 'Netle çalışıyor…' : '');
  inbox('status').className = active.error ? 'error' : '';
  inbox('retry').hidden = active.busy || !active.retry;
  inbox('chat').hidden = !active.sessionId || needsOrderCheck(mail);
  inbox('revise').hidden = !active.hasDraft;
  inbox('answers').hidden = !active.questions || needsOrderCheck(mail);
  inbox('questions').replaceChildren(...Object.entries(active.questions || {}).map(([key, question]) => {
    const group = document.createElement('div');
    const field = document.createElement('label');
    field.textContent = question;
    const isDeliveryDate = key === 'teslimattan_sonra_gun';
    const input = document.createElement(isDeliveryDate ? 'input' : 'select');
    input.name = key;
    input.required = true;
    if (isDeliveryDate) {
      input.type = 'date';
      input.max = todayDateInput();
    } else {
      for (const [label, value] of [['Seç', ''], ['Evet', 'true'], ['Hayır', 'false']]) {
        input.add(new Option(label, value));
      }
    }
    input.value = active.answers[key] ?? '';
    field.append(input);
    group.append(field);
    if (isDeliveryDate) group.append(deliveryUnknownOption(active.answers.teslim_tarihi_bilinmiyor));
    return group;
  }));
  inbox('message').value = active.message;
  inbox('recipient').value = active.recipient;
  inbox('reply-subject').value = active.subject;
  inbox('draft').value = active.draft;
  sizeInboxDraft();
  showList(inbox('sources'), active.sources);
  inbox('policy-sources').hidden = !active.sources.length;
  for (const field of inbox('case').querySelectorAll('form button, form select, form input, form textarea')) {
    field.disabled = active.busy;
  }
  updateDeliveryFields(inbox('answers'));
  for (const field of ['recipient', 'reply-subject', 'draft']) {
    inbox(field).readOnly = active.busy || Boolean(active.sent);
  }
  if (mail.thread) inbox('reply-subject').readOnly = true;
  if (mail.order_check?.status === 'verified' || active.correctionOnly) inbox('recipient').readOnly = true;
  if (active.correctionOnly) inbox('draft').readOnly = true;
  const missingThread = mail.id.startsWith('gmail-') && !mail.thread;
  inbox('send').disabled = active.busy || Boolean(active.sent) || missingThread || (needsOrderCheck(mail) && !active.correctionOnly)
    || mail.delivery_state === 'uncertain';
  inbox('revise').disabled = active.busy || Boolean(active.sent) || Boolean(active.correctionOnly);
  inbox('send-status').textContent = active.sendStatus || (missingThread
    ? 'Özgün e-postada zincir kimliği bulunamadığı için yanıt gönderilemiyor.'
    : mail.thread ? 'Yanıt, özgün e-posta zincirine bağlanacak.' : '');
}

function sizeInboxDraft() {
  const draft = inbox('draft');
  draft.style.height = 'auto';
  if (draft.scrollHeight) draft.style.height = (draft.scrollHeight + 2) + 'px';
}

function openRefundMail(mail) {
  selectedRefundMail = mail;
  const previous = refundCases.get(mail.id);
  const version = mailVersion(mail);
  const orderBlocked = needsOrderCheck(mail);
  const orderSnapshot = JSON.stringify(mail.order_check?.record ?? null);
  if (previous?.version === version && previous.orderBlocked === orderBlocked
      && previous.orderSnapshot === orderSnapshot) {
    renderInbox();
    return;
  }
  const entry = {
    version, orderBlocked, orderSnapshot, busy: false, turns: previous?.turns.slice() || [],
    sources: [], answers: {}, message: '', draft: '',
    subject: /^re:/i.test(mail.subject) ? mail.subject : 'Re: ' + mail.subject,
    recipient: mail.thread ? mail.sender_address : mail.recipient
  };
  refundCases.set(mail.id, entry);
  const canRequestCorrection = mail.thread && mail.sender_address
    && ['unmatched', 'review'].includes(mail.order_check?.status);
  if (mail.delivery_state || (orderBlocked && !canRequestCorrection)) {
    entry.sent = mail.delivery_state === 'sent';
    renderInbox();
    return;
  }
  const previousMessages = mail.messages?.slice(0, -1) || [];
  const request = mail.followup
    ? (mail.body.trim() || mail.subject).slice(0, 12000)
    : mail.id.startsWith('gmail-') ? (mail.subject + '\n\n' + mail.body).slice(0, 12000) : mail.body;
  if (mail.followup) entry.turns.push({text: 'Müşterinin yeni mesajı bu vakaya eklendi.'});
  runInboxRequest(mail, {
    action: 'start', request, defer_draft: true, followup: Boolean(mail.followup),
    gmail_message_id: mail.thread?.message_id,
    previous_message_ids: previousMessages.map(item => item.thread?.message_id).filter(Boolean)
  });
}

async function runInboxRequest(mail, payload, employeeTurn) {
  const entry = refundCases.get(mail.id);
  if (!entry || entry.busy) return;
  entry.busy = true;
  entry.error = '';
  entry.retry = null;
  let generateNext = false;
  renderInbox();
  try {
    const data = await post('/api/refund', payload);
    if (refundCases.get(mail.id) !== entry) return;
    if (employeeTurn) entry.turns.push({employee: true, text: employeeTurn});
    if (payload.action === 'discuss' || payload.action === 'revise') {
      entry.message = '';
      entry.turns.push({text: data.message || 'Yanıt taslağı güncellendi.'});
    } else {
      entry.sessionId = data.session_id;
      entry.correctionOnly = Boolean(data.correction_only);
      entry.questions = data.questions;
      entry.readyForDraft = Boolean(data.ready_for_draft);
      generateNext = entry.readyForDraft && payload.action !== 'generate';
      entry.answers = {};
      entry.turns.push({text: data.questions
        ? Object.values(data.questions).join('\n')
        : data.ready_for_draft ? 'Netle yanıt taslağını hazırlıyor.'
        : 'Yanıt taslağı sağ panelde hazır.'});
    }
    if (typeof data.draft === 'string') {
      entry.hasDraft = true;
      entry.draft = data.draft;
      entry.sources = data.sources;
    } else if (data.message && data.sources.length) {
      entry.turns[entry.turns.length - 1].text += '\n\n' + data.sources.join('\n');
    }
  } catch (error) {
    entry.error = error.message;
    if (['start', 'resume', 'generate'].includes(payload.action)) {
      entry.retry = {payload, employeeTurn};
    }
  } finally {
    entry.busy = false;
    if (refundCases.get(mail.id) === entry && !generateNext) renderInbox();
  }
  if (generateNext && refundCases.get(mail.id) === entry) {
    await runInboxRequest(mail, {action: 'generate', session_id: entry.sessionId});
  }
}

function startInboxPolling() {
  if (inboxPolling !== null) return;
  inboxPolling = setInterval(() => {
    if (!document.body.classList.contains('password-only')) {
      void refreshInbox({automatic: true});
    }
  }, 30_000);
}

function resetInbox() {
  clearInterval(inboxPolling);
  inboxPolling = null;
  refundCases.clear();
  showClosedCases = false;
  selectedRefundMail = null;
  inboxMails = [];
  inboxRefresh = null;
  inbox('source').textContent = 'Gmail · Netle İade';
  inbox('load-status').textContent = '';
  inbox('refresh').disabled = false;
  renderInbox();
}

async function refreshInbox({automatic = false} = {}) {
  if (inboxRefresh) return;
  const refresh = {};
  inboxRefresh = refresh;
  inbox('refresh').disabled = true;
  inbox('load-status').textContent = 'Gelen mailler kontrol ediliyor…';
  try {
    const data = await post('/api/inbox', {});
    if (inboxRefresh !== refresh) return;
    if (!data.enabled) {
      inbox('load-status').textContent = 'Bu çalışan için Gmail bağlantısı yapılandırılmadı.';
      return;
    }
    const previousSelection = selectedRefundMail;
    const nextSelection = data.mails.find(mail => mail.id === previousSelection?.id) ?? null;
    const selectionChanged = JSON.stringify(previousSelection) !== JSON.stringify(nextSelection);
    if (automatic && selectionChanged && refundCases.get(previousSelection?.id)?.busy) return;
    const listChanged = JSON.stringify(inboxMails) !== JSON.stringify(data.mails);
    inboxMails = data.mails;
    for (const mail of inboxMails) {
      const entry = refundCases.get(mail.id);
      if (entry?.version === mailVersion(mail) && mail.delivery_state === 'sent') {
        entry.sent = true;
        entry.sendStatus = 'Bu e-postaya yanıt gönderildi.';
      }
    }
    selectedRefundMail = nextSelection;
    if (nextSelection) showClosedCases = isClosedCase(nextSelection);
    inbox('source').textContent = 'Gmail · Netle İade';
    inbox('load-status').textContent = inboxMails.length ? '' : 'Bu etikette henüz mail yok.';
    if (!automatic || selectionChanged) {
      if (selectedRefundMail) openRefundMail(selectedRefundMail);
      else renderInbox();
    } else if (listChanged) {
      renderInboxList();
    }
  } catch (error) {
    if (inboxRefresh === refresh) inbox('load-status').textContent = error.message;
  } finally {
    if (inboxRefresh === refresh) {
      inboxRefresh = null;
      inbox('refresh').disabled = false;
    }
  }
}

for (const [filter, closed] of [['open', false], ['closed', true]]) {
  inbox(filter).addEventListener('click', () => {
    if (showClosedCases === closed) return;
    showClosedCases = closed;
    selectedRefundMail = null;
    renderInbox();
  });
}

inbox('refresh').addEventListener('click', () => refreshInbox());

inbox('retry').addEventListener('click', () => {
  const retry = refundCases.get(selectedRefundMail.id).retry;
  runInboxRequest(selectedRefundMail, retry.payload, retry.employeeTurn);
});

inbox('answers').addEventListener('input', event => {
  const input = event.target;
  refundCases.get(selectedRefundMail.id).answers[input.name] =
    input.type === 'checkbox' ? input.checked : input.value;
  updateDeliveryFields(event.currentTarget);
});

inbox('answers').addEventListener('submit', event => {
  event.preventDefault();
  const entry = refundCases.get(selectedRefundMail.id);
  const answers = refundAnswers(event.currentTarget);
  const labels = [];
  for (const [key, value] of Object.entries(answers)) {
    if (key === 'teslim_tarihi') {
      labels.push('Teslim tarihi: ' + value.split('-').reverse().join('/'));
    } else if (key === 'teslim_tarihi_bilinmiyor') {
      labels.push('Teslim tarihi: Bilmiyorum.');
    } else {
      labels.push(entry.questions[key] + ' ' + (value ? 'Evet' : 'Hayır'));
    }
  }
  runInboxRequest(selectedRefundMail, {action: 'resume', session_id: entry.sessionId, answers},
    labels.join('\n'));
});

inbox('chat').addEventListener('submit', event => {
  event.preventDefault();
  const entry = refundCases.get(selectedRefundMail.id);
  const message = entry.message.trim();
  if (!message) return;
  const action = event.submitter?.name === 'revise' ? 'revise' : 'discuss';
  const recentNotes = entry.turns.filter(turn => turn.employee).map(turn => turn.text).join('\n');
  runInboxRequest(selectedRefundMail, {
    action, session_id: entry.sessionId, message, recent_notes: recentNotes.slice(-2400),
    current_draft: action === 'revise' ? entry.draft : ''
  }, message);
});

for (const [field, property] of [['message', 'message'], ['recipient', 'recipient'],
  ['reply-subject', 'subject'], ['draft', 'draft']]) {
  inbox(field).addEventListener('input', event => {
    refundCases.get(selectedRefundMail.id)[property] = event.target.value;
    if (field === 'draft') sizeInboxDraft();
  });
}

inbox('draft-panel').addEventListener('submit', async event => {
  event.preventDefault();
  const mail = selectedRefundMail;
  const entry = mail && refundCases.get(mail.id);
  if (!entry?.hasDraft || entry.busy || entry.sent || (needsOrderCheck(mail) && !entry.correctionOnly) || mail.delivery_state === 'uncertain'
      || (mail.id.startsWith('gmail-') && !mail.thread)) return;
  entry.busy = true;
  entry.sendStatus = 'E-posta gönderiliyor…';
  renderInbox();
  try {
    const data = await post('/api/mail', {
      session_id: entry.sessionId, recipient: entry.recipient, subject: entry.subject,
      body: entry.draft, thread: mail.thread
    });
    entry.sent = data.sent;
    if (entry.sent && selectedRefundMail?.id === mail.id && refundCases.get(mail.id) === entry) {
      showClosedCases = true;
    }
    if (data.sent && !data.already_sent) entry.sentReply = entry.draft;
    entry.sendStatus = 'E-posta gönderildi.';
  } catch (error) {
    entry.sendStatus = error.message;
  } finally {
    entry.busy = false;
    if (refundCases.get(mail.id) === entry) renderInbox();
  }
});
