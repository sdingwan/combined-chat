const form = document.getElementById("channelForm");
const twitchInput = document.getElementById("twitchInput");
const kickInput = document.getElementById("kickInput");
const statusEl = document.getElementById("status");
const chatEl = document.getElementById("chat");
const connectBtn = document.getElementById("connectBtn");
const disconnectBtn = document.getElementById("disconnectBtn");
const messageInput = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const platformSelect = document.getElementById("platformSelect");
const authStatusEl = document.getElementById("authStatus");
const twitchLoginBtn = document.getElementById("twitchLoginBtn");
const kickLoginBtn = document.getElementById("kickLoginBtn");
const logoutBtn = document.getElementById("logoutBtn");
const chatPauseBanner = document.getElementById("chatPauseBanner");
const chatPauseLabel = document.getElementById("chatPauseLabel");
const chatResumeButton = document.getElementById("chatResumeButton");

let socket = null;
let sendingMessage = false;
let connectionReady = false;
let authState = { authenticated: false, accounts: [], user: null };
const maxMessages = 50;
const scrollLockEpsilon = 4;
const bufferedMessageLimit = 200;
const bufferedMessages = [];
let unreadBufferedCount = 0;
let pausedForScroll = false;
let suppressScrollHandler = false;
const currentChannels = { twitch: "", kick: "" };

const storageKey = "combinedChatState";

function createDefaultPersistedState() {
  return {
    connected: false,
    channels: { twitch: "", kick: "" },
    messages: [],
  };
}

let persistedState = createDefaultPersistedState();
let hydratingMessages = false;
let persistenceAvailable = false;

try {
  persistenceAvailable =
    typeof window !== "undefined" && typeof window.localStorage !== "undefined";
} catch (err) {
  persistenceAvailable = false;
}

function sanitizePersistedMessages(messages) {
  if (!Array.isArray(messages)) {
    return [];
  }
  const sanitized = [];
  messages.forEach((entry) => {
    if (!entry || typeof entry !== "object") {
      return;
    }
    try {
      const clone = JSON.parse(JSON.stringify(entry));
      sanitized.push(clone);
    } catch (err) {
      // Ignore entries that cannot be serialized
    }
  });
  const startIndex = Math.max(0, sanitized.length - maxMessages);
  return sanitized.slice(startIndex);
}

function loadPersistedState() {
  if (!persistenceAvailable) {
    return;
  }
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) {
      return;
    }
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object") {
      return;
    }
    const nextState = createDefaultPersistedState();
    nextState.connected = Boolean(data.connected);
    if (data.channels && typeof data.channels === "object") {
      nextState.channels.twitch =
        typeof data.channels.twitch === "string" ? data.channels.twitch : "";
      nextState.channels.kick =
        typeof data.channels.kick === "string" ? data.channels.kick : "";
    }
    nextState.messages = sanitizePersistedMessages(data.messages);
    persistedState = nextState;
  } catch (err) {
    persistenceAvailable = false;
    console.warn("Failed to load persisted chat state", err);
  }
}

function savePersistedState() {
  if (!persistenceAvailable) {
    return;
  }
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(persistedState));
  } catch (err) {
    persistenceAvailable = false;
    console.warn("Failed to persist chat state", err);
  }
}

function clearPersistedMessages() {
  persistedState.messages = [];
}

function recordMessageForPersistence(payload) {
  if (!persistenceAvailable) {
    return;
  }
  let clone;
  try {
    clone = JSON.parse(JSON.stringify(payload));
  } catch (err) {
    console.warn("Failed to store chat message", err);
    return;
  }
  persistedState.messages.push(clone);
  if (persistedState.messages.length > maxMessages) {
    const excess = persistedState.messages.length - maxMessages;
    persistedState.messages.splice(0, excess);
  }
  savePersistedState();
}

if (persistenceAvailable) {
  loadPersistedState();
}
const moderationOptions = [
  { action: "ban", label: "🚫", ariaLabel: "Ban user", variant: "danger", isIcon: true },
  { action: "timeout",
    label: "🕓",
    ariaLabel: "Timeout 10 minutes (600 seconds)",
    duration: 10 * 60,
    isIcon: true,
  },
  { action: "timeout", label: "1s", ariaLabel: "Timeout 1 second", duration: 1 },
  { action: "timeout", label: "1hr", ariaLabel: "Timeout 1 hour", duration: 60 * 60 },
  { action: "timeout", label: "24hr", ariaLabel: "Timeout 24 hours", duration: 24 * 60 * 60 },
  { action: "unban", label: "Unban", ariaLabel: "Unban user", variant: "muted" },
  {
    action: "untimeout",
    label: "Untimeout",
    ariaLabel: "Remove current timeout",
    variant: "muted",
  },
];
const moderationActionLabels = {
  ban: "Ban",
  timeout: "Timeout",
  unban: "Unban",
  untimeout: "Timeout removal",
};
const moderationSuccessSuffix = {
  ban: "applied to",
  timeout: "applied to",
  unban: "completed for",
  untimeout: "completed for",
};
let moderationMenuTarget = null;
let moderationMenuAnchor = null;
const sendButtonPlatformClasses = ["button--twitch", "button--kick", "button--dual", "button--neutral"];
const messageInputPlatformClasses = [
  "message-input--twitch",
  "message-input--kick",
  "message-input--neutral",
];

const moderationMenu = document.createElement("div");
moderationMenu.id = "moderationMenu";
moderationMenu.classList.add("moderation-menu", "hidden");
document.body.appendChild(moderationMenu);

