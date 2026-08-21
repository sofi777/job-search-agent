// Floating, cross-page assistant (base.html includes chat_widget.html on every logged-in
// page). No page reload for a turn - fetch()+JSON against /assistant/* (see app.py), same
// AJAX precedent as the rating endpoint tailor.html already uses. History/current model are
// lazy-loaded on first open, not injected into every page render (see app.py's inject_user).
(function () {
  const panel = document.querySelector('[data-assistant-panel]');
  const toggle = document.querySelector('[data-assistant-toggle]');
  if (!panel || !toggle) return;

  const closeBtn = panel.querySelector('[data-assistant-close]');
  const messagesEl = panel.querySelector('[data-assistant-messages]');
  const input = panel.querySelector('[data-assistant-input]');
  const sendBtn = panel.querySelector('[data-assistant-send]');
  const modelSelect = panel.querySelector('[data-assistant-model]');

  let historyLoaded = false;
  let lastRenderedId = 0;
  let thinkingRow = null;
  let pollTimer = null;
  const POLL_INTERVAL_MS = 2000;
  const POLL_TIMEOUT_MS = 3 * 60 * 1000;

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function clearEmptyState() {
    const empty = messagesEl.querySelector('[data-assistant-empty]');
    if (empty) empty.remove();
  }

  function renderMessage(m) {
    clearEmptyState();
    const row = document.createElement('div');
    row.className = 'msg-row ' + m.role;
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.textContent = m.content;
    row.appendChild(bubble);

    if (m.artifact_text) {
      const header = document.createElement('div');
      header.className = 'assistant-artifact-header';
      const label = document.createElement('span');
      label.className = 'text-muted';
      label.style.fontSize = '12px';
      label.textContent = 'Draft';
      const copyBtn = document.createElement('button');
      copyBtn.type = 'button';
      copyBtn.className = 'btn btn-ghost btn-small';
      copyBtn.textContent = 'Copy';
      header.appendChild(label);
      header.appendChild(copyBtn);
      bubble.appendChild(header);

      const artifact = document.createElement('div');
      artifact.className = 'assistant-artifact';
      artifact.textContent = m.artifact_text;
      bubble.appendChild(artifact);
      copyBtn.addEventListener('click', () => navigator.clipboard.writeText(artifact.innerText));
    }

    if (m.linked_chat_message_id) {
      const rating = document.createElement('div');
      rating.className = 'msg-rating';
      rating.dataset.messageId = m.linked_chat_message_id;
      rating.innerHTML =
        '<button type="button" class="rate-btn" data-rating="up">\u{1F44D}</button>' +
        '<button type="button" class="rate-btn" data-rating="down">\u{1F44E}</button>';
      bubble.appendChild(rating);
    }

    messagesEl.appendChild(row);
    scrollToBottom();
    return row;
  }

  function renderThinking() {
    const row = document.createElement('div');
    row.className = 'msg-row assistant';
    row.innerHTML = '<div class="msg-bubble"><span class="typing-dot"></span>' +
      '<span class="typing-dot"></span><span class="typing-dot"></span> Working on it…</div>';
    messagesEl.appendChild(row);
    scrollToBottom();
    return row;
  }

  function renderError(text) {
    clearEmptyState();
    const row = document.createElement('div');
    row.className = 'msg-row assistant';
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble tag tag-outline';
    bubble.textContent = text;
    row.appendChild(bubble);
    messagesEl.appendChild(row);
    scrollToBottom();
  }

  // Renders only messages newer than what's already on screen, keyed by id - shared by
  // loadHistory() and the poll fallback below so a message never gets rendered twice.
  function appendMessages(messages) {
    messages.forEach((m) => {
      if (m.id <= lastRenderedId) return;
      renderMessage(m);
      lastRenderedId = Math.max(lastRenderedId, m.id);
    });
  }

  function showThinking() {
    sendBtn.disabled = true;
    if (!thinkingRow) thinkingRow = renderThinking();
  }

  function hideThinking() {
    sendBtn.disabled = false;
    if (thinkingRow) {
      thinkingRow.remove();
      thinkingRow = null;
    }
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // A turn (see assistant.handle_turn) keeps running and saving to the DB server-side even
  // if this tab navigates away, gets backgrounded/frozen, or is closed mid-request - so
  // instead of trusting the in-flight fetch's own callback, poll history until the reply
  // shows up. Covers: switching windows, closing/reopening the widget, and reloading the
  // page while a turn is still in flight.
  function startPolling() {
    if (pollTimer) return;
    showThinking();
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    pollTimer = setInterval(() => {
      if (Date.now() > deadline) {
        stopPolling();
        hideThinking();
        renderError('Still no response - try again.');
        return;
      }
      fetch('/assistant/history')
        .then((r) => r.json())
        .then((data) => {
          const messages = data.messages || [];
          const last = messages[messages.length - 1];
          if (last && last.role === 'assistant' && last.id > lastRenderedId) {
            stopPolling();
            hideThinking();
            appendMessages(messages);
          }
        })
        .catch(() => {}); // transient - next tick retries
    }, POLL_INTERVAL_MS);
  }

  function loadHistory() {
    historyLoaded = true;
    fetch('/assistant/history')
      .then((r) => r.json())
      .then((data) => {
        const messages = data.messages || [];
        appendMessages(messages);
        if (data.model) modelSelect.value = data.model;
        const last = messages[messages.length - 1];
        if (last && last.role === 'user') startPolling();
      })
      .catch(() => renderError('Could not load chat history.'));
  }

  function open() {
    panel.hidden = false;
    if (!historyLoaded) loadHistory();
    input.focus();
  }

  function close() {
    panel.hidden = true;
  }

  toggle.addEventListener('click', () => (panel.hidden ? open() : close()));
  closeBtn.addEventListener('click', close);

  function send() {
    const text = input.value.trim();
    if (!text) return;
    renderMessage({ role: 'user', content: text });
    showThinking();
    input.value = '';
    input.style.height = 'auto';

    fetch('/assistant/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    })
      .then((r) => r.json().then((data) => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        stopPolling(); // a leftover poll from an earlier pending turn shouldn't outlive this answer
        if (!ok) {
          hideThinking();
          renderError(data.error || 'Something went wrong.');
          return;
        }
        if (data.user_message) lastRenderedId = Math.max(lastRenderedId, data.user_message.id);
        hideThinking();
        appendMessages(data.assistant_messages || []);
      })
      .catch(() => {
        // The request may have been interrupted (navigation, backgrounded tab) rather than
        // actually failed - the turn keeps running server-side, so fall back to polling for
        // it instead of declaring failure.
        startPolling();
      });
  }

  sendBtn.addEventListener('click', send);
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = input.scrollHeight + 'px';
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  modelSelect.addEventListener('change', () => {
    fetch('/assistant/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelSelect.value }),
    });
  });

  // Thumbs up/down on a mirrored cover-letter turn rates the real chat_messages row (see
  // linked_chat_message_id) - same fetch pattern as tailor.html's own rating buttons. Scoped
  // to this panel (not document-wide) so it never double-fires alongside tailor.html's
  // identical listener when both are present on the same page.
  panel.addEventListener('click', (e) => {
    const btn = e.target.closest('.rate-btn');
    if (!btn || btn.disabled) return;
    const container = btn.closest('.msg-rating');
    const messageId = container.dataset.messageId;
    const rating = btn.dataset.rating;
    fetch(`/messages/${messageId}/rate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating }),
    }).then((r) => {
      if (!r.ok) return;
      container.querySelectorAll('.rate-btn').forEach((b) => {
        b.disabled = true;
      });
      btn.classList.add('selected');
    });
  });
})();
