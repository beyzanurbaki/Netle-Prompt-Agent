const sampleRefundMails = [
  {
    id: 'coffee',
    sender: 'Deniz Kaya <deniz@example.test>',
    recipient: 'deniz@example.test',
    subject: 'Kahve makinesi iade talebi',
    body: 'Merhaba, kahve makinesini 25 gün önce satın aldım. Kutuyu bugün açtım. Ürün kullanılmadı. İade etmek istiyorum.'
  },
  {
    id: 'plates',
    sender: 'Ece Demir <ece@example.test>',
    recipient: 'ece@example.test',
    subject: 'Kırık gelen seramik tabak',
    body: 'Merhaba, seramik tabağım kırık geldi. Ürünü iade etmek istiyorum. Sipariş numarası NL-1042. Ürün fotoğrafı var.'
  },
  {
    id: 'earphones',
    sender: 'Mert Akın <mert@example.test>',
    recipient: 'mert@example.test',
    subject: 'Kulaklık iade koşulları',
    body: 'Merhaba, hijyen mühürlü kulaklığımı iade etmek istiyorum. Ürün dün teslim edildi, kullanılmadı ve aksesuarları eksiksiz.'
  }
];

const inbox = id => document.querySelector('#inbox-' + id);
const refundCases = new Map();
const employeeResponses = {
  'missing-document': {
    label: 'Eksik belge',
    message: detail => 'İade talebinizi aldık. İncelemeye devam edebilmemiz için aşağıdaki belgeyi bu e-postayı yanıtlayarak paylaşabilir misiniz?\n\n' + detail
  },
  rejected: {
    label: 'Ret gerekçesi',
    message: detail => 'İade talebinizi inceledik. Talebiniz aşağıdaki gerekçeyle onaylanmadı.\n\n' + detail
  },
  approved: {message: () => 'İade talebinizi inceledik ve onayladık.'}
};
let selectedRefundMail = null;
let inboxMails = sampleRefundMails;
let inboxRefresh = null;

function mailVersion(mail) {
  return mail.thread?.message_id || mail.messages?.at(-1)?.id || mail.id;
}

function mailCard(label, text, style) {
  const card = document.createElement('div');
  card.className = 'inbox-message ' + style;
  const title = document.createElement('strong');
  title.textContent = label;
  card.append(title, text);
  return card;
}

function renderInbox() {
  const mail = selectedRefundMail;
  const active = mail && refundCases.get(mail.id);
  inbox('list').replaceChildren(...inboxMails.map(sample => {
    const saved = refundCases.get(sample.id);
    const entry = saved?.version === mailVersion(sample) ? saved : null;
    const button = document.createElement('button');
    button.className = 'inbox-mail';
    button.type = 'button';
    button.setAttribute('aria-pressed', String(sample.id === mail?.id));
    const subject = document.createElement('strong');
    subject.textContent = sample.subject;
    const state = document.createElement('small');
    const sent = entry?.sent || sample.delivery_state === 'sent';
    state.textContent = sent ? 'Gönderildi'
      : sample.delivery_state === 'uncertain' ? 'Gönderim belirsiz'
      : entry?.busy ? 'İşleniyor' : entry?.error ? 'İşlem bekliyor'
      : entry?.hasDraft ? 'Taslak hazır' : entry?.questions ? 'Bilgi bekliyor'
      : entry?.readyForDraft || sample.followup ? 'Yanıt bekliyor' : 'Yeni';
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
  inbox('empty').hidden = Boolean(active);
  inbox('case').hidden = !active;
  inbox('draft-empty').hidden = Boolean(active?.hasDraft);
  inbox('draft-panel').hidden = !active?.hasDraft;
  updateResponseOptions(active);
  inbox('draft-empty').textContent = !active ? 'Yanıt hazırlamak için bir iade e-postası seç.'
    : active.sent ? 'Bu e-postaya yanıt gönderildi.'
    : mail.delivery_state === 'uncertain' ? 'Gönderim sonucu belirsiz. Posta kutusunu kontrol et.'
    : active.error ? 'Sohbet panelindeki işlem durumunu kontrol et.'
    : active.busy ? 'Netle çalışıyor…'
    : active.questions ? 'Netle önerisi için sohbet panelindeki soruları yanıtla veya bir yanıt türü seç.'
    : 'Yanıt oluştur’a basarak düzenlenebilir taslağı hazırla.';
  if (!active) {
    for (const field of ['recipient', 'reply-subject', 'draft', 'message']) inbox(field).value = '';
    for (const field of ['thread', 'questions', 'sources']) inbox(field).replaceChildren();
    return;
  }

  inbox('subject').textContent = mail.subject;
  inbox('sender').textContent = mail.sender;
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
    turn.textContent = (employee ? 'Sen' : 'Netle') + '\n' + text;
    return turn;
  }));
  thread.scrollTop = thread.scrollHeight;
  inbox('status').textContent = active.error || (active.busy ? 'Netle çalışıyor…' : '');
  inbox('status').className = active.error ? 'error' : '';
  inbox('retry').hidden = active.busy || !active.retry;
  inbox('chat').hidden = !active.sessionId;
  inbox('revise').hidden = !active.hasDraft || active.employeeDraft;
  inbox('answers').hidden = !active.questions;
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
  const missingThread = mail.id.startsWith('gmail-') && !mail.thread;
  inbox('send').disabled = active.busy || Boolean(active.sent) || missingThread || mail.delivery_state === 'uncertain';
  inbox('revise').disabled = active.busy || Boolean(active.sent);
  inbox('send-status').textContent = active.sendStatus || (missingThread
    ? 'Özgün e-postada zincir kimliği bulunamadığı için yanıt gönderilemiyor.'
    : mail.thread ? 'Yanıt, özgün e-posta zincirine bağlanacak.' : '');
}

