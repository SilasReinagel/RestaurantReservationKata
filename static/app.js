// Chat client. The session id lives only in memory: reloading the page starts a new
// conversation, so the visible transcript and the server's context never disagree.
(() => {
  const transcript = document.getElementById("transcript");
  const form = document.getElementById("composer");
  const input = document.getElementById("input");
  const sendButton = document.getElementById("send");
  const typing = document.getElementById("typing");
  const newChat = document.getElementById("new-chat");

  const CODE_PATTERN = /\bBV-[A-Z0-9]{4}\b/g;
  const GREETING = "Hi! I can book, change, or cancel a reservation at Bella Vista. How can I help?";
  let sessionId = null;
  let busy = false;

  function isNearBottom() {
    return transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
  }

  // Build message content with textContent only (never innerHTML), highlighting codes.
  function renderText(container, text) {
    let last = 0;
    for (const match of text.matchAll(CODE_PATTERN)) {
      container.append(text.slice(last, match.index));
      const badge = document.createElement("span");
      badge.className = "code";
      badge.textContent = match[0];
      container.append(badge);
      last = match.index + match[0].length;
    }
    container.append(text.slice(last));
  }

  function addMessage(role, text, { retry } = {}) {
    const stick = isNearBottom();
    const bubble = document.createElement("div");
    bubble.className = `message ${role}`;
    renderText(bubble, text);
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "retry";
      button.textContent = "Retry";
      button.addEventListener("click", () => { bubble.remove(); send(retry, { echo: false }); });
      bubble.append(button);
    }
    transcript.append(bubble);
    if (stick || role === "guest") transcript.scrollTop = transcript.scrollHeight;
  }

  function setBusy(value) {
    busy = value;
    typing.hidden = !value;
    sendButton.disabled = value;
    input.disabled = value;
    if (!value) input.focus();
  }

  async function send(text, { echo = true } = {}) {
    if (busy || !text.trim()) return;
    if (echo) addMessage("guest", text);
    setBusy(true);
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = data.error?.message || "Something went wrong. Please try again.";
        addMessage("error", message, { retry: response.status >= 500 ? text : null });
        return;
      }
      if (data.session_reset) {
        addMessage("notice", "The server restarted, so this conversation started fresh. Your saved reservations are safe.");
      }
      sessionId = data.session_id;
      addMessage("agent", data.reply);
    } catch {
      addMessage("error", "Couldn't reach the server. Check that it's running and try again.", { retry: text });
    } finally {
      setBusy(false);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value;
    input.value = "";
    autosize();
    send(text);
  });

  // Enter sends; Shift+Enter adds a new line.
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  }
  input.addEventListener("input", autosize);

  newChat.addEventListener("click", () => {
    if (busy) return;
    sessionId = null;
    transcript.replaceChildren();
    addMessage("agent", GREETING);
    input.focus();
  });

  addMessage("agent", GREETING);
})();
