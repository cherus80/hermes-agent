(function () {
  const DEFAULT_GATEWAY = "http://127.0.0.1:8765";
  const DEFAULT_SESSION = "default";
  const EXTENSION_VERSION = "0.2.6";
  const MAX_TEXT = 20000;
  const MAX_ELEMENTS = 500;
  const INTERACTIVE_SELECTORS = [
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
  ];
  let lastSnapshotAt = 0;
  let mutationTimer = null;
  let snapshotFailureCount = 0;
  let pauseAutoSnapshotsUntil = 0;
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

  function markVersion() {
    try {
      if (document.documentElement) {
        document.documentElement.setAttribute("data-hermes-browser-operator-version", EXTENSION_VERSION);
      }
    } catch (_err) {
      // Diagnostic marker only.
    }
  }

  function finiteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function safeRect(el) {
    try {
      const rect = el && typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : null;
      return {
        x: Math.round(finiteNumber(rect && (rect.x ?? rect.left))),
        y: Math.round(finiteNumber(rect && (rect.y ?? rect.top))),
        width: Math.max(0, Math.round(finiteNumber(rect && rect.width))),
        height: Math.max(0, Math.round(finiteNumber(rect && rect.height)))
      };
    } catch (_err) {
      return { x: 0, y: 0, width: 0, height: 0 };
    }
  }

  function safeQuerySelectorAll(selectors, root = document) {
    const queryRoot = root && typeof root.querySelectorAll === "function" ? root : null;
    if (!queryRoot) return [];
    const selectorList = Array.isArray(selectors) ? selectors : String(selectors || "").split(",");
    const elements = [];
    const seen = new Set();
    for (const rawSelector of selectorList) {
      const selector = String(rawSelector || "").trim();
      if (!selector) continue;
      try {
        for (const el of Array.from(queryRoot.querySelectorAll(selector) || [])) {
          if (seen.has(el)) continue;
          seen.add(el);
          elements.push(el);
        }
      } catch (_err) {
        // Some pages patch or break DOM querying. Skip the bad selector and keep collecting.
      }
    }
    return elements;
  }

  function safeAttr(el, name) {
    try {
      return el && typeof el.getAttribute === "function" ? el.getAttribute(name) || "" : "";
    } catch (_err) {
      return "";
    }
  }

  function safeClosest(el, selector) {
    try {
      return el && typeof el.closest === "function" ? el.closest(selector) : null;
    } catch (_err) {
      return null;
    }
  }

  function cssEscape(value) {
    if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(String(value));
    return cssString(value);
  }

  function visible(el) {
    if (!el || !(el instanceof Element)) return false;
    let style;
    try {
      style = window.getComputedStyle(el);
    } catch (_err) {
      return false;
    }
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
      return false;
    }
    const rect = safeRect(el);
    return rect.width > 0 && rect.height > 0;
  }

  function textOf(el, limit = 300) {
    if (!el) return "";
    try {
      const text = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
      return text.slice(0, limit);
    } catch (_err) {
      return "";
    }
  }

  function elementLabel(el) {
    try {
      if (!el) return "";
      const parentLabel = safeClosest(el, "label");
      if (parentLabel) return textOf(parentLabel);
      return safeAttr(el, "aria-label") || safeAttr(el, "title") || safeAttr(el, "aria-description") || "";
    } catch (_err) {
      return "";
    }
  }

  function stableSelector(el) {
    try {
      if (!el || !(el instanceof Element)) return "";
      const id = safeAttr(el, "id");
      if (id) return `#${cssEscape(id)}`;
      const aria = safeAttr(el, "aria-label");
      if (aria) return `${el.tagName.toLowerCase()}[aria-label="${cssString(aria)}"]`;
      const name = safeAttr(el, "name");
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
    } catch (_err) {
      return "";
    }
  }

  function cssString(value) {
    return String(value).replace(/\\/g, "\\\\").replace(/"/g, "\\\"");
  }

  function nearText(el) {
    try {
      const parent = safeClosest(el, "section, article, form, div, li") || el.parentElement;
      return textOf(parent, 250);
    } catch (_err) {
      return "";
    }
  }

  function collectElements() {
    const elements = [];
    const seen = new Set();
    for (const el of safeQuerySelectorAll(INTERACTIVE_SELECTORS)) {
      if (elements.length >= MAX_ELEMENTS) break;
      try {
        if (seen.has(el) || !visible(el)) continue;
        seen.add(el);
        const rect = safeRect(el);
        if (rect.width <= 0 || rect.height <= 0) continue;
        const type = safeAttr(el, "type").toLowerCase();
        const isPassword = type === "password";
        elements.push({
          ref: `e${elements.length + 1}`,
          tag: el.tagName.toLowerCase(),
          role: safeAttr(el, "role"),
          type,
          text: isPassword ? "" : textOf(el),
          ariaLabel: safeAttr(el, "aria-label"),
          placeholder: isPassword ? "" : safeAttr(el, "placeholder"),
          label: isPassword ? "" : elementLabel(el),
          name: safeAttr(el, "name"),
          id: safeAttr(el, "id"),
          selector: stableSelector(el),
          href: el instanceof HTMLAnchorElement ? el.href : "",
          visible: true,
          enabled: !el.disabled,
          rect,
          nearText: isPassword ? "" : nearText(el),
          hasValue: !isPassword && "value" in el ? Boolean(el.value) : undefined
        });
      } catch (_err) {
        // One hostile or transient element should not prevent the page snapshot.
      }
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
    try {
      const text = (document.body ? document.body.innerText : "").replace(/\s+/g, " ").trim();
      return text.slice(0, MAX_TEXT);
    } catch (_err) {
      return "";
    }
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
    try {
      const now = Date.now();
      if (!force && pauseAutoSnapshotsUntil && now < pauseAutoSnapshotsUntil) {
        return { ok: true, skipped: true, paused: true };
      }
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
      const response = await postJson("/v1/snapshots", snapshot);
      snapshotFailureCount = 0;
      pauseAutoSnapshotsUntil = 0;
      return response;
    } catch (err) {
      snapshotFailureCount += 1;
      if (!force && snapshotFailureCount >= 3) {
        pauseAutoSnapshotsUntil = Date.now() + 30000;
      }
      return handleAsyncError(err);
    }
  }

  function elementScoreText(el) {
    try {
      return [
        textOf(el, 500),
        safeAttr(el, "aria-label"),
        safeAttr(el, "placeholder"),
        elementLabel(el),
        safeAttr(el, "name"),
        safeAttr(el, "id"),
        nearText(el)
      ].join(" ").toLowerCase();
    } catch (_err) {
      return "";
    }
  }

  function scoreElement(el, target) {
    const label = elementScoreText(el);
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
    let best = null;
    let bestScore = 0;
    for (const el of safeQuerySelectorAll(INTERACTIVE_SELECTORS)) {
      try {
        const score = scoreElement(el, target);
        if (score > bestScore) {
          best = el;
          bestScore = score;
        }
      } catch (_err) {
        // Ignore elements that disappear while we are scoring the page.
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
          element: { score: found.score, label: elementScoreText(found.el).slice(0, 300) }
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
        element: { score: found.score, label: elementScoreText(found.el).slice(0, 300) }
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

  markVersion();
  const observer = new MutationObserver(() => {
    if (Date.now() < pauseAutoSnapshotsUntil) return;
    clearTimeout(mutationTimer);
    mutationTimer = setTimeout(() => quiet(() => sendSnapshot(false)), 1600);
  });
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true, attributes: false });
  }
  window.addEventListener("focus", () => quiet(() => sendSnapshot(false)));
  setInterval(() => quiet(() => sendSnapshot(false)), 7000);
  setInterval(() => quiet(() => pollActions()), 2000);
  setTimeout(() => quiet(() => sendSnapshot(true)), 1000);
})();