function setButtonBusy(button, busy, busyLabel) {
  if (!button) {
    return;
  }
  if (busy) {
    button.dataset.wasDisabled = button.disabled ? "1" : "0";
    if (!button.dataset.originalLabel) {
      button.dataset.originalLabel = button.textContent.trim();
    }
    if (busyLabel) {
      button.textContent = busyLabel;
    }
    button.classList.add("is-busy");
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  } else {
    const original = button.dataset.originalLabel;
    if (original) {
      button.textContent = original;
    }
    button.classList.remove("is-busy");
    button.removeAttribute("aria-busy");
    const wasDisabled = button.dataset.wasDisabled === "1";
    if (wasDisabled) {
      button.disabled = true;
    } else {
      button.disabled = false;
    }
    delete button.dataset.wasDisabled;
  }
}

function markConnected() {
  if (!connectBtn) {
    return;
  }
  connectBtn.textContent = "Connected";
  connectBtn.classList.add("is-active");
  connectBtn.disabled = true;
}

function resetConnectState() {
  if (!connectBtn) {
    return;
  }
  connectBtn.classList.remove("is-active");
  connectBtn.textContent = "Connect";
  connectBtn.disabled = false;
}

function setStatus(message) {
  statusEl.textContent = message;
}

function hasAccount(platform) {
  return (
    Array.isArray(authState.accounts) &&
    authState.accounts.some((account) => account.platform === platform)
  );
}

function buildPlatformOptions() {
  const options = [];
  const twitchChannel = twitchInput.value.trim();
  const twitchReady = twitchChannel && hasAccount("twitch");
  if (twitchReady) {
    options.push({ value: "twitch", label: `Twitch (${twitchChannel})` });
  }
  const kickChannel = kickInput.value.trim();
  const kickReady = kickChannel && hasAccount("kick");
  if (kickReady) {
    options.push({ value: "kick", label: `Kick (${kickChannel})` });
  }
  if (twitchReady && kickReady) {
    options.push({ value: "both", label: "Twitch + Kick (send to both)" });
  }
  return options;
}

function applySendButtonStyle(platform) {
  const buttonClass =
    platform === "twitch"
      ? "button--twitch"
      : platform === "kick"
        ? "button--kick"
        : platform === "both"
          ? "button--dual"
          : "button--neutral";
  sendButton.classList.remove(...sendButtonPlatformClasses);
  sendButton.classList.add(buttonClass);
  sendButton.dataset.platform = platform || "";

  if (messageInput) {
    const inputClass =
      platform === "twitch"
        ? "message-input--twitch"
        : platform === "kick"
          ? "message-input--kick"
          : "message-input--neutral";
    messageInput.classList.remove(...messageInputPlatformClasses);
    messageInput.classList.add(inputClass);
  }
}

function updateMessageControls() {
  const options = buildPlatformOptions();
  const isLocked = connectionReady;
  const channelInputs = [
    { el: twitchInput, lockLabel: "Disconnect to change Twitch channel" },
    { el: kickInput, lockLabel: "Disconnect to change Kick channel" },
  ];

  channelInputs.forEach(({ el, lockLabel }) => {
    if (!el) {
      return;
    }
    if (!Object.prototype.hasOwnProperty.call(el.dataset, "originalPlaceholder")) {
      el.dataset.originalPlaceholder = el.placeholder || "";
    }
    if (!Object.prototype.hasOwnProperty.call(el.dataset, "originalTitle")) {
      el.dataset.originalTitle = el.title || "";
    }

    el.readOnly = isLocked;
    el.classList.toggle("input--locked", isLocked);
    if (isLocked) {
      el.setAttribute("aria-disabled", "true");
      el.title = lockLabel;
      if (!el.value) {
        el.placeholder = lockLabel;
      }
    } else {
      el.removeAttribute("aria-disabled");
      const originalPlaceholder = el.dataset.originalPlaceholder || "";
      el.placeholder = originalPlaceholder;
      const originalTitle = el.dataset.originalTitle || "";
      if (originalTitle) {
        el.title = originalTitle;
      } else {
        el.removeAttribute("title");
      }
    }
  });

  platformSelect.innerHTML = "";
  if (!options.length) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = connectionReady
      ? "Link accounts to send"
      : "Connect to enable messaging";
    platformSelect.appendChild(placeholder);
  } else {
    options.forEach((option, index) => {
      const optionEl = document.createElement("option");
      optionEl.value = option.value;
      optionEl.textContent = option.label;
      if (index === 0) {
        optionEl.selected = true;
      }
      platformSelect.appendChild(optionEl);
    });
  }

  platformSelect.disabled = !connectionReady || !options.length;
  const canSend = connectionReady && options.length > 0;
  messageInput.disabled = !canSend;
  sendButton.disabled = !canSend;
  const selectedPlatform = !platformSelect.disabled ? platformSelect.value : "";
  applySendButtonStyle(selectedPlatform);

  if (!canSend) {
    messageInput.value = "";
  }

  if (!connectionReady) {
    messageInput.placeholder = "Connect to a chat to send messages";
  } else if (!options.length) {
    messageInput.placeholder = "Link your account to send messages";
  } else {
    messageInput.placeholder = "Type your message here...";
  }
}

function isChatPaused() {
  return pausedForScroll;
}

function isNearBottom() {
  if (!chatEl) {
    return true;
  }
  const distanceFromBottom = chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight;
  return distanceFromBottom <= scrollLockEpsilon;
}