function updateResponseOptions(entry) {
  inbox('draft-options').hidden = !entry?.sessionId || Boolean(entry.sent);
  const type = entry?.responseType || 'suggested';
  const response = employeeResponses[type];
  const select = inbox('response-type');
  const detail = inbox('response-detail');
  const create = inbox('create-draft');
  select.value = type;
  select.disabled = Boolean(entry?.busy);
  const detailLabel = response?.label || '';
  inbox('response-detail-field').hidden = !detailLabel;
  inbox('response-detail-label').textContent = detailLabel;
  detail.value = entry?.responseDetails[type] || '';
  detail.setCustomValidity('');
  detail.disabled = Boolean(entry?.busy);
  detail.required = Boolean(detailLabel);
  create.hidden = !response && Boolean(entry?.hasDraft) && !entry?.readyForDraft;
  create.disabled = !entry || entry.busy || (!response && !entry.readyForDraft);
  create.textContent = entry?.hasDraft ? 'Yeni taslak oluştur' : 'Yanıt oluştur';
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
  if (previous?.version === version) {
    renderInbox();
    return;
  }
  const entry = {
    version, busy: false, turns: previous?.turns.slice() || [],
    sources: [], answers: {}, message: '', draft: '', responseType: 'suggested', responseDetails: {},
    subject: /^re:/i.test(mail.subject) ? mail.subject : 'Re: ' + mail.subject,
    recipient: mail.recipient
  };
  refundCases.set(mail.id, entry);
  if (mail.delivery_state) {
    entry.sent = mail.delivery_state === 'sent';
    renderInbox();
    return;
  }
  const previousMessages = mail.messages?.slice(0, -1) || [];
  const previousText = previousMessages.map(item => item.body).join('\n\n');
  const request = mail.followup
    ? 'Konu: ' + mail.subject.slice(0, 300) + '\n\nÖnceki müşteri mesajları:\n' + previousText.slice(-4000)
      + '\n\nSon müşteri mesajı:\n' + mail.body.slice(0, 7500)
    : mail.id.startsWith('gmail-') ? (mail.subject + '\n\n' + mail.body).slice(0, 12000) : mail.body;
  if (mail.followup) entry.turns.push({text: 'Müşterinin yeni mesajı bu vakaya eklendi.'});
  runInboxRequest(mail, {
    action: 'start', request, defer_draft: true, followup: Boolean(mail.followup),
    previous_message_ids: previousMessages.map(item => item.thread?.message_id).filter(Boolean)
  });
}

