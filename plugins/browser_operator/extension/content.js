(function () {
  const DEFAULT_GATEWAY = "http://127.0.0.1:8765";
  const DEFAULT_SESSION = "default";
  const MAX_TEXT = 20000;
  const MAX_ELEMENTS = 500;
  let lastSnapshotAt = 0;
  let mutationTimer = null;
  let cachedSettings = {
    gatewayUrl: DEFAULT_GATEWAY,
    sessionId: DEFAULT_SESSION,
    token: ""
  };
  let extensionContextInvalidated = false;

  function isExtensionContextInvalidated(err) {
    return String(err && (err.message || err)).includes("Extension context invalidated");
  }

  function extensionContextAvailable() {
    try {
      return (
        !extensionContextInvalidated &&
        typeof chrome !== "undefined" &&
        chrome.runtime &&
        chrome.runtime.id &&
        chrome.storage &&
        chrome.storage.local
      );
    } catch (_err) {
      return false;
    }
  }

  function handleAsyncError(err) {
    if (isExtensionContextInvalidated(err)) {
      extensionContextInvalidated = true;
    }
    return { ok: false, error: String(err && err.message ? err.message : err) };
  }

  function quiet(fn) {
    try {
      return Promise.resolve(fn()).catch(handleAsyncError);
    } catch (err) {
      return Promise.resolve(handleAsyncError(err));
    }
  }

  function visible(el) {
    if (!el || !(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function textOf(el, limit = 300) {
    if (!el) return "";
    const text = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    return text.slice(0, limit);
  }

  function labelFor(el) {
    if (!el) return "";
    const id = el.getAttribute("id");
    if (id) {
      const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (label) return textOf(label);
    }
    const parentLabel = el.closest("label");
    if (parentLabel) return textOf(parentLabel);
    return "";
  }

  function stableSelector(el) {
    if (!el || !(el instanceof Element)) return "";
    const id = el.getAttribute("id");
    if (id) return `#${CSS.escape(id)}`;
    const aria = el.getAttribute("aria-label");
    if (aria) return `${el.tagName.toLowerCase()}[aria-label="${cssString(aria)}"]`;
    const name = el.getAttribute("name");
    if (name) return `${el.tagName.toLowerCase()}[name="${cssString(name)}"]`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 5) {
      const tag = node.tagName.toLowerCase();
      let index = 1;
      let sib = node;
      while ((sib = sib.previousElementSibling)) {
        if (sib.tagName.toLowerCase() === tag) index += 1;
      }
      parts.unshift(`${tag}:nth-of-type(${index})`);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }

  function cssString(value) {
    return String(value).replace(/\\/g, "\\\\").replace(/"/g, "\\\"");
  }

  function nearText(el) {
    const parent = el.closest("section, article, form, div, li") || el.parentElement;
    return textOf(parent, 250);
  }

  function collectElements() {
    const selector = [
      "a[href]",
      "button",
      "input",
      "textarea",
      "select",
      "[role='button']",
      "[role='link']",
      "[role='textbox']",
      "[contenteditable='true']",
      "[tabindex]"
    ].join(",");
    const elements = [];
    const seen = new Set();
    for (const el of document.querySelectorAll(selector)) {
      if (elements.length >= MAX_ELEMENTS) break;
      if (seen.has(el) || !visible(el)) continue;
      seen.add(el);
      const rect = el.getBoundingClientRect();
      const type = (el.getAttribute("type") || "").toLowerCase();
      const isPassword = type === "password";
      elements.push({
        ref: `e${elements.length + 1}`,
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute("role") || "",
        type,
        text: isPassword ? "" : textOf(el),
        ariaLabel: el.getAttribute("aria-label") || "",
        placeholder: isPassword ? "" : (el.getAttribute("placeholder") || ""),
        label: isPassword ? "" : labelFor(el),
        name: el.getAttribute("name") || "",
        id: el.getAttribute("id") || "",
        selector: stableSelector(el),
        href: el instanceof HTMLAnchorElement ? el.href : "",
        visible: true,
        enabled: !el.disabled,
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height)
        },
        nearText: isPassword ? "" : nearText(el),
        hasValue: !isPassword && "value" in el ? Boolean(el.value) : undefined
      });
    }
    return elements;
  }

  function collectAuthSignals(elements) {
    const loginRe = /\b(sign in|log in|login|continue with google|войти|авториз|вход)\b/i;
    const accountRe = /\b(account|profile|avatar|my channel|your channel|аккаунт|профиль|канал)\b/i;
    let loginElements = 0;
    let accountElements = 0;
    let passwordInputs = 0;
    for (const item of elements) {
      const label = [
        item.text,
        item.ariaLabel,
        item.placeholder,
        item.label,
        item.nearText,
        item.id,
        item.name
      ].join(" ");
      if (item.type === "password") passwordInputs += 1;
      if (loginRe.test(label)) loginElements += 1;
      if (accountRe.test(label)) accountElements += 1;
    }
    return { loginElements, accountElements, passwordInputs };
  }

  function visibleText() {
    const text = (document.body ? document.body.innerText : "").replace(/\s+/g, " ").trim();
    return text.slice(0, MAX_TEXT);
  }

  async function settings() {
    if (!extensionContextAvailable()) return cachedSettings;
    try {
      const cfg = await chrome.storage.local.get(cachedSettings);
      cachedSettings = { ...cachedSettings, ...cfg };
    } catch (err) {
      handleAsyncError(err);
    }
    return cachedSettings;
  }

  async function postJson(path, payload) {
    const cfg = await settings();
    const base = (cfg.gatewayUrl || DEFAULT_GATEWAY).replace(/\/+$/, "");
    const headers = { "Content-Type": "application/json" };
    if (cfg.token) headers["X-Hermes-Browser-Token"] = cfg.token;
    const res = await fetch(base + path, {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    });
    return res.json();
  }

  async function getJson(path) {
    const cfg = await settings();
    const base = (cfg.gatewayUrl || DEFAULT_GATEWAY).replace(/\/+$/, "");
    const headers = {};
    if (cfg.token) headers["X-Hermes-Browser-Token"] = cfg.token;
    const res = await fetch(base + path, { headers });
    return res.json();
  }

  async function sendSnapshot(force = false) {
    const now = Date.now();
    if (!force && now - lastSnapshotAt < 2500) return { ok: true, skipped: true };
    lastSnapshotAt = now;
    const cfg = await settings();
    const elements = collectElements();
    const snapshot = {
      sessionId: cfg.sessionId || DEFAULT_SESSION,
      tabId: "active",
      url: location.href,
      title: document.title,
      text: visibleText(),
      elements,
      authSignals: collectAuthSignals(elements),
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
        devicePixelRatio: window.devicePixelRatio || 1
      },
      createdAt: new Date().toISOString()
    };
    try {
      return await postJson("/v1/snapshots", snapshot);
    } catch (err) {
      return { ok: false, error: String(err) };
    }
  }

  function labelForScore(el) {
    return [
      el.innerText || el.textContent || "",
      el.getAttribute("aria-label") || "",
      el.getAttribute("placeholder") || "",
      labelFor(el),
      el.getAttribute("name") || "",
      el.getAttribute("id") || "",
      nearText(el)
    ].join(" ").toLowerCase();
  }

  function scoreElement(el, target) {
    const label = labelForScore(el);
    const words = String(target || "").toLowerCase().match(/[\w-]+/g) || [];
    let score = 0;
    if (target && label.includes(String(target).toLowerCase())) score += 4;
    for (const word of words) {
      if (word.length > 1 && label.includes(word)) score += 1;
    }
    if (!visible(el)) score -= 3;
    if (el.disabled) score -= 1;
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    if (["button", "a", "input", "textarea", "select"].includes(tag) || ["button", "link", "textbox"].includes(role)) {
      score += 0.25;
    }
    return score;
  }

  function resolveElement(target) {
    const selector = [
      "a[href]",
      "button",
      "input",
      "textarea",
      "select",
      "[role='button']",
      "[role='link']",
      "[role='textbox']",
      "[contenteditable='true']",
      "[tabindex]"
    ].join(",");
    let best = null;
    let bestScore = 0;
    for (const el of document.querySelectorAll(selector)) {
      const score = scoreElement(el, target);
      if (score > bestScore) {
        best = el;
        bestScore = score;
      }
    }
    return { el: best, score: bestScore };
  }

  function highlight(el) {
    if (!el) return;
    el.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
    const oldOutline = el.style.outline;
    const oldShadow = el.style.boxShadow;
    el.style.outline = "3px solid #155eef";
    el.style.boxShadow = "0 0 0 6px rgba(21, 94, 239, 0.22)";
    setTimeout(() => {
      el.style.outline = oldOutline;
      el.style.boxShadow = oldShadow;
    }, 4500);
  }

  function setValue(el, value) {
    if (!el) return false;
    if (el.isContentEditable) {
      el.focus();
      el.textContent = value;
    } else if ("value" in el) {
      el.focus();
      el.value = value;
    } else {
      return false;
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function selectValue(el, value) {
    if (!(el instanceof HTMLSelectElement)) return setValue(el, value);
    const needle = String(value || "").toLowerCase();
    for (const option of el.options) {
      if (option.value.toLowerCase() === needle || option.text.toLowerCase().includes(needle)) {
        el.value = option.value;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }
    }
    return false;
  }

  function valueObject(action) {
    if (action && action.value && typeof action.value === "object") return action.value;
    return {};
  }

  function scrollPage(action) {
    const value = valueObject(action);
    const direction = String(value.direction || action.target || "down").toLowerCase();
    const unit = String(value.unit || "pages").toLowerCase();
    const rawAmount = Number(value.amount || action.value || 1);
    const amount = Number.isFinite(rawAmount) && rawAmount > 0 ? rawAmount : 1;
    const pixels = unit === "pixels" ? amount : Math.round(window.innerHeight * 0.85 * amount);

    if (direction === "top") {
      window.scrollTo({ top: 0, left: window.scrollX, behavior: "smooth" });
      return "Scrolled to top.";
    }
    if (direction === "bottom") {
      window.scrollTo({ top: document.documentElement.scrollHeight, left: window.scrollX, behavior: "smooth" });
      return "Scrolled to bottom.";
    }
    window.scrollBy({
      top: direction === "up" ? -pixels : pixels,
      left: 0,
      behavior: "smooth"
    });
    return `Scrolled ${direction} by ${Math.round(pixels)}px.`;
  }

  function confirmNavigation(action, label) {
    if (!action.requiresUserConfirm) return true;
    return window.confirm(`Hermes wants to ${label}.\n\n${action.reason || ""}`);
  }

  async function executeAction(action) {
    const cfg = await settings();
    const actionId = action.id;
    try {
      if (action.type === "navigate") {
        const url = String(action.value || action.target || "");
        if (!url) throw new Error("No URL provided.");
        if (!confirmNavigation(action, `navigate to:\n${url}`)) {
          throw new Error("User cancelled navigation.");
        }
        location.href = url;
        await postJson("/v1/actions/result", {
          actionId,
          sessionId: cfg.sessionId || DEFAULT_SESSION,
          ok: true,
          status: "done",
          message: "Navigation started."
        });
        return;
      }

      if (["back", "forward", "reload"].includes(action.type)) {
        if (!confirmNavigation(action, action.type)) {
          throw new Error(`User cancelled ${action.type}.`);
        }
        await postJson("/v1/actions/result", {
          actionId,
          sessionId: cfg.sessionId || DEFAULT_SESSION,
          ok: true,
          status: "done",
          message: `${action.type} started.`
        });
        if (action.type === "back") history.back();
        if (action.type === "forward") history.forward();
        if (action.type === "reload") location.reload();
        setTimeout(() => quiet(() => sendSnapshot(true)), 1000);
        return;
      }

      if (action.type === "scroll") {
        const message = scrollPage(action);
        await postJson("/v1/actions/result", {
          actionId,
          sessionId: cfg.sessionId || DEFAULT_SESSION,
          ok: true,
          status: "done",
          message
        });
        setTimeout(() => quiet(() => sendSnapshot(true)), 900);
        return;
      }

      if (action.type === "auth_probe") {
        await sendSnapshot(true);
        await postJson("/v1/actions/result", {
          actionId,
          sessionId: cfg.sessionId || DEFAULT_SESSION,
          ok: true,
          status: "done",
          message: "Auth snapshot refreshed."
        });
        return;
      }

      const found = resolveElement(action.target || "");
      if (!found.el || found.score < 1) {
        throw new Error(`Target not found: ${action.target || "(empty)"}`);
      }
      highlight(found.el);

      if (action.type === "highlight") {
        await postJson("/v1/actions/result", {
          actionId,
          sessionId: cfg.sessionId || DEFAULT_SESSION,
          ok: true,
          status: "done",
          message: "Element highlighted.",
          element: { score: found.score, label: labelForScore(found.el).slice(0, 300) }
        });
        return;
      }

      if (action.requiresUserConfirm) {
        const ok = window.confirm(
          `Hermes wants to ${action.type}:\n${action.target || "(target)"}\n\n${action.reason || ""}`
        );
        if (!ok) throw new Error("User cancelled action.");
      }

      let ok = false;
      if (action.type === "fill") ok = setValue(found.el, action.value || "");
      if (action.type === "select") ok = selectValue(found.el, action.value || "");
      if (action.type === "click") {
        found.el.click();
        ok = true;
      }
      if (!ok) throw new Error(`Action failed: ${action.type}`);
      await postJson("/v1/actions/result", {
        actionId,
        sessionId: cfg.sessionId || DEFAULT_SESSION,
        ok: true,
        status: "done",
        message: `${action.type} completed.`,
        element: { score: found.score, label: labelForScore(found.el).slice(0, 300) }
      });
      setTimeout(() => quiet(() => sendSnapshot(true)), 800);
    } catch (err) {
      await postJson("/v1/actions/result", {
        actionId,
        sessionId: cfg.sessionId || DEFAULT_SESSION,
        ok: false,
        status: "failed",
        error: String(err && err.message ? err.message : err)
      });
    }
  }

  async function pollActions() {
    const cfg = await settings();
    const sessionId = encodeURIComponent(cfg.sessionId || DEFAULT_SESSION);
    try {
      const response = await getJson(`/v1/actions/next?sessionId=${sessionId}&tabId=active`);
      if (response && response.ok && response.action) {
        await executeAction(response.action);
      }
    } catch (err) {
      // Bridge may be offline; stay quiet in the user's page.
    }
  }

  if (extensionContextAvailable()) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message && message.type === "hermes:snapshotNow") {
        quiet(() => sendSnapshot(true)).then(sendResponse);
        return true;
      }
      return false;
    });
  }

  const observer = new MutationObserver(() => {
    clearTimeout(mutationTimer);
    mutationTimer = setTimeout(() => quiet(() => sendSnapshot(false)), 1200);
  });
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true, attributes: true });
  }
  window.addEventListener("focus", () => quiet(() => sendSnapshot(false)));
  setInterval(() => quiet(() => sendSnapshot(false)), 7000);
  setInterval(() => quiet(() => pollActions()), 2000);
  setTimeout(() => quiet(() => sendSnapshot(true)), 1000);
})();
