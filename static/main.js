const form = document.getElementById("channelForm");
const twitchInput = document.getElementById("twitchInput");
const kickInput = document.getElementById("kickInput");
const chatEl = document.getElementById("chat");
const connectBtn = document.getElementById("connectBtn");
const disconnectBtn = document.getElementById("disconnectBtn");
const messageInput = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const platformSelect = document.getElementById("platformSelect");
const twitchLabel = document.getElementById("twitchLabel");
const kickLabel = document.getElementById("kickLabel");
const twitchInputOverlay = document.getElementById("twitchInputDecor");
const kickInputOverlay = document.getElementById("kickInputDecor");
const authStatusEl = document.getElementById("authStatus");
const twitchLoginBtn = document.getElementById("twitchLoginBtn");
const kickLoginBtn = document.getElementById("kickLoginBtn");
const logoutBtn = document.getElementById("logoutBtn");
const chatPauseBanner = document.getElementById("chatPauseBanner");
const chatPauseLabel = document.getElementById("chatPauseLabel");
const chatResumeButton = document.getElementById("chatResumeButton");
const replyPreview = document.getElementById("replyPreview");
const replyPreviewSpacer = document.getElementById("replyPreviewSpacer");
const replyPreviewLabel = document.getElementById("replyPreviewLabel");
const replyPreviewMessage = document.getElementById("replyPreviewMessage");
const replyCancelButton = document.getElementById("replyCancelButton");
const fullscreenToggle = document.getElementById("fullscreenToggle");
const chatArea = document.querySelector(".chat-area");
const fontSizeIncrease = document.getElementById("fontSizeIncrease");
const fontSizeDecrease = document.getElementById("fontSizeDecrease");
const messageInputContainer = document.querySelector(".message-input-container");
let chatInputOffset = 0;

let socket = null;
let sendingMessage = false;
let connectionReady = false;
let connectionFinalized = false;
let authState = { authenticated: false, accounts: [], user: null };
const maxMessages = 150;
const scrollLockEpsilon = 4;
const bufferedMessageLimit = 200;
const bufferedMessages = [];
let unreadBufferedCount = 0;
let pausedForScroll = false;
let suppressScrollHandler = false;
const currentChannels = { twitch: [], kick: [] };
const platformConnectionState = { twitch: false, kick: false };
const platformEverConnected = { twitch: false, kick: false };
const connectedChannelSets = {
  twitch: new Set(),
  kick: new Set(),
};
const everConnectedChannelSets = {
  twitch: new Set(),
  kick: new Set(),
};
const attemptedChannelSets = {
  twitch: new Set(),
  kick: new Set(),
};
const platformAttempted = { twitch: false, kick: false };
let replyTarget = null;
let replyTargetElement = null;
let preferredSendPlatform = "";
let chatFontScale = 1;

const storageKey = "combinedChatState";