function scrollToBottom() {
  if (!chatEl) {
    return;
  }
  suppressScrollHandler = true;
  chatEl.scrollTop = chatEl.scrollHeight;
  const reset = () => {
    suppressScrollHandler = false;
  };
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(reset);
  } else {
    setTimeout(reset, 0);
  }
}

function updatePauseBanner() {
  if (!chatPauseBanner || !chatResumeButton) {
    return;
  }
  const paused = isChatPaused();
  chatPauseBanner.classList.toggle("hidden", !paused);
  if (!paused) {
    return;
  }
  const unreadCount = unreadBufferedCount;
  if (chatPauseLabel) {
    chatPauseLabel.textContent = "Chat Paused Due to Scroll";
  }
  const hasUnread = unreadCount > 0;
  chatResumeButton.disabled = false;
  chatResumeButton.textContent = hasUnread
    ? unreadCount === 1
      ? "Show 1 new message"
      : `Show ${unreadCount} new messages`
    : "\u2193 Back to bottom";
}

function bufferIncomingMessage(payload) {
  if (!payload) {
    return;
  }
  if (bufferedMessages.length >= bufferedMessageLimit) {
    bufferedMessages.shift();
  }
  bufferedMessages.push(payload);
  unreadBufferedCount = bufferedMessages.length;
  updatePauseBanner();
}

function flushBufferedMessages({ snapToBottom = true } = {}) {
  if (!bufferedMessages.length) {
    unreadBufferedCount = 0;
    updatePauseBanner();
    if (snapToBottom) {
      scrollToBottom();
    }
    return;
  }
  const pending = bufferedMessages.splice(0, bufferedMessages.length);
  pending.forEach((message) => {
    addMessageToDom(message);
  });
  unreadBufferedCount = 0;
  if (snapToBottom) {
    scrollToBottom();
  }
  updatePauseBanner();
}

function onChatScroll() {
  if (!chatEl || suppressScrollHandler) {
    return;
  }
  if (isNearBottom()) {
    if (pausedForScroll) {
      pausedForScroll = false;
      flushBufferedMessages({ snapToBottom: true });
    } else {
      updatePauseBanner();
    }
  } else if (!pausedForScroll) {
    pausedForScroll = true;
    updatePauseBanner();
  }
}

function renderAuthState() {
  if (authState && authState.authenticated) {
    const displayName =
      (authState.user && (authState.user.display_name || `User #${authState.user.id}`)) ||
      "Authenticated";
    const linked = [
      hasAccount("twitch") ? "Twitch ✓" : "Twitch ×",
      hasAccount("kick") ? "Kick ✓" : "Kick ×",
    ];
    authStatusEl.textContent = `${displayName} — Linked: ${linked.join(" · ")}`;
    logoutBtn.disabled = false;
  } else {
    authStatusEl.textContent = "Not logged in. Sign in to send messages.";
    logoutBtn.disabled = true;
  }

  const twitchLinked = hasAccount("twitch");
  twitchLoginBtn.textContent = twitchLinked ? "Twitch Linked" : "Login with Twitch";
  twitchLoginBtn.disabled = twitchLinked;

  const kickLinked = hasAccount("kick");
  kickLoginBtn.textContent = kickLinked ? "Kick Linked" : "Login with Kick";
  kickLoginBtn.disabled = kickLinked;

  updateMessageControls();
}

async function refreshAuthStatus() {
  try {
    const response = await fetch("/auth/status", { credentials: "same-origin" });
    if (!response.ok) {
      throw new Error(`Status ${response.status}`);
    }
    const data = await response.json();
    if (data && typeof data === "object") {
      authState = {
        authenticated: Boolean(data.authenticated),
        accounts: Array.isArray(data.accounts) ? data.accounts : [],
        user: data.user || null,
      };
    } else {
      authState = { authenticated: false, accounts: [], user: null };
    }
  } catch (err) {
    console.warn("Failed to load auth status", err);
    authState = { authenticated: false, accounts: [], user: null };
  }
  renderAuthState();
}

function enableMessageInput() {
  connectionReady = true;
  updateMessageControls();
}

function disableMessageInput() {
  connectionReady = false;
  updateMessageControls();
}

function clearChat({ resetPersisted = false } = {}) {
  chatEl.innerHTML = "";
  bufferedMessages.length = 0;
  unreadBufferedCount = 0;
  pausedForScroll = false;
  updatePauseBanner();
  scrollToBottom();
  if (resetPersisted) {
    clearPersistedMessages();
    savePersistedState();
  }
}

function hideModerationMenu() {
  moderationMenuTarget = null;
  moderationMenuAnchor = null;
  moderationMenu.style.visibility = "";
  moderationMenu.classList.add("hidden");
  moderationMenu.innerHTML = "";
}

function positionModerationMenu() {
  if (!moderationMenuAnchor || moderationMenu.classList.contains("hidden")) {
    return;
  }
  if (!document.body.contains(moderationMenuAnchor)) {
    hideModerationMenu();
    return;
  }
  const rect = moderationMenuAnchor.getBoundingClientRect();
  const bounds = moderationMenu.getBoundingClientRect();
  const menuWidth = bounds.width || moderationMenu.offsetWidth || 0;
  const menuHeight = bounds.height || moderationMenu.offsetHeight || 0;
  let left = rect.left;
  let top = rect.bottom + 6;
  const maxLeft = window.innerWidth - menuWidth - 8;
  if (Number.isFinite(maxLeft) && left > maxLeft) {
    left = Math.max(8, maxLeft);
  }
  if (left < 8) {
    left = 8;
  }
  const maxTop = window.innerHeight - menuHeight - 8;
  if (Number.isFinite(maxTop) && top > maxTop) {
    top = rect.top - menuHeight - 6;
  }
  if (top < 8) {
    top = 8;
  }
  moderationMenu.style.left = `${Math.round(left)}px`;
  moderationMenu.style.top = `${Math.round(top)}px`;
}

