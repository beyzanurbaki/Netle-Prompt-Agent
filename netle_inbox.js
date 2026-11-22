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
let selectedRefundMail = null;

function renderInbox() {
  const mail = selectedRefundMail;
  const active = mail && refundCases.get(mail.id);
  inbox('list').replaceChildren(...sampleRefundMails.map(sample => {
    const entry = refundCases.get(sample.id);
    const button = document.createElement('button');
    button.className = 'inbox-mail';
    button.type = 'button';
    button.setAttribute('aria-pressed', String(sample === mail));
    const subject = document.createElement('strong');
    subject.textContent = sample.subject;
    const state = document.createElement('small');
    state.textContent = entry?.sent ? 'Gönderildi' : entry?.busy ? 'İşleniyor' : entry?.error ? 'İşlem bekliyor'
      : entry?.questions ? 'Bilgi bekliyor' : entry?.hasDraft ? 'Taslak hazır' : 'Yeni';
    button.append(subject, state);
    button.addEventListener('click', () => openRefundMail(sample));
    return button;
  }));
  inbox('empty').hidden = Boolean(active);
  inbox('case').hidden = !active;
  inbox('draft-empty').hidden = Boolean(active?.hasDraft);
  inbox('draft-panel').hidden = !active?.hasDraft;
  if (!active) {
    for (const field of ['recipient', 'reply-subject', 'draft', 'message']) inbox(field).value = '';
    for (const field of ['thread', 'questions', 'sources']) inbox(field).replaceChildren();
    return;
  }

  inbox('subject').textContent = mail.subject;
  inbox('sender').textContent = mail.sender;
  inbox('original').textContent = mail.body;
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
  inbox('revise').hidden = !active.hasDraft;
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
  for (const field of inbox('case').querySelectorAll('form button, form select, form input, form textarea')) {
    field.disabled = active.busy;
  }
  updateDeliveryFields(inbox('answers'));
  for (const field of ['recipient', 'reply-subject', 'draft']) {
    inbox(field).readOnly = active.busy || Boolean(active.sent);
  }
  inbox('send').disabled = active.busy || Boolean(active.sent);
  inbox('revise').disabled = active.busy || Boolean(active.sent);
  inbox('send-status').textContent = active.sendStatus || '';
}

function sizeInboxDraft() {
  const draft = inbox('draft');
  draft.style.height = 'auto';
  if (draft.scrollHeight) draft.style.height = (draft.scrollHeight + 2) + 'px';
}

function openRefundMail(mail) {
  selectedRefundMail = mail;
  if (refundCases.has(mail.id)) {
    renderInbox();
    return;
  }
  const entry = {
    busy: false, turns: [], sources: [], answers: {}, message: '', draft: '',
    subject: 'Re: ' + mail.subject, recipient: mail.recipient
  };
  refundCases.set(mail.id, entry);
  runInboxRequest(mail, {action: 'start', request: mail.body});
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
      entry.answers = {};
      entry.turns.push({text: data.questions
        ? Object.values(data.questions).join('\n')
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
  renderInbox();
}

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
  if (!entry?.hasDraft || entry.busy || entry.sent) return;
  entry.busy = true;
  entry.sendStatus = 'E-posta gönderiliyor…';
  renderInbox();
  try {
    const data = await post('/api/mail', {
      session_id: entry.sessionId, recipient: entry.recipient, subject: entry.subject, body: entry.draft
    });
    entry.sent = data.sent;
    entry.sendStatus = 'E-posta gönderildi.';
  } catch (error) {
    entry.sendStatus = error.message;
  } finally {
    entry.busy = false;
    if (refundCases.get(mail.id) === entry) renderInbox();
  }
});