function createDefaultPersistedState() {
  return {
    connected: false,
    channels: { twitch: [], kick: [] },
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

function normalizeChannelSlug(platform, value) {
  if (value == null) {
    return "";
  }
  const raw = String(value).trim();
  if (!raw) {
    return "";
  }
  let slug = raw
    .replace(/^https?:\/\/(www\.)?twitch\.tv\//i, "")
    .replace(/^https?:\/\/(www\.)?kick\.com\//i, "")
    .replace(/^[@#]/, "")
    .replace(/\s+/g, "");
  if (platform === "kick") {
    slug = slug.replace(/_/g, "-");
  }
  return slug.toLowerCase();
}

function parseChannelList(raw, platform) {
  const result = [];
  const seen = new Set();
  const tokens = Array.isArray(raw)
    ? raw
    : typeof raw === "string"
      ? raw.replace(/\n/g, ",").split(",")
      : raw != null
        ? [raw]
        : [];
  for (const token of tokens) {
    const cleaned = normalizeChannelSlug(platform, token);
    if (!cleaned || seen.has(cleaned)) {
      continue;
    }
    seen.add(cleaned);
    result.push(cleaned);
    if (result.length >= 10) {
      break;
    }
  }
  return result;
}

function formatChannelList(channels) {
  if (!Array.isArray(channels) || !channels.length) {
    return "";
  }
  return channels.join(", ");
}

function channelArraysEqual(a, b) {
  if (a === b) {
    return true;
  }
  if (!Array.isArray(a) || !Array.isArray(b)) {
    return false;
  }
  if (a.length !== b.length) {
    return false;
  }
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) {
      return false;
    }
  }
  return true;
}

function platformOptionValue(platform, channel = "*") {
  if (platform === "both") {
    return "both:*";
  }
  const key = normalizePlatformKey(platform);
  if (!key) {
    return "";
  }
  const slug =
    channel && channel !== "*"
      ? normalizeChannelSlug(key, channel)
      : "*";
  return slug === "*" ? `${key}:*` : `${key}:${slug}`;
}

function parsePlatformOptionValue(value) {
  if (typeof value !== "string" || !value) {
    return null;
  }
  if (value === "both:*") {
    return { platform: "both", channel: "*" };
  }
  const match = value.match(/^(twitch|kick)(?::(.+))?$/);
  if (!match) {
    return null;
  }
  const platform = match[1];
  const channelPart = match[2] ?? "*";
  const channel = channelPart === "*" ? "*" : normalizeChannelSlug(platform, channelPart);
  return { platform, channel };
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
      nextState.channels.twitch = parseChannelList(data.channels.twitch, "twitch");
      nextState.channels.kick = parseChannelList(data.channels.kick, "kick");
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

updateConnectionIndicators();

const updateChatInputOffset = () => {
  if (!messageInputContainer) {
    return;
  }
  const nextOffset = (messageInputContainer.offsetHeight || 0) + 12;
  if (nextOffset !== chatInputOffset) {
    chatInputOffset = nextOffset;
    updatePauseBannerOffset();
  }
};

if (messageInputContainer) {
  updateChatInputOffset();
  window.addEventListener("resize", updateChatInputOffset);
  if (typeof ResizeObserver !== "undefined") {
    const resizeObserver = new ResizeObserver(updateChatInputOffset);
    resizeObserver.observe(messageInputContainer);
  }
}

if (fullscreenToggle && chatArea) {
  const updateFullscreenState = () => {
    const isActive = chatArea.classList.contains("chat-area--expanded");
    fullscreenToggle.classList.toggle("is-active", isActive);
    fullscreenToggle.setAttribute(
      "aria-label",
      isActive ? "Exit fullscreen" : "Enter fullscreen"
    );
    document.body.classList.toggle("chat-only-mode", isActive);
    updatePauseBannerOffset();
  };

  fullscreenToggle.addEventListener("click", () => {
    chatArea.classList.toggle("chat-area--expanded");
    updateFullscreenState();
  });

  updateFullscreenState();
}
if (chatArea && (fontSizeIncrease || fontSizeDecrease)) {
  const fontScaleBounds = { min: 0.75, max: 1.3, step: 0.1 };
  const applyChatFontScale = () => {
    chatArea.style.setProperty("--chat-font-scale", chatFontScale.toFixed(2));
    if (fontSizeIncrease) {
      const atMax = chatFontScale >= fontScaleBounds.max - 0.001;
      fontSizeIncrease.disabled = atMax;
    }
    if (fontSizeDecrease) {
      const atMin = chatFontScale <= fontScaleBounds.min + 0.001;
      fontSizeDecrease.disabled = atMin;
    }
  };
  const setChatFontScale = (next) => {
    const clamped = Math.min(
      fontScaleBounds.max,
      Math.max(fontScaleBounds.min, Math.round(next * 100) / 100)
    );
    if (clamped !== chatFontScale) {
      chatFontScale = clamped;
      applyChatFontScale();
    }
  };
  applyChatFontScale();
  if (fontSizeIncrease) {
    fontSizeIncrease.addEventListener("click", () => {
      setChatFontScale(chatFontScale + fontScaleBounds.step);
    });
  }
  if (fontSizeDecrease) {
    fontSizeDecrease.addEventListener("click", () => {
      setChatFontScale(chatFontScale - fontScaleBounds.step);
    });
  }
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
const moderationMenuHost = chatArea || document.body;
moderationMenuHost.appendChild(moderationMenu);

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
  connectionFinalized = true;
}

function resetConnectState() {
  if (!connectBtn) {
    return;
  }
  connectionFinalized = false;
  connectBtn.classList.remove("is-active");
  connectBtn.textContent = "Connect";
  connectBtn.disabled = false;
}

function finalizeConnection() {
  if (!connectBtn || connectionFinalized) {
    return;
  }
  setButtonBusy(connectBtn, false);
  markConnected();
}

function normalizePlatformKey(value) {
  const raw = typeof value === "string" ? value.toLowerCase() : "";
  return raw === "twitch" || raw === "kick" ? raw : "";
}

function resetPlatformConnectionState(options = {}) {
  const preserveEverConnected = Boolean(
    options && options.preserveEverConnected
  );
  const resetAttempts = Boolean(options && options.resetAttempts);
  let changed = false;
  Object.keys(platformConnectionState).forEach((key) => {
    const activeSet = connectedChannelSets[key];
    if (activeSet && activeSet.size) {
      activeSet.clear();
      changed = true;
    }
    const everSet = everConnectedChannelSets[key];
    platformConnectionState[key] = false;
    if (!preserveEverConnected) {
      if (everSet && everSet.size) {
        everSet.clear();
        changed = true;
      }
      if (platformEverConnected[key]) {
        platformEverConnected[key] = false;
        changed = true;
      }
    } else if (everSet && everSet.size) {
      platformEverConnected[key] = true;
    }
    if (resetAttempts) {
      const attemptedSet = attemptedChannelSets[key];
      if (attemptedSet && attemptedSet.size) {
        attemptedSet.clear();
        changed = true;
      }
      if (platformAttempted[key]) {
        platformAttempted[key] = false;
        changed = true;
      }
    }
  });
  if (changed) {
    updateMessageControls();
    updateConnectionIndicators();
  }
}

function setAttemptedChannels(platform, channels) {
  const key = normalizePlatformKey(platform);
  if (!key) {
    return;
  }
  const attemptedSet = attemptedChannelSets[key];
  if (!attemptedSet) {
    return;
  }
  attemptedSet.clear();
  if (Array.isArray(channels)) {
    channels.forEach((channel) => {
      if (channel) {
        attemptedSet.add(channel);
      }
    });
  }
  if (channels && channels.length) {
    platformAttempted[key] = true;
  }
}

function renderChannelInputDecorations() {
  const pairs = [
    {
      platform: "twitch",
      input: twitchInput,
      overlay: twitchInputOverlay,
    },
    {
      platform: "kick",
      input: kickInput,
      overlay: kickInputOverlay,
    },
  ];

  pairs.forEach(({ platform, input, overlay }) => {
    if (!input || !overlay) {
      return;
    }
    const control = input.parentElement;
    if (!control) {
      return;
    }
    const rawValue = input.value || "";
    const trimmed = rawValue.trim();
    if (!trimmed) {
      control.classList.remove("has-decoration");
      overlay.textContent = "";
      return;
    }

    const fragment = document.createDocumentFragment();
    const connectedSet = connectedChannelSets[platform] || new Set();
    const attemptedSet = attemptedChannelSets[platform] || new Set();
    const attempted = Boolean(platformAttempted[platform]);
    const segments = rawValue.split(/(,\s*)/);
    let hasDecoration = false;

    segments.forEach((segment) => {
      if (!segment) {
        return;
      }
      if (/,/.test(segment)) {
        const separator = document.createElement("span");
        separator.className = "channel-input__separator";
        separator.textContent = segment;
        fragment.appendChild(separator);
        return;
      }
      const token = document.createElement("span");
      token.className = "channel-input__token";
      token.textContent = segment;
      const slug = normalizeChannelSlug(platform, segment);
      const isValid = Boolean(slug);
      const isConnected = isValid && connectedSet.has(slug);
      if (isConnected) {
        token.classList.add("is-connected");
        hasDecoration = true;
      } else if (isValid && attempted && attemptedSet.has(slug)) {
        token.classList.add("is-disconnected");
        hasDecoration = true;
      }
      fragment.appendChild(token);
    });
    if (hasDecoration) {
      overlay.replaceChildren(fragment);
      control.classList.add("has-decoration");
    } else {
      control.classList.remove("has-decoration");
      overlay.innerHTML = "";
    }
  });
}

function updateConnectionIndicators() {
  const pairs = [
    { platform: "twitch", label: twitchLabel },
    { platform: "kick", label: kickLabel },
  ];

  pairs.forEach(({ platform, label }) => {
    if (!label) {
      return;
    }
    const connected = Boolean(platformConnectionState[platform]);
    const channelCount = connectedChannelSets[platform]
      ? connectedChannelSets[platform].size
      : 0;
    const labelText = connected
      ? channelCount > 1
        ? `${formatPlatformName(platform)} chat connected (${channelCount} channels)`
        : `${formatPlatformName(platform)} chat connected`
      : `${formatPlatformName(platform)} chat disconnected`;
    label.setAttribute("aria-label", labelText);
    label.setAttribute("title", labelText);
  });

  renderChannelInputDecorations();
}

function updatePlatformConnectionState(payload) {
  if (!payload || typeof payload !== "object") {
    return;
  }
  const platform = normalizePlatformKey(payload.platform);
  if (!platform) {
    return;
  }
  const type = typeof payload.type === "string" ? payload.type : "";
  const message = typeof payload.message === "string" ? payload.message : "";
  const channelSlug = normalizeChannelSlug(platform, payload.channel || "");
  const normalizedMessage = message.toLowerCase();
  const indicatesDisconnect =
    normalizedMessage.includes("disconnected from") ||
    normalizedMessage.includes("stopped listening to");
  const indicatesConnect = normalizedMessage.includes("connected to");

  if (!message && type !== "error" && !channelSlug) {
    return;
  }

  const channelSet = connectedChannelSets[platform];
  const everSet = everConnectedChannelSets[platform];
  let stateChanged = false;

  if (type === "status" && indicatesConnect) {
    if (channelSlug) {
      if (!channelSet.has(channelSlug)) {
        channelSet.add(channelSlug);
        stateChanged = true;
      }
      if (!everSet.has(channelSlug)) {
        everSet.add(channelSlug);
        platformEverConnected[platform] = true;
        stateChanged = true;
      }
    } else if (!platformConnectionState[platform]) {
      platformConnectionState[platform] = true;
      stateChanged = true;
    }
  } else if (type === "status" && indicatesDisconnect) {
    if (channelSlug && channelSet.has(channelSlug)) {
      channelSet.delete(channelSlug);
      stateChanged = true;
    } else if (!channelSlug && platformConnectionState[platform]) {
      stateChanged = true;
    }
  } else if (type === "error" && channelSlug && channelSet.has(channelSlug)) {
    channelSet.delete(channelSlug);
    stateChanged = true;
  }

  const nextConnected = channelSet.size > 0;
  if (platformConnectionState[platform] !== nextConnected) {
    platformConnectionState[platform] = nextConnected;
    stateChanged = true;
  }

  if (stateChanged) {
    updateMessageControls();
    updateConnectionIndicators();
  }
  if (!connectionFinalized && platformConnectionState[platform]) {
    finalizeConnection();
  }
}

function announceDisconnectStatus() {
  const notices = [];
  const kickEver = Array.from(everConnectedChannelSets.kick);
  if (kickEver.length) {
    const summary =
      kickEver.length === 1
        ? kickEver[0]
        : `${kickEver.slice(0, 10).join(", ")}`;
    notices.push(`Disconnected from Kick chat for ${summary}`);
  }
  const twitchEver = Array.from(everConnectedChannelSets.twitch);
  if (twitchEver.length) {
    const summary =
      twitchEver.length === 1
        ? twitchEver[0]
        : `${twitchEver.slice(0, 10).join(", ")}`;
    notices.push(`Disconnected from Twitch chat for ${summary}`);
  }
  if (notices.length === 0) {
    notices.push("Disconnected.");
  }
  notices.forEach((notice) => setStatus(notice));
  resetPlatformConnectionState({ resetAttempts: true });
}

function setStatus(message, options = {}) {
  const opts = options || {};
  const text =
    typeof message === "string"
      ? message.trim()
      : message != null
        ? String(message).trim()
        : "";
  if (!text) {
    return;
  }
  if (opts.silent) {
    return;
  }
  const type = opts.type === "error" ? "error" : "status";
  appendMessage({ type, message: text });
}

function extractApiErrorMessage(detail, fallback = "Unknown error") {
  if (detail == null || detail === "") {
    return fallback;
  }
  if (typeof detail === "string") {
    const trimmed = detail.trim();
    return trimmed ? trimmed : fallback;
  }
  if (typeof detail === "number" || typeof detail === "boolean") {
    return String(detail);
  }
  if (Array.isArray(detail)) {
    for (const entry of detail) {
      const message = extractApiErrorMessage(entry, "");
      if (message) {
        return message;
      }
    }
    return fallback;
  }
  if (typeof detail === "object") {
    if (typeof detail.message === "string" && detail.message.trim()) {
      return detail.message.trim();
    }
    const nestedKeys = ["kick_error", "twitch_error", "error", "detail", "payload", "errors"];
    for (const key of nestedKeys) {
      if (Object.prototype.hasOwnProperty.call(detail, key)) {
        const message = extractApiErrorMessage(detail[key], "");
        if (message) {
          return message;
        }
      }
    }
    for (const value of Object.values(detail)) {
      const message = extractApiErrorMessage(value, "");
      if (message) {
        return message;
      }
    }
  }
  return fallback;
}

function hasAccount(platform) {
  return (
    Array.isArray(authState.accounts) &&
    authState.accounts.some((account) => account.platform === platform)
  );
}

function buildPlatformOptions() {
  const options = [];
  const twitchConnected = Array.from(connectedChannelSets.twitch || []);
  const kickConnected = Array.from(connectedChannelSets.kick || []);
  const twitchLinked = hasAccount("twitch");
  const kickLinked = hasAccount("kick");

  const addOption = (value, label) => {
    options.push({ value, label });
  };

  if (
    twitchLinked &&
    kickLinked &&
    twitchConnected.length &&
    kickConnected.length
  ) {
    addOption(
      "both:*",
      twitchConnected.length + kickConnected.length > 2
        ? "Twitch + Kick (All)"
        : "Twitch + Kick (both)"
    );
  }

  if (twitchLinked && twitchConnected.length) {
    if (twitchConnected.length > 1) {
      addOption(
        "twitch:*",
        `Twitch (All ${twitchConnected.length})`
      );
    }
    twitchConnected.forEach((channel) => {
      addOption(`twitch:${channel}`, `Twitch (${channel})`);
    });
  }

  if (kickLinked && kickConnected.length) {
    if (kickConnected.length > 1) {
      addOption(
        "kick:*",
        `Kick (All ${kickConnected.length})`
      );
    }
    kickConnected.forEach((channel) => {
      addOption(`kick:${channel}`, `Kick (${channel})`);
    });
  }

  if (replyTarget && replyTarget.platform) {
    const replyValue = platformOptionValue(
      replyTarget.platform,
      replyTarget.channel || "*"
    );
    const filtered = options.filter((option) => option.value === replyValue);
    if (filtered.length) {
      return filtered;
    }
    return options.filter((option) =>
      option.value.startsWith(`${replyTarget.platform}:`)
    );
  }

  return options;
}

function applySendButtonStyle(selectionValue) {
  const parsed = parsePlatformOptionValue(selectionValue);
  const platform = parsed ? parsed.platform : normalizePlatformKey(selectionValue) || "";
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

function normalizeUsername(value) {
  if (typeof value !== "string") {
    return "";
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  return trimmed.startsWith("@") ? trimmed.slice(1) : trimmed;
}

function truncateText(value, maxLength = 140) {
  const raw = typeof value === "string" ? value : String(value ?? "");
  if (raw.length <= maxLength) {
    return raw;
  }
  return `${raw.slice(0, Math.max(0, maxLength - 1))}…`;
}

function renderReplyPreviewContent(payload) {
  if (!payload || !payload.message) {
    return "";
  }
  const snippetPayload = {
    message: payload.message,
    platform: payload.platform || "",
  };
  if (Array.isArray(payload.emotes) && payload.emotes.length) {
    snippetPayload.emotes = payload.emotes;
  }
  return renderMessageContent(snippetPayload);
}

function ensureReplyTargetVisible(element) {
  if (!chatEl || !(element instanceof HTMLElement)) {
    return;
  }
  const containerRect = chatEl.getBoundingClientRect();
  const elementRect = element.getBoundingClientRect();
  if (elementRect.top >= containerRect.top && elementRect.bottom <= containerRect.bottom) {
    return;
  }
  element.scrollIntoView({ behavior: "smooth", block: "center" });
}

function updateReplyPreview() {
  if (!replyPreview) {
    return;
  }
  const wasAtBottom = isNearBottom();
  if (chatEl) {
    if (replyPreviewSpacer && replyPreviewSpacer.parentElement !== chatEl) {
      chatEl.appendChild(replyPreviewSpacer);
    }
    if (replyPreview.parentElement !== chatEl) {
      chatEl.appendChild(replyPreview);
    }
    if (
      replyPreviewSpacer &&
      replyPreview.parentElement === chatEl &&
      replyPreview.previousElementSibling !== replyPreviewSpacer
    ) {
      chatEl.insertBefore(replyPreviewSpacer, replyPreview);
    }
  }
  if (!replyTarget) {
    if (replyPreviewSpacer) {
      replyPreviewSpacer.classList.add("hidden");
    }
    replyPreview.classList.add("hidden");
    if (replyPreviewLabel) {
      replyPreviewLabel.textContent = "";
    }
    if (replyPreviewMessage) {
      replyPreviewMessage.textContent = "";
    }
  } else {
    const username = normalizeUsername(replyTarget.username || replyTarget.user || "");
    if (replyPreviewLabel) {
      replyPreviewLabel.textContent = username ? `Replying to @${username}:` : "Replying:";
    }
    if (replyPreviewMessage) {
      const snippetHtml = renderReplyPreviewContent(replyTarget);
      replyPreviewMessage.innerHTML = snippetHtml;
    }
    if (replyPreviewSpacer) {
      replyPreviewSpacer.classList.remove("hidden");
    }
    replyPreview.classList.remove("hidden");
  }
  if (wasAtBottom) {
    pausedForScroll = false;
    flushBufferedMessages({ snapToBottom: true });
  } else {
    updatePauseBanner();
  }
  updatePauseBannerOffset();
}

function getAvailablePlatformValues() {
  if (!platformSelect) {
    return [];
  }
  return Array.from(platformSelect.options).map((option) => option.value);
}

function restorePreferredPlatformSelection() {
  if (!platformSelect || platformSelect.disabled) {
    return;
  }
  const availableValues = getAvailablePlatformValues();
  let value = "";
  if (preferredSendPlatform && availableValues.includes(preferredSendPlatform)) {
    value = preferredSendPlatform;
  } else if (availableValues.length) {
    value = availableValues[0];
  }
  if (value) {
    platformSelect.value = value;
  } else {
    platformSelect.value = "";
  }
  applySendButtonStyle(value);
}

function clearReplyTarget(options = {}) {
  if (replyTargetElement) {
    replyTargetElement.classList.remove("message--reply-target");
  }
  replyTarget = null;
  replyTargetElement = null;
  updateReplyPreview();
  if (options.updateControls !== false) {
    updateMessageControls();
  } else {
    restorePreferredPlatformSelection();
  }
}

function setReplyTarget(target, element) {
  if (!target || !target.messageId) {
    setStatus("This message cannot be replied to.");
    return;
  }

  if (replyTargetElement && replyTargetElement !== element) {
    replyTargetElement.classList.remove("message--reply-target");
  }

  const selectionValue = platformOptionValue(target.platform, target.channel || "*");
  replyTarget = { ...target, optionValue: selectionValue };
  replyTargetElement = element instanceof HTMLElement ? element : null;
  if (replyTargetElement) {
    replyTargetElement.classList.add("message--reply-target");
  }

  updateReplyPreview();
  updateMessageControls();
  if (replyTargetElement) {
    ensureReplyTargetVisible(replyTargetElement);
  }

  const platformLabel = formatPlatformName(target.platform);
  const username = normalizeUsername(target.username || target.user || "");
  if (!hasAccount(target.platform)) {
    setStatus(
      `Link your ${platformLabel} account to reply${username ? ` to @${username}` : ""}.`
    );
  }

  if (platformSelect && !platformSelect.disabled) {
    if (selectionValue) {
      platformSelect.value = selectionValue;
      preferredSendPlatform = selectionValue;
      applySendButtonStyle(selectionValue);
    }
  }

  if (messageInput && !messageInput.disabled) {
    messageInput.focus();
  }
}

function startReplyFromElement(messageEl) {
  if (!(messageEl instanceof HTMLElement)) {
    return;
  }
  const messageId = messageEl.dataset.messageId;
  const platform = messageEl.dataset.platform || "";
  if (!messageId || !platform) {
    setStatus("This message cannot be replied to.");
    return;
  }
  const channel = messageEl.dataset.channel || "";
  const username = messageEl.dataset.username || "";
  const messageText = messageEl.dataset.rawMessage || "";
  const userId = messageEl.dataset.userId || "";
  let emotes;
  const emotesRaw = messageEl.dataset.emotes;
  if (emotesRaw) {
    try {
      const parsed = JSON.parse(emotesRaw);
      if (Array.isArray(parsed) && parsed.length) {
        emotes = parsed;
      }
    } catch (err) {
      console.warn("Failed to parse emote metadata for reply target", err);
    }
  }
  setReplyTarget(
    {
      platform,
      channel,
      messageId,
      username,
      user: username,
      message: messageText,
      userId,
      emotes,
    },
    messageEl,
  );
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
  if (platformSelect.disabled) {
    platformSelect.value = "";
  } else {
    const availableValues = options.map((option) => option.value);
    let nextValue = "";
    if (
      replyTarget &&
      replyTarget.optionValue &&
      availableValues.includes(replyTarget.optionValue)
    ) {
      nextValue = replyTarget.optionValue;
    } else if (preferredSendPlatform && availableValues.includes(preferredSendPlatform)) {
      nextValue = preferredSendPlatform;
    } else if (availableValues.length) {
      nextValue = availableValues[0];
      preferredSendPlatform = nextValue;
    }
    if (nextValue) {
      platformSelect.value = nextValue;
    }
  }
  const canSend = connectionReady && options.length > 0;
  messageInput.disabled = !canSend;
  sendButton.disabled = !canSend;
  const selectedPlatform = !platformSelect.disabled ? platformSelect.value : "";
  if (!replyTarget && selectedPlatform) {
    preferredSendPlatform = selectedPlatform;
  }
  applySendButtonStyle(selectedPlatform);

  if (!canSend) {
    messageInput.value = "";
  }

  if (!connectionReady) {
    messageInput.placeholder = "Connect to a chat to send messages";
  } else if (!options.length) {
    messageInput.placeholder = replyTarget
      ? "Link your account to reply"
      : "Link your account to send messages";
  } else if (replyTarget) {
    const replyName = normalizeUsername(replyTarget.username || replyTarget.user || "");
    const channelLabel =
      replyTarget.channel && typeof replyTarget.channel === "string" && replyTarget.channel
        ? ` in #${replyTarget.channel}`
        : "";
    messageInput.placeholder = replyName
      ? `Reply to @${replyName}${channelLabel}...`
      : `Reply to this message${channelLabel}...`;
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
  updatePauseBannerOffset();
  if (!paused) {
    return;
  }
  const unreadCount = unreadBufferedCount;
  if (chatPauseLabel) {
    chatPauseLabel.textContent = "Chat Paused Due to Scroll";
  }
  const hasUnread = unreadCount > 0;
  chatResumeButton.disabled = false;
  if (!hasUnread) {
    chatResumeButton.textContent = "\u2193 Back to bottom";
  } else if (unreadCount === 1) {
    chatResumeButton.textContent = "Show 1 new message";
  } else if (unreadCount >= bufferedMessageLimit) {
    chatResumeButton.textContent = `Show ${bufferedMessageLimit}+ new messages`;
  } else {
    chatResumeButton.textContent = `Show ${unreadCount} new messages`;
  }
}

function updatePauseBannerOffset() {
  if (!chatPauseBanner) {
    return;
  }
  const fullscreenActive =
    chatArea &&
    (document.fullscreenElement === chatArea || document.webkitFullscreenElement === chatArea);
  const baseOffset = chatInputOffset || 0;
  const fullscreenOffset = fullscreenActive ? 10 : 0;
  let bottom = 18 + baseOffset + fullscreenOffset;
  if (replyPreview && !replyPreview.classList.contains("hidden")) {
    const previewHeight = replyPreview.offsetHeight || 0;
    const spacerHeight =
      replyPreviewSpacer && !replyPreviewSpacer.classList.contains("hidden")
        ? replyPreviewSpacer.offsetHeight || 0
        : 0;
    // Offset banner by preview height plus spacer and border separation.
    bottom = Math.max(bottom, baseOffset + fullscreenOffset + previewHeight + spacerHeight + 24);
  }
  chatPauseBanner.style.bottom = `${bottom}px`;
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
  clearReplyTarget({ updateControls: false });
  if (chatEl) {
    const children = Array.from(chatEl.children);
    children.forEach((child) => {
      if (
        (replyPreview && child === replyPreview) ||
        (replyPreviewSpacer && child === replyPreviewSpacer)
      ) {
        return;
      }
      chatEl.removeChild(child);
    });
  }
  bufferedMessages.length = 0;
  unreadBufferedCount = 0;
  pausedForScroll = false;
  updatePauseBanner();
  scrollToBottom();
  if (resetPersisted) {
    clearPersistedMessages();
    savePersistedState();
  }
  updateMessageControls();
}

function hideModerationMenu() {
  moderationMenuTarget = null;
  moderationMenuAnchor = null;
  moderationMenu.style.visibility = "";
  moderationMenu.classList.add("hidden");
  moderationMenu.innerHTML = "";
  // Reset position flag so menu can be repositioned next time
  moderationMenu.dataset.positionFixed = "false";
}

function positionModerationMenu() {
  if (!moderationMenuAnchor || moderationMenu.classList.contains("hidden")) {
    return;
  }
  if (!document.body.contains(moderationMenuAnchor)) {
    hideModerationMenu();
    return;
  }
  
  // Only reposition if we don't have a stored position or if the menu is hidden
  if (moderationMenu.dataset.positionFixed === "true") {
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
  
  // Mark position as fixed so it won't move with new messages
  moderationMenu.dataset.positionFixed = "true";
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
  
  // Reset position flag for new menu
  moderationMenu.dataset.positionFixed = "false";

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
  const channelLabel =
    metadata.channel && typeof metadata.channel === "string" && metadata.channel
      ? `#${metadata.channel}`
      : platformLabel;
  title.textContent = `${channelLabel} • ${metadata.username}`;
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
  const configured = currentChannels[normalizedPlatform] || [];
  const normalizedChannel =
    typeof targetMeta.channel === "string"
      ? normalizeChannelSlug(normalizedPlatform, targetMeta.channel)
      : "";
  const channel =
    normalizedChannel ||
    (Array.isArray(configured) && configured.length ? configured[0] : "");
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
    const response = await fetch("/chat/moderate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const errorBody = await response.json().catch(() => null);
      const fallbackDetail = response.statusText || "Unknown error";
      const detailSource =
        errorBody && typeof errorBody === "object" && errorBody !== null
          ? Object.prototype.hasOwnProperty.call(errorBody, "detail")
            ? errorBody.detail
            : errorBody
          : errorBody;
      const detailMessage = extractApiErrorMessage(detailSource, fallbackDetail);
      setStatus(detailMessage, { type: "error" });
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
    setStatus(`Network error while sending ${decoratedAction} request.`, { type: "error" });
  }
}

function openModerationMenuForElement(usernameEl) {
  if (!(usernameEl instanceof HTMLElement)) {
    return;
  }
  const platform = usernameEl.dataset.platform;
  const username = usernameEl.dataset.username || usernameEl.textContent || "";
  const userId = usernameEl.dataset.userId || "";
  const channel = usernameEl.dataset.channel || "";
  if (!platform || !username) {
    return;
  }
  showModerationMenu(usernameEl, { platform, username, userId, channel });
}

chatEl.addEventListener("click", (event) => {
  const replyButton = event.target instanceof HTMLElement ? event.target.closest(".reply-button") : null;
  if (replyButton) {
    event.preventDefault();
    event.stopPropagation();
    const messageEl = replyButton.closest(".message");
    if (messageEl) {
      startReplyFromElement(messageEl);
    }
    hideModerationMenu();
    return;
  }

  const target = event.target instanceof HTMLElement ? event.target.closest(".username") : null;
  if (target) {
    event.preventDefault();
    event.stopPropagation();
    openModerationMenuForElement(target);
    return;
  }

  const messageEl = event.target instanceof HTMLElement ? event.target.closest(".message") : null;
  if (messageEl && messageEl.dataset.messageId) {
    const interactive = event.target instanceof HTMLElement
      ? event.target.closest("a, button, input, textarea, select, [role='button']")
      : null;
    if (interactive) {
      hideModerationMenu();
      return;
    }
    const replyRegion = event.target instanceof HTMLElement
      ? event.target.closest(".content")
      : null;
    if (!replyRegion || replyRegion.closest(".message") !== messageEl) {
      hideModerationMenu();
      return;
    }
    event.preventDefault();
    startReplyFromElement(messageEl);
    hideModerationMenu();
    return;
  }

  hideModerationMenu();
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
updatePauseBannerOffset();
window.addEventListener("resize", positionModerationMenu);
window.addEventListener("resize", updatePauseBannerOffset);

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
    preferredSendPlatform = platformSelect.value || "";
    applySendButtonStyle(platformSelect.value);
  }
});

if (replyCancelButton) {
  replyCancelButton.addEventListener("click", () => {
    if (replyTarget) {
      clearReplyTarget();
    } else {
      clearReplyTarget();
    }
  });
}

function createMessageElement(payload) {
  const data = payload || {};
  const wrapper = document.createElement("div");
  wrapper.classList.add("message");
  if (data.platform) {
    wrapper.dataset.platform = data.platform;
  } else {
    delete wrapper.dataset.platform;
  }
  if (data.channel) {
    wrapper.dataset.channel = String(data.channel);
  } else {
    delete wrapper.dataset.channel;
  }
  if (data.id != null) {
    wrapper.dataset.messageId = String(data.id);
  } else {
    delete wrapper.dataset.messageId;
  }
  if (data.user != null) {
    wrapper.dataset.username = String(data.user);
  } else {
    delete wrapper.dataset.username;
  }
  if (data.user_id != null) {
    wrapper.dataset.userId = String(data.user_id);
  } else {
    delete wrapper.dataset.userId;
  }
  if (data.message != null) {
    wrapper.dataset.rawMessage = String(data.message);
  } else {
    delete wrapper.dataset.rawMessage;
  }
  if (Array.isArray(data.emotes) && data.emotes.length) {
    try {
      wrapper.dataset.emotes = JSON.stringify(data.emotes);
    } catch (err) {
      console.warn("Failed to serialize emote metadata", err);
      delete wrapper.dataset.emotes;
    }
  } else {
    delete wrapper.dataset.emotes;
  }

  if (data.type === "chat") {
    const meta = document.createElement("span");
    meta.classList.add("meta");

    if (data.reply && (data.reply.user || data.reply.message)) {
      const replyContext = document.createElement("div");
      replyContext.classList.add("reply-context");

      const replyIcon = document.createElement("span");
      replyIcon.classList.add("reply-context__icon");
      replyIcon.textContent = "↪";
      replyContext.appendChild(replyIcon);

      const replyBody = document.createElement("span");
      replyBody.classList.add("reply-context__body");

      const replyLabel = document.createElement("span");
      replyLabel.classList.add("reply-context__label");
      const parentUser = normalizeUsername(data.reply.user || "");
      if (parentUser) {
        replyLabel.appendChild(document.createTextNode("Replying to "));
        const mention = document.createElement("span");
        mention.classList.add("mention");
        mention.textContent = `@${parentUser}`;
        if (isSelfMention(parentUser)) {
          mention.classList.add("mention--self");
        }
        replyLabel.appendChild(mention);
        replyLabel.appendChild(document.createTextNode(":"));
      } else {
        replyLabel.textContent = "Replying:";
      }
      replyBody.appendChild(replyLabel);

      if (data.reply && data.reply.message) {
        const snippetHtml = renderReplySnippetContent(
          data.reply,
          data.platform || ""
        );
        if (snippetHtml) {
          replyBody.appendChild(document.createTextNode(" "));
          const replySnippet = document.createElement("span");
          replySnippet.classList.add("reply-context__snippet");
          replySnippet.innerHTML = snippetHtml;
          replyBody.appendChild(replySnippet);
        }
      }

      replyContext.appendChild(replyBody);
      meta.appendChild(replyContext);
    }

    const badgeRow = document.createElement("span");
    badgeRow.classList.add("badges");

    if (
      data.channel_profile_image &&
      typeof data.channel_profile_image === "string" &&
      data.channel_profile_image.trim()
    ) {
      const avatar = document.createElement("img");
      avatar.classList.add("badge", "channel-avatar");
      avatar.src = data.channel_profile_image;
      avatar.alt = data.channel_display_name || data.channel || "channel avatar";
      avatar.title = avatar.alt;
      avatar.loading = "lazy";
      avatar.decoding = "async";
      avatar.referrerPolicy = "no-referrer";
      badgeRow.appendChild(avatar);
    }

    if (data.platform === "twitch" || data.platform === "kick") {
      const platformBadge = document.createElement("span");
      platformBadge.classList.add("platform-icon", data.platform);
      badgeRow.appendChild(platformBadge);
    }

    if (Array.isArray(data.badges) && data.badges.length) {
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
    if (data.channel) {
      username.dataset.channel = data.channel;
    } else {
      delete username.dataset.channel;
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

    const identityRow = document.createElement("span");
    identityRow.classList.add("identity");
    if (badgeRow.childElementCount) {
      identityRow.appendChild(badgeRow);
    }
    identityRow.appendChild(nameGroup);

    meta.appendChild(identityRow);

    const text = document.createElement("span");
    text.classList.add("content");
    text.innerHTML = renderMessageContent(data);

    meta.appendChild(text);

    wrapper.appendChild(meta);

    if (wrapper.dataset.messageId) {
      const replyButtonEl = document.createElement("button");
      replyButtonEl.type = "button";
      replyButtonEl.classList.add("reply-button");
      replyButtonEl.setAttribute(
        "aria-label",
        data.user ? `Reply to ${data.user}` : "Reply to message",
      );
      replyButtonEl.title = "Reply to message";
      replyButtonEl.textContent = "⤴";
      wrapper.appendChild(replyButtonEl);
    }
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
  let messageCount = 0;
  const children = Array.from(chatEl.children);
  children.forEach((child) => {
    if (
      (replyPreview && child === replyPreview) ||
      (replyPreviewSpacer && child === replyPreviewSpacer)
    ) {
      return;
    }
    messageCount += 1;
  });
  if (messageCount <= maxMessages) {
    return;
  }
  for (const child of children) {
    if (
      (replyPreview && child === replyPreview) ||
      (replyPreviewSpacer && child === replyPreviewSpacer)
    ) {
      continue;
    }
    if (messageCount <= maxMessages) {
      break;
    }
    if (
      moderationMenuAnchor &&
      child instanceof HTMLElement &&
      child.contains(moderationMenuAnchor)
    ) {
      hideModerationMenu();
    }
    if (replyTargetElement && child === replyTargetElement) {
      replyTargetElement.classList.remove("message--reply-target");
      replyTargetElement = null;
    }
    chatEl.removeChild(child);
    messageCount -= 1;
  }
}

function addMessageToDom(payload) {
  if (!chatEl) {
    return;
  }
  const element = createMessageElement(payload);
  if (replyPreviewSpacer && replyPreviewSpacer.parentElement === chatEl) {
    chatEl.insertBefore(element, replyPreviewSpacer);
  } else if (replyPreview && replyPreview.parentElement === chatEl) {
    chatEl.insertBefore(element, replyPreview);
  } else {
    chatEl.appendChild(element);
  }
  enforceMessageLimit();
  if (
    replyTarget &&
    element.dataset &&
    element.dataset.messageId === replyTarget.messageId &&
    element.dataset.platform === replyTarget.platform &&
    (replyTarget.channel
      ? element.dataset.channel === replyTarget.channel
      : true)
  ) {
    if (replyTargetElement && replyTargetElement !== element) {
      replyTargetElement.classList.remove("message--reply-target");
    }
    replyTargetElement = element;
    replyTargetElement.classList.add("message--reply-target");
  }
  if (!hydratingMessages) {
    recordMessageForPersistence(payload);
  }
  positionModerationMenu();
}

function appendMessage(payload) {
  if (!payload) {
    return;
  }
  updatePlatformConnectionState(payload);
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

function mentionKey(value) {
  if (value == null) {
    return "";
  }
  const normalized = normalizeUsername(String(value));
  if (!normalized) {
    return "";
  }
  return normalized.toLowerCase();
}

function getSelfMentionKeys() {
  const keys = new Set();
  if (!authState || !authState.authenticated) {
    return keys;
  }
  const push = (value) => {
    const key = mentionKey(value);
    if (key) {
      keys.add(key);
    }
  };
  if (authState.user && typeof authState.user === "object") {
    ["display_name", "displayName", "username", "login", "name"].forEach((field) => {
      if (Object.prototype.hasOwnProperty.call(authState.user, field)) {
        push(authState.user[field]);
      }
    });
  }
  if (Array.isArray(authState.accounts)) {
    authState.accounts.forEach((account) => {
      if (!account || typeof account !== "object") {
        return;
      }
      ["display_name", "displayName", "username", "login", "name"].forEach((field) => {
        if (Object.prototype.hasOwnProperty.call(account, field)) {
          push(account[field]);
        }
      });
    });
  }
  return keys;
}

function formatTextWithMentions(text, mentionKeys) {
  const rawText = typeof text === "string" ? text : String(text ?? "");
  if (!rawText) {
    return "";
  }
  const targets = mentionKeys || getSelfMentionKeys();
  if (!targets.size) {
    return escapeHtml(rawText);
  }
  const mentionPattern = /@([A-Za-z0-9_]+)/g;
  let result = "";
  let lastIndex = 0;
  let match;
  while ((match = mentionPattern.exec(rawText)) !== null) {
    const [fullMatch, username] = match;
    const start = match.index;
    if (start > lastIndex) {
      result += escapeHtml(rawText.slice(lastIndex, start));
    }
    if (targets.has(mentionKey(username))) {
      result += `<span class="mention mention--self">${escapeHtml(fullMatch)}</span>`;
    } else {
      result += escapeHtml(fullMatch);
    }
    lastIndex = start + fullMatch.length;
  }
  if (lastIndex < rawText.length) {
    result += escapeHtml(rawText.slice(lastIndex));
  }
  return result;
}

function isSelfMention(value) {
  const key = mentionKey(value);
  if (!key) {
    return false;
  }
  return getSelfMentionKeys().has(key);
}

function renderMessageContent(payload) {
  const raw = String(payload.message ?? "");
  const mentionKeys = getSelfMentionKeys();
  if (payload.platform === "kick") {
    const regex = /\[emote:(\d+):([^\]]+)\]/g;
    let match;
    let lastIndex = 0;
    const segments = [];
    while ((match = regex.exec(raw)) !== null) {
      const [token, id, name] = match;
      if (match.index > lastIndex) {
        segments.push(formatTextWithMentions(raw.slice(lastIndex, match.index), mentionKeys));
      }
      const safeName = name.replace(/"/g, "&quot;");
      const src = `https://files.kick.com/emotes/${id}/fullsize`;
      segments.push(
        `<img class="emote" src="${src}" alt="${safeName}" title="${safeName}" />`
      );
      lastIndex = match.index + token.length;
    }
    if (lastIndex < raw.length) {
      segments.push(formatTextWithMentions(raw.slice(lastIndex), mentionKeys));
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
        segments.push(formatTextWithMentions(raw.slice(cursor, start), mentionKeys));
      }
      const safeAlt = (alt || "emote").replace(/"/g, "&quot;");
      segments.push(
        `<img class="emote" src="${url}" alt="${safeAlt}" title="${safeAlt}" />`
      );
      cursor = end + 1;
    });
    if (cursor < raw.length) {
      segments.push(formatTextWithMentions(raw.slice(cursor), mentionKeys));
    }
    if (segments.length) {
      return segments.join("");
    }
  }
  return formatTextWithMentions(raw, mentionKeys);
}

function renderReplySnippetContent(reply, fallbackPlatform) {
  if (!reply || typeof reply.message !== "string" || reply.message.length === 0) {
    return "";
  }
  const platform = reply.platform || fallbackPlatform || "";
  const payload = {
    message: reply.message,
    platform,
  };
  if (platform === "twitch") {
    if (Array.isArray(reply.emotes) && reply.emotes.length) {
      payload.emotes = reply.emotes;
    } else if (chatEl) {
      const targetId =
        typeof reply.message_id === "string"
          ? reply.message_id
          : typeof reply.messageId === "string"
            ? reply.messageId
            : "";
      if (targetId) {
        let existing = null;
        const nodes = chatEl.querySelectorAll(".message");
        for (const node of nodes) {
          if (node instanceof HTMLElement && node.dataset.messageId === targetId) {
            existing = node;
            break;
          }
        }
        if (existing) {
          const emotesRaw = existing.dataset.emotes;
          if (emotesRaw) {
            try {
              const parsed = JSON.parse(emotesRaw);
              if (Array.isArray(parsed) && parsed.length) {
                payload.emotes = parsed;
              }
            } catch (err) {
              console.warn("Failed to reuse emote metadata for reply snippet", err);
            }
          }
        }
      }
    }
  }
  return renderMessageContent(payload);
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
  const twitchChannels = parseChannelList(twitchInput.value || "", "twitch");
  const kickChannels = parseChannelList(kickInput.value || "", "kick");

  if (!twitchChannels.length && !kickChannels.length) {
    setStatus("Enter at least one streamer to start listening.");
    return;
  }

  twitchInput.value = formatChannelList(twitchChannels);
  kickInput.value = formatChannelList(kickChannels);
  setAttemptedChannels("twitch", twitchChannels);
  setAttemptedChannels("kick", kickChannels);
  renderChannelInputDecorations();

  hideModerationMenu();
  const previousTwitch = currentChannels.twitch || [];
  const previousKick = currentChannels.kick || [];
  const channelsChanged =
    !channelArraysEqual(twitchChannels, previousTwitch) ||
    !channelArraysEqual(kickChannels, previousKick);

  if (!preserveMessages && channelsChanged) {
    clearChat({ resetPersisted: true });
  }
  if (!preserveMessages && !channelsChanged && persistenceAvailable) {
    // Ensure persisted state reflects current values without dropping messages
    persistedState.channels.twitch = [...twitchChannels];
    persistedState.channels.kick = [...kickChannels];
    savePersistedState();
  }
  connectionFinalized = false;
  setButtonBusy(connectBtn, true, "Connecting…");
  setButtonBusy(disconnectBtn, false);
  disconnectBtn.disabled = true;

  currentChannels.twitch = [...twitchChannels];
  currentChannels.kick = [...kickChannels];
  if (persistenceAvailable) {
    persistedState.channels.twitch = [...twitchChannels];
    persistedState.channels.kick = [...kickChannels];
    savePersistedState();
  }

  resetPlatformConnectionState();

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
    setStatus("Connection established.", { silent: true });
    enableMessageInput();
    setButtonBusy(disconnectBtn, false);
    disconnectBtn.disabled = false;
    if (persistenceAvailable) {
      persistedState.connected = true;
      persistedState.channels.twitch = [...twitchChannels];
      persistedState.channels.kick = [...kickChannels];
      savePersistedState();
    }
    activeSocket.send(
      JSON.stringify({
        action: "subscribe",
        twitch: twitchChannels,
        kick: kickChannels,
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
    announceDisconnectStatus();
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
    setStatus("WebSocket error encountered.", { type: "error" });
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
  clearReplyTarget();
  await refreshAuthStatus();
});

twitchInput.addEventListener("input", updateMessageControls);
kickInput.addEventListener("input", updateMessageControls);
twitchInput.addEventListener("input", renderChannelInputDecorations);
kickInput.addEventListener("input", renderChannelInputDecorations);

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
  const provided = normalizeColor(payload.color);
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
    announceDisconnectStatus();
    disableMessageInput();
    hideModerationMenu();
    resetConnectState();
    if (persistenceAvailable) {
      persistedState.connected = false;
      savePersistedState();
    }
  }
});

function restoreFromPersistedState() {
  if (!persistenceAvailable) {
    return;
  }
  const channels = persistedState.channels || {};
  const twitchList = parseChannelList(channels.twitch, "twitch");
  const kickList = parseChannelList(channels.kick, "kick");
  if (twitchInput) {
    twitchInput.value = formatChannelList(twitchList);
  }
  if (kickInput) {
    kickInput.value = formatChannelList(kickList);
  }
  currentChannels.twitch = [...twitchList];
  currentChannels.kick = [...kickList];
  renderChannelInputDecorations();

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
    (twitchList.length || kickList.length) &&
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
  const selectionValue = platformSelect.value;
  const message = messageInput.value.trim();

  if (!selectionValue) {
    setStatus("Select a platform to send messages.");
    return;
  }
  if (!message) {
    return;
  }
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    setStatus("WebSocket is not connected.", { type: "error" });
    return;
  }

  const selection = parsePlatformOptionValue(selectionValue);
  if (!selection) {
    setStatus("Invalid platform selection.");
    return;
  }

  preferredSendPlatform = selectionValue;

  const formatTargetLabel = (target) => {
    const platformLabel = formatPlatformName(target.platform);
    return target.channel ? `${platformLabel} (#${target.channel})` : platformLabel;
  };

  const collectTargets = (platform, channelSpec) => {
    const connected = Array.from(connectedChannelSets[platform] || []);
    if (!connected.length) {
      setStatus(`No ${formatPlatformName(platform)} channels are currently connected.`);
      return null;
    }
    if (!channelSpec || channelSpec === "*") {
      return connected.map((channel) => ({ platform, channel }));
    }
    const normalizedChannel = normalizeChannelSlug(platform, channelSpec);
    if (!connected.includes(normalizedChannel)) {
      setStatus(
        `Channel #${normalizedChannel} is not connected on ${formatPlatformName(platform)}.`
      );
      return null;
    }
    return [{ platform, channel: normalizedChannel }];
  };

  let targets;
  if (selection.platform === "both") {
    const twitchTargets = collectTargets("twitch", "*");
    if (!twitchTargets) {
      return;
    }
    const kickTargets = collectTargets("kick", "*");
    if (!kickTargets) {
      return;
    }
    targets = [...twitchTargets, ...kickTargets];
  } else {
    const platformTargets = collectTargets(selection.platform, selection.channel);
    if (!platformTargets) {
      return;
    }
    targets = platformTargets;
  }

  if (!targets.length) {
    setStatus("No connected channels available to send messages.");
    return;
  }

  const seenTargets = new Set();
  targets = targets.filter((target) => {
    const key = `${target.platform}:${target.channel}`;
    if (seenTargets.has(key)) {
      return false;
    }
    seenTargets.add(key);
    return true;
  });

  const sendButtonWasDisabled = sendButton.disabled;

  sendingMessage = true;

  sendButton.disabled = true;
  messageInput.disabled = true;
  platformSelect.disabled = true;

  try {
    const successes = [];
    const failures = [];
    const activeReplyTarget =
      replyTarget && replyTarget.messageId
        ? {
            platform: replyTarget.platform,
            channel: replyTarget.channel || "",
            messageId: replyTarget.messageId,
            userId: replyTarget.userId || "",
            username: replyTarget.username || replyTarget.user || "",
          }
        : null;

    for (const target of targets) {
      try {
        const requestBody = {
          platform: target.platform,
          channel: target.channel,
          message,
        };
        if (
          activeReplyTarget &&
          activeReplyTarget.platform === target.platform &&
          (!activeReplyTarget.channel || activeReplyTarget.channel === target.channel)
        ) {
          requestBody.reply_to = {
            message_id: activeReplyTarget.messageId,
            user_id: activeReplyTarget.userId || undefined,
            username: normalizeUsername(activeReplyTarget.username || "") || undefined,
          };
        }
        const response = await fetch("/chat/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify(requestBody),
        });

        if (!response.ok) {
          const errorBody = await response.json().catch(() => null);
          const fallbackDetail = response.statusText || "Unknown error";
          const detailSource =
            errorBody && typeof errorBody === "object" && errorBody !== null
              ? Object.prototype.hasOwnProperty.call(errorBody, "detail")
                ? errorBody.detail
                : errorBody
              : errorBody;
          const detailMessage = extractApiErrorMessage(detailSource, fallbackDetail);
          failures.push({
            platform: target.platform,
            channel: target.channel,
            detail: detailMessage,
          });
          console.error(
            `Failed to send chat message via ${target.platform}#${target.channel}: ${detailMessage}`
          );
          continue;
        }

        successes.push(target);
      } catch (err) {
        console.error(
          `Network error while sending via ${target.platform}#${target.channel}`,
          err
        );
        failures.push({
          platform: target.platform,
          channel: target.channel,
          detail: "Network error",
        });
      }
    }

    if (failures.length) {
      const failure = failures[0];
      const failureLabel = formatTargetLabel(failure);
      if (successes.length) {
        const successLabel = successes
          .map((target) => formatTargetLabel(target))
          .join(" and ");
        setStatus(
          `Message sent via ${successLabel}, but failed via ${failureLabel}: ${failure.detail}`,
          { type: "error" },
        );
      } else {
        setStatus(`Failed to send via ${failureLabel}: ${failure.detail}`, {
          type: "error",
        });
      }
      return;
    }

    messageInput.value = "";
    clearReplyTarget({ updateControls: false });
  } catch (err) {
    console.error("Failed to send chat message", err);
    setStatus("Network error while sending chat message.", { type: "error" });
  } finally {
    sendingMessage = false;
    updateMessageControls();
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