function showModerationMenu(anchor, metadata) {
  if (!metadata || typeof metadata !== "object") {
    hideModerationMenu();
    return;
  }
  moderationMenuTarget = metadata;
  moderationMenuAnchor = anchor instanceof HTMLElement ? anchor : null;
  if (!moderationMenuAnchor) {
    hideModerationMenu();
    return;
  }
  moderationMenu.innerHTML = "";

  const header = document.createElement("div");
  header.classList.add("moderation-menu__header");

  const title = document.createElement("div");
  title.classList.add("moderation-menu__title");
  const platformLabel =
    metadata.platform === "twitch"
      ? "Twitch"
      : metadata.platform === "kick"
        ? "Kick"
        : metadata.platform || "Unknown";
  title.textContent = `${platformLabel} • ${metadata.username}`;
  header.appendChild(title);

  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.classList.add("moderation-menu__close");
  closeButton.setAttribute("aria-label", "Close moderation menu");
  closeButton.title = "Close moderation menu";
  closeButton.textContent = "×";
  closeButton.addEventListener("click", (event) => {
    event.stopPropagation();
    hideModerationMenu();
  });
  header.appendChild(closeButton);

  moderationMenu.appendChild(header);

  const actions = document.createElement("div");
  actions.classList.add("moderation-menu__actions");
  moderationMenu.appendChild(actions);

  moderationOptions.forEach((option) => {
    const optionButton = document.createElement("button");
    optionButton.type = "button";
    optionButton.textContent = option.label;
    optionButton.dataset.action = option.action;
    if (option.duration) {
      optionButton.dataset.duration = String(option.duration);
    }
    optionButton.classList.add("moderation-menu__button");
    if (option.isIcon) {
      optionButton.classList.add("moderation-menu__button--icon");
    }
    if (option.variant === "danger") {
      optionButton.classList.add("moderation-menu__button--danger");
    }
    if (option.variant === "muted") {
      optionButton.classList.add("moderation-menu__button--muted");
    }
    const accessibleLabel = option.ariaLabel || option.label;
    optionButton.setAttribute("aria-label", accessibleLabel);
    optionButton.title = accessibleLabel;
    optionButton.addEventListener("click", (event) => {
      event.stopPropagation();
      performModeration(option.action, option.duration ?? null);
    });
    actions.appendChild(optionButton);
  });

  moderationMenu.classList.remove("hidden");
  moderationMenu.style.visibility = "hidden";
  moderationMenu.style.left = "0px";
  moderationMenu.style.top = "0px";

  requestAnimationFrame(() => {
    positionModerationMenu();
    if (!moderationMenu.classList.contains("hidden")) {
      moderationMenu.style.visibility = "visible";
    }
  });
}

function formatDurationLabel(seconds) {
  if (!Number.isFinite(seconds)) {
    return "";
  }
  const totalSeconds = Math.max(0, Math.round(seconds));
  if (totalSeconds === 0) {
    return "0s";
  }
  if (totalSeconds % 3600 === 0) {
    const hours = totalSeconds / 3600;
    return `${hours}h`;
  }
  if (totalSeconds % 60 === 0) {
    const minutes = totalSeconds / 60;
    return `${minutes}m`;
  }
  return `${totalSeconds}s`;
}