async function runInboxRequest(mail, payload, employeeTurn) {
  const entry = refundCases.get(mail.id);
  if (!entry || entry.busy) return;
  entry.busy = true;
  entry.error = '';
  entry.retry = null;
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
      entry.questions = data.questions;
      entry.readyForDraft = Boolean(data.ready_for_draft);
      entry.answers = {};
      entry.turns.push({text: data.questions
        ? Object.values(data.questions).join('\n')
        : data.ready_for_draft ? 'Sağ panelden yanıt taslağını oluşturabilirsin.'
        : 'Yanıt taslağı sağ panelde hazır.'});
    }
    if (typeof data.draft === 'string' && !(entry.employeeDraft && payload.action === 'resume')) {
      entry.hasDraft = true;
      entry.employeeDraft = false;
      entry.draft = data.draft;
      entry.sources = data.sources;
    } else if (data.message && data.sources.length) {
      entry.turns[entry.turns.length - 1].text += '\n\n' + data.sources.join('\n');
    }
  } catch (error) {
    entry.error = error.message;
    if (payload.action === 'start' || payload.action === 'resume') {
      entry.retry = {payload, employeeTurn};
    }
  } finally {
    entry.busy = false;
    if (refundCases.get(mail.id) === entry) renderInbox();
  }
}

function resetInbox() {
  refundCases.clear();
  selectedRefundMail = null;
  inboxMails = sampleRefundMails;
  inboxRefresh = null;
  inbox('source').textContent = 'Örnek posta kutusu';
  inbox('load-status').textContent = '';
  inbox('refresh').disabled = false;
  renderInbox();
}

async function refreshInbox() {
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
    inboxMails = data.mails;
    for (const mail of inboxMails) {
      const entry = refundCases.get(mail.id);
      if (entry?.version === mailVersion(mail) && mail.delivery_state === 'sent') {
        entry.sent = true;
        entry.sendStatus = 'Bu e-postaya yanıt gönderildi.';
      }
    }
    selectedRefundMail = inboxMails.find(mail => mail.id === selectedRefundMail?.id) ?? null;
    inbox('source').textContent = 'Gmail · Netle İade';
    inbox('load-status').textContent = inboxMails.length ? '' : 'Bu etikette henüz mail yok.';
    if (selectedRefundMail) openRefundMail(selectedRefundMail);
    else renderInbox();
  } catch (error) {
    if (inboxRefresh === refresh) inbox('load-status').textContent = error.message;
  } finally {
    if (inboxRefresh === refresh) {
      inboxRefresh = null;
      inbox('refresh').disabled = false;
    }
  }
}

inbox('refresh').addEventListener('click', refreshInbox);

inbox('response-type').addEventListener('change', event => {
  const entry = refundCases.get(selectedRefundMail.id);
  entry.responseType = event.target.value;
  updateResponseOptions(entry);
});

inbox('response-detail').addEventListener('input', event => {
  const entry = refundCases.get(selectedRefundMail.id);
  event.target.setCustomValidity('');
  entry.responseDetails[entry.responseType] = event.target.value;
});

inbox('create-draft').addEventListener('click', () => {
  const mail = selectedRefundMail;
  const entry = mail && refundCases.get(mail.id);
  if (!entry?.sessionId || entry.busy || entry.sent) return;
  const response = employeeResponses[entry.responseType];
  if (!response) {
    if (entry.readyForDraft) runInboxRequest(mail, {action: 'generate', session_id: entry.sessionId});
    return;
  }
  const input = inbox('response-detail');
  const detail = (entry.responseDetails[entry.responseType] || '').trim();
  if (response.label && !detail) {
    input.setCustomValidity(response.label + ' alanını doldur.');
    input.reportValidity();
    return;
  }
  entry.draft = 'Merhaba,\n\n' + response.message(detail) + '\n\nİyi çalışmalar dileriz.';
  entry.hasDraft = true;
  entry.employeeDraft = true;
  entry.sources = [];
  entry.sendStatus = '';
  renderInbox();
  inbox('draft').focus();
});

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
  if (!entry?.hasDraft || entry.busy || entry.sent || mail.delivery_state === 'uncertain'
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
    if (data.sent && !data.already_sent) entry.sentReply = entry.draft;
    entry.sendStatus = 'E-posta gönderildi.';
  } catch (error) {
    entry.sendStatus = error.message;
  } finally {
    entry.busy = false;
    if (refundCases.get(mail.id) === entry) renderInbox();
  }
});