function getModeratorDisplayName() {
  const user = authState && authState.user;
  if (!user || typeof user !== "object") {
    return "You";
  }
  const candidates = ["display_name", "displayName", "username", "login", "name"];
  for (const key of candidates) {
    const value = user[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  if (Object.prototype.hasOwnProperty.call(user, "id")) {
    return `User #${user.id}`;
  }
  return "You";
}

function formatPlatformName(value) {
  if (typeof value !== "string") {
    return "Platform";
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return "Platform";
  }
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

async function performModeration(action, duration) {
  const targetMeta = moderationMenuTarget;
  hideModerationMenu();
  if (!targetMeta) {
    return;
  }

  const { platform, username, userId } = targetMeta;
  const normalizedPlatform = platform === "twitch" ? "twitch" : "kick";
  const channelFallback = normalizedPlatform === "twitch" ? twitchInput.value.trim() : kickInput.value.trim();
  const channel = currentChannels[normalizedPlatform] || channelFallback;
  const normalizedPlatformLabel = formatPlatformName(normalizedPlatform);

  if (!channel) {
    setStatus(`No ${normalizedPlatformLabel} channel is active for moderation.`);
    return;
  }

  const payload = {
    platform: normalizedPlatform,
    channel,
    target: username,
    action,
  };

  if (typeof duration === "number" && Number.isFinite(duration)) {
    payload.duration = duration;
  }
  if (userId) {
    payload.target_id = userId;
  }

  const durationSeconds =
    action === "timeout" && typeof payload.duration === "number"
      ? payload.duration
      : null;
  const baseLabel = moderationActionLabels[action] || action;
  const decoratedAction =
    durationSeconds != null ? `${baseLabel} (${formatDurationLabel(durationSeconds)})` : baseLabel;
  const platformLabel = formatPlatformName(platform);

  try {
    setStatus(`Sending ${decoratedAction} for ${username} on ${platformLabel}…`);
    const response = await fetch("/chat/moderate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const errorBody = await response.json().catch(() => ({}));
      const detail =
        errorBody && errorBody.detail
          ? errorBody.detail
          : response.statusText || "Unknown error";
      const detailText =
        typeof detail === "string" ? detail : JSON.stringify(detail);
      setStatus(`Moderation failed for ${decoratedAction}: ${detailText}`);
      return;
    }

    const suffix = moderationSuccessSuffix[action] || "processed for";
    const moderator = getModeratorDisplayName();
    if (action === "timeout") {
      const durationLabel =
        durationSeconds != null ? formatDurationLabel(durationSeconds) : "";
      const durationText = durationLabel ? ` for ${durationLabel}` : "";
      setStatus(`${moderator} timed out ${username}${durationText} on ${platformLabel}.`);
    } else if (action === "ban") {
      setStatus(`${moderator} banned ${username} on ${platformLabel}.`);
    } else if (action === "unban") {
      setStatus(`${moderator} unbanned ${username} on ${platformLabel}.`);
    } else if (action === "untimeout") {
      setStatus(`${moderator} removed the timeout for ${username} on ${platformLabel}.`);
    } else {
      setStatus(`${decoratedAction} ${suffix} ${username} on ${platformLabel}.`);
    }
  } catch (err) {
    console.error("Failed to send moderation request", err);
    setStatus(`Network error while sending ${decoratedAction} request.`);
  }
}

function openModerationMenuForElement(usernameEl) {
  if (!(usernameEl instanceof HTMLElement)) {
    return;
  }
  const platform = usernameEl.dataset.platform;
  const username = usernameEl.dataset.username || usernameEl.textContent || "";
  const userId = usernameEl.dataset.userId || "";
  if (!platform || !username) {
    return;
  }
  showModerationMenu(usernameEl, { platform, username, userId });
}

chatEl.addEventListener("click", (event) => {
  const target = event.target instanceof HTMLElement ? event.target.closest(".username") : null;
  if (target) {
    event.preventDefault();
    event.stopPropagation();
    openModerationMenuForElement(target);
  } else {
    hideModerationMenu();
  }
});

chatEl.addEventListener("keydown", (event) => {
  if (!(event.target instanceof HTMLElement) || !event.target.classList.contains("username")) {
    return;
  }
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    openModerationMenuForElement(event.target);
  }
  if (event.key === "Escape") {
    hideModerationMenu();
  }
});

chatEl.addEventListener("scroll", () => {
  onChatScroll();
  positionModerationMenu();
});

if (chatResumeButton) {
  chatResumeButton.addEventListener("click", () => {
    pausedForScroll = false;
    flushBufferedMessages({ snapToBottom: true });
  });
}

updatePauseBanner();
window.addEventListener("resize", positionModerationMenu);

document.addEventListener("click", (event) => {
  if (moderationMenu.classList.contains("hidden")) {
    return;
  }
  if (event.target instanceof HTMLElement && moderationMenu.contains(event.target)) {
    return;
  }
  hideModerationMenu();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    hideModerationMenu();
  }
});

platformSelect.addEventListener("change", () => {
  if (!platformSelect.disabled) {
    applySendButtonStyle(platformSelect.value);
  }
});

function createMessageElement(payload) {
  const data = payload || {};
  const wrapper = document.createElement("div");
  wrapper.classList.add("message");
  if (data.platform) {
    wrapper.dataset.platform = data.platform;
  }

  if (data.type === "chat") {
    const icon = document.createElement("div");
    icon.classList.add("platform-icon", data.platform);
    wrapper.appendChild(icon);

    const meta = document.createElement("span");
    meta.classList.add("meta");

    if (Array.isArray(data.badges) && data.badges.length) {
      const badgeRow = document.createElement("span");
      badgeRow.classList.add("badges");
      data.badges.forEach((badge) => {
        if (!badge || !badge.image_url) {
          return;
        }
        const img = document.createElement("img");
        img.classList.add("badge");
        img.src = badge.image_url;
        if (badge.title) {
          img.alt = badge.title;
          img.title = badge.title;
        } else if (badge.set_id) {
          img.alt = badge.set_id;
          img.title = badge.set_id;
        } else {
          img.alt = "twitch badge";
          img.title = "twitch badge";
        }
        badgeRow.appendChild(img);
      });
      if (badgeRow.childElementCount) {
        meta.appendChild(badgeRow);
      }
    }

    const username = document.createElement("span");
    username.classList.add("username");
    username.textContent = `${data.user}`;
    username.style.color = resolveUsernameColor(data);
    username.dataset.platform = data.platform || "";
    username.dataset.username = data.user || "";
    if (data.user_id) {
      username.dataset.userId = data.user_id;
    }
    username.setAttribute("role", "button");
    username.setAttribute("tabindex", "0");
    username.setAttribute("aria-haspopup", "menu");

    const nameGroup = document.createElement("span");
    nameGroup.classList.add("name-group");
    nameGroup.appendChild(username);

    const separator = document.createElement("span");
    separator.classList.add("separator");
    separator.textContent = ":";
    separator.setAttribute("aria-hidden", "true");
    nameGroup.appendChild(separator);

    meta.appendChild(nameGroup);

    const text = document.createElement("span");
    text.classList.add("content");
    text.innerHTML = renderMessageContent(data);

    meta.appendChild(text);

    wrapper.appendChild(meta);
  } else {
    if (data.type) {
      wrapper.classList.add(data.type);
    }
    if (data.message != null) {
      wrapper.textContent = data.message;
    } else {
      wrapper.textContent = "";
    }
  }

  return wrapper;
}

function enforceMessageLimit() {
  if (!chatEl) {
    return;
  }
  while (chatEl.children.length > maxMessages) {
    const firstChild = chatEl.firstChild;
    if (!firstChild) {
      break;
    }
    if (
      moderationMenuAnchor &&
      firstChild instanceof HTMLElement &&
      firstChild.contains(moderationMenuAnchor)
    ) {
      hideModerationMenu();
    }
    chatEl.removeChild(firstChild);
  }
}

function addMessageToDom(payload) {
  if (!chatEl) {
    return;
  }
  const element = createMessageElement(payload);
  chatEl.appendChild(element);
  enforceMessageLimit();
  if (!hydratingMessages) {
    recordMessageForPersistence(payload);
  }
  positionModerationMenu();
}

function appendMessage(payload) {
  if (!payload) {
    return;
  }
  const atBottom = isNearBottom();
  if (isChatPaused() || !atBottom) {
    if (!pausedForScroll && !atBottom) {
      pausedForScroll = true;
    }
    bufferIncomingMessage(payload);
    return;
  }
  addMessageToDom(payload);
  scrollToBottom();
}

function renderMessageContent(payload) {
  const raw = String(payload.message ?? "");
  if (payload.platform === "kick") {
    const regex = /\[emote:(\d+):([^\]]+)\]/g;
    let match;
    let lastIndex = 0;
    const segments = [];
    while ((match = regex.exec(raw)) !== null) {
      const [token, id, name] = match;
      if (match.index > lastIndex) {
        segments.push(escapeHtml(raw.slice(lastIndex, match.index)));
      }
      const safeName = name.replace(/"/g, "&quot;");
      const src = `https://files.kick.com/emotes/${id}/fullsize`;
      segments.push(
        `<img class="emote" src="${src}" alt="${safeName}" title="${safeName}" />`
      );
      lastIndex = match.index + token.length;
    }
    if (lastIndex < raw.length) {
      segments.push(escapeHtml(raw.slice(lastIndex)));
    }
    return segments.join("");
  }
  if (payload.platform === "twitch" && Array.isArray(payload.emotes) && payload.emotes.length) {
    const replacements = [];
    payload.emotes.forEach((meta) => {
      if (!meta || !meta.id || !Array.isArray(meta.positions)) {
        return;
      }
      meta.positions.forEach(([start, end]) => {
        replacements.push({
          start,
          end,
          url: twitchEmoteUrl(meta.id),
          alt: meta.name || meta.id,
        });
      });
    });
    replacements.sort((a, b) => a.start - b.start);
    const segments = [];
    let cursor = 0;
    replacements.forEach(({ start, end, url, alt }) => {
      if (start < cursor) {
        return;
      }
      if (start > cursor) {
        segments.push(escapeHtml(raw.slice(cursor, start)));
      }
      const safeAlt = (alt || "emote").replace(/"/g, "&quot;");
      segments.push(
        `<img class="emote" src="${url}" alt="${safeAlt}" title="${safeAlt}" />`
      );
      cursor = end + 1;
    });
    if (cursor < raw.length) {
      segments.push(escapeHtml(raw.slice(cursor)));
    }
    if (segments.length) {
      return segments.join("");
    }
  }
  return escapeHtml(raw);
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}

function twitchEmoteUrl(id) {
  return `https://static-cdn.jtvnw.net/emoticons/v2/${id}/default/dark/2.0`;
}

disconnectBtn.disabled = true;

function connect(options = {}) {
  const preserveMessages = Boolean(options && options.preserveMessages);
  const twitch = twitchInput.value.trim();
  const kick = kickInput.value.trim();

  if (!twitch && !kick) {
    setStatus("Enter at least one streamer to start listening.");
    return;
  }

  hideModerationMenu();
  const previousTwitch = currentChannels.twitch || "";
  const previousKick = currentChannels.kick || "";
  const channelsChanged = twitch !== previousTwitch || kick !== previousKick;

  if (!preserveMessages && channelsChanged) {
    clearChat({ resetPersisted: true });
  }
  if (!preserveMessages && !channelsChanged && persistenceAvailable) {
    // Ensure persisted state reflects current values without dropping messages
    persistedState.channels.twitch = twitch;
    persistedState.channels.kick = kick;
    savePersistedState();
  }
  setStatus("Connecting…");
  setButtonBusy(connectBtn, true, "Connecting…");
  setButtonBusy(disconnectBtn, false);
  disconnectBtn.disabled = true;

  currentChannels.twitch = twitch;
  currentChannels.kick = kick;
  if (persistenceAvailable) {
    persistedState.channels.twitch = twitch;
    persistedState.channels.kick = kick;
    savePersistedState();
  }

  if (socket) {
    socket.close();
  }

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const activeSocket = new WebSocket(`${protocol}://${window.location.host}/ws`);
  socket = activeSocket;

  activeSocket.addEventListener("open", (event) => {
    if (event.target !== activeSocket) {
      return;
    }
    setStatus("Connection open. Joined chats.");
    enableMessageInput();
    setButtonBusy(connectBtn, false);
    setButtonBusy(disconnectBtn, false);
    disconnectBtn.disabled = false;
    markConnected();
    if (persistenceAvailable) {
      persistedState.connected = true;
      persistedState.channels.twitch = twitch;
      persistedState.channels.kick = kick;
      savePersistedState();
    }
    activeSocket.send(
      JSON.stringify({
        action: "subscribe",
        twitch,
        kick,
      })
    );
  });

  activeSocket.addEventListener("message", (event) => {
    if (event.target !== activeSocket) {
      return;
    }
    try {
      const payload = JSON.parse(event.data);
      appendMessage(payload);
    } catch (err) {
      console.error("Failed to parse message", err);
    }
  });

  activeSocket.addEventListener("close", (event) => {
    if (event.target !== activeSocket) {
      return;
    }
    if (socket === activeSocket) {
      socket = null;
    }
    if (persistenceAvailable) {
      persistedState.connected = false;
      savePersistedState();
    }
    setStatus("Disconnected.");
    disableMessageInput();
    hideModerationMenu();
    setButtonBusy(connectBtn, false);
    setButtonBusy(disconnectBtn, false);
    disconnectBtn.disabled = true;
    resetConnectState();
  });

  activeSocket.addEventListener("error", (event) => {
    if (event.target !== activeSocket) {
      return;
    }
    if (socket === activeSocket) {
      socket = null;
    }
    if (persistenceAvailable) {
      persistedState.connected = false;
      savePersistedState();
    }
    setStatus("WebSocket error encountered.");
    setButtonBusy(connectBtn, false);
    setButtonBusy(disconnectBtn, false);
    disconnectBtn.disabled = true;
    resetConnectState();
  });
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  connect();
});

function currentRedirectPath() {
  return encodeURIComponent(`${window.location.pathname}${window.location.search}`);
}

twitchLoginBtn.addEventListener("click", () => {
  if (twitchLoginBtn.disabled) {
    return;
  }
  window.location.href = `/auth/twitch/login?redirect_path=${currentRedirectPath()}`;
});

kickLoginBtn.addEventListener("click", () => {
  if (kickLoginBtn.disabled) {
    return;
  }
  window.location.href = `/auth/kick/login?redirect_path=${currentRedirectPath()}`;
});

logoutBtn.addEventListener("click", async () => {
  try {
    await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch (err) {
    console.warn("Logout failed", err);
  }
  authState = { authenticated: false, accounts: [], user: null };
  if (persistenceAvailable) {
    persistedState = createDefaultPersistedState();
    savePersistedState();
  }
  await refreshAuthStatus();
});

twitchInput.addEventListener("input", updateMessageControls);
kickInput.addEventListener("input", updateMessageControls);

const fallbackPalette = [
  "#ff75e6",
  "#ade55c",
  "#fd7eff",
  "#1f9bff",
  "#f8d568",
  "#c792ea",
  "#ff955c",
  "#4edfff",
  "#f97316",
  "#22d3ee",
  "#34d399",
  "#a855f7",
  "#facc15",
  "#fb7185",
  "#e0e7ff",
  "#bef264",
];

const colorCache = new Map();

function colorCacheKey(payload) {
  const platform = String(payload.platform || "").toLowerCase();
  const user = String(payload.user || "").toLowerCase();
  return `${platform}:${user}`;
}

function resolveUsernameColor(payload) {
  const key = colorCacheKey(payload);
  const provided = payload.platform === "twitch" ? normalizeColor(payload.color) : null;
  if (provided) {
    colorCache.set(key, provided);
    return provided;
  }
  if (colorCache.has(key)) {
    return colorCache.get(key);
  }
  const fallback = fallbackUsernameColor(payload.user || "");
  colorCache.set(key, fallback);
  return fallback;
}

function fallbackUsernameColor(name) {
  const normalized = String(name || "").toLowerCase();
  if (!normalized) {
    return fallbackPalette[0];
  }
  let hash = 0;
  for (let i = 0; i < normalized.length; i += 1) {
    hash = (hash << 5) - hash + normalized.charCodeAt(i);
    hash |= 0;
  }
  return fallbackPalette[Math.abs(hash) % fallbackPalette.length];
}

function normalizeColor(value) {
  if (value == null) {
    return null;
  }
  const raw = String(value).trim();
  if (!raw) {
    return null;
  }
  if (/^(rgb|hsl)a?\(/i.test(raw)) {
    return raw;
  }
  if (/^#[0-9a-fA-F]{3}$/.test(raw)) {
    return raw;
  }
  if (/^#[0-9a-fA-F]{4}$/.test(raw)) {
    const rgb = raw.slice(1, 4);
    return `#${rgb[0]}${rgb[0]}${rgb[1]}${rgb[1]}${rgb[2]}${rgb[2]}`;
  }
  if (/^#[0-9a-fA-F]{6}$/.test(raw)) {
    return raw;
  }
  if (/^#[0-9a-fA-F]{8}$/.test(raw)) {
    return `#${raw.slice(1, 7)}`;
  }
  if (/^0x[0-9a-fA-F]{6,8}$/i.test(raw)) {
    return `#${raw.slice(2, 8)}`;
  }
  if (/^[0-9a-fA-F]{6,8}$/.test(raw)) {
    return `#${raw.slice(0, 6)}`;
  }
  return null;
}

disconnectBtn.addEventListener("click", () => {
  setButtonBusy(disconnectBtn, true, "Disconnecting…");
  const closingSocket = socket;
  if (closingSocket) {
    closingSocket.close();
    socket = null;
  } else {
    setTimeout(() => setButtonBusy(disconnectBtn, false), 200);
  }
  setStatus("Disconnected.");
  disableMessageInput();
  hideModerationMenu();
  resetConnectState();
  if (persistenceAvailable) {
    persistedState.connected = false;
    savePersistedState();
  }
});

function restoreFromPersistedState() {
  if (!persistenceAvailable) {
    return;
  }
  const channels = persistedState.channels || {};
  if (twitchInput) {
    twitchInput.value = channels.twitch || "";
  }
  if (kickInput) {
    kickInput.value = channels.kick || "";
  }
  currentChannels.twitch = channels.twitch || "";
  currentChannels.kick = channels.kick || "";

  if (persistedState.messages && persistedState.messages.length) {
    hydratingMessages = true;
    persistedState.messages.forEach((message) => {
      addMessageToDom(message);
    });
    hydratingMessages = false;
    scrollToBottom();
    updatePauseBanner();
  }

  if (
    persistedState.connected &&
    (channels.twitch || channels.kick) &&
    (!socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING)
  ) {
    setTimeout(() => {
      if (
        !socket ||
        socket.readyState === WebSocket.CLOSED ||
        socket.readyState === WebSocket.CLOSING
      ) {
        connect({ preserveMessages: true });
      }
    }, 0);
  }
}

// Message input event listeners
messageInput.addEventListener("keypress", (event) => {
  if (event.key === "Enter" && !messageInput.disabled) {
    event.preventDefault();
    sendMessage();
  }
});

sendButton.addEventListener("click", () => {
  if (!messageInput.disabled) {
    sendMessage();
  }
});

async function sendMessage() {
  if (sendingMessage) {
    return;
  }
  const platform = platformSelect.value;
  const message = messageInput.value.trim();

  if (!platform) {
    setStatus("Select a platform to send messages.");
    return;
  }
  if (!message) {
    return;
  }
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    setStatus("WebSocket is not connected.");
    return;
  }

  const formatPlatformLabel = (value) => {
    if (value === "twitch") {
      return "Twitch";
    }
    if (value === "kick") {
      return "Kick";
    }
    return value;
  };

  const targets =
    platform === "both"
      ? [
          { platform: "twitch", channel: twitchInput.value.trim() },
          { platform: "kick", channel: kickInput.value.trim() },
        ]
      : [{ platform, channel: platform === "twitch" ? twitchInput.value.trim() : kickInput.value.trim() }];

  if (platform === "both") {
    const missing = targets.filter((target) => !target.channel).map((target) => target.platform);
    if (missing.length) {
      setStatus("Enter both Twitch and Kick channels before sending.");
      return;
    }
  } else if (!targets[0].channel) {
    setStatus(`Enter a ${platform} channel before sending.`);
    return;
  }

  const previousPlatformValue = platform;

  const sendButtonWasDisabled = sendButton.disabled;

  sendingMessage = true;

  sendButton.disabled = true;
  messageInput.disabled = true;
  platformSelect.disabled = true;

  try {
    const successes = [];
    const failures = [];

    for (const target of targets) {
      try {
        const response = await fetch("/chat/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ platform: target.platform, channel: target.channel, message }),
        });

        if (!response.ok) {
          const errorBody = await response.json().catch(() => ({}));
          const detail =
            errorBody && errorBody.detail
              ? errorBody.detail
              : response.statusText || "Unknown error";
          const detailText =
            typeof detail === "string" ? detail : JSON.stringify(detail);
          failures.push({ platform: target.platform, detail: detailText });
          console.error(
            `Failed to send chat message via ${target.platform}: ${detailText}`
          );
          continue;
        }

        successes.push(target.platform);
      } catch (err) {
        console.error(`Network error while sending via ${target.platform}`, err);
        failures.push({ platform: target.platform, detail: "Network error" });
      }
    }

    if (failures.length) {
      const failure = failures[0];
      const failureLabel = formatPlatformLabel(failure.platform);
      if (successes.length) {
        const successLabel = successes.map((value) => formatPlatformLabel(value)).join(" and ");
        setStatus(
          `Message sent via ${successLabel}, but failed via ${failureLabel}: ${failure.detail}`
        );
      } else {
        setStatus(`Failed to send via ${failureLabel}: ${failure.detail}`);
      }
      return;
    }

    messageInput.value = "";
    if (targets.length === 2) {
      setStatus("Message sent to Twitch and Kick.");
    } else {
      const prettyPlatform = formatPlatformLabel(targets[0].platform);
      setStatus(`Message sent via ${prettyPlatform}.`);
    }
  } catch (err) {
    console.error("Failed to send chat message", err);
    setStatus("Network error while sending chat message.");
  } finally {
    sendingMessage = false;
    updateMessageControls();
    if (previousPlatformValue) {
      const options = Array.from(platformSelect.options);
      const matchingOption = options.find((option) => option.value === previousPlatformValue);
      if (matchingOption) {
        platformSelect.value = previousPlatformValue;
      }
    }
    applySendButtonStyle(!platformSelect.disabled ? platformSelect.value : "");
    if (sendButtonWasDisabled && !sendButton.disabled) {
      sendButton.disabled = true;
    }
    if (!messageInput.disabled) {
      messageInput.focus();
    }
  }
}

restoreFromPersistedState();
applySendButtonStyle("");
updateMessageControls();
refreshAuthStatus();
