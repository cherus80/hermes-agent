(function () {
  const DEFAULT_GATEWAY = "http://127.0.0.1:8765";
  const DEFAULT_SESSION = "default";
  const EXTENSION_VERSION = "0.3.2";
  const MAX_TEXT = 20000;
  const MAX_ELEMENTS = 500;
  const MAX_REGIONS = 120;
  const MAX_REGION_CONTROLS = 24;
  const INTERACTIVE_SELECTORS = [
    "a[href]",
    "button",
    "input",
    "textarea",
    "select",
    "[role='button']",
    "[role='link']",
    "[role='textbox']",
    "[role='searchbox']",
    "[role='combobox']",
    "[role='checkbox']",
    "[role='radio']",
    "[role='switch']",
    "[role='menuitem']",
    "[role='option']",
    "[role='tab']",
    "[contenteditable='true']",
    "[contenteditable='plaintext-only']",
    "[contenteditable='']",
    "[tabindex]",
    "[onclick]",
    "[aria-label]",
    "[aria-labelledby]",
    "[data-testid]",
    "[data-test-id]",
    "[data-test]",
    "[data-cy]"
  ];
  let lastSnapshotAt = 0;
  let mutationTimer = null;
  let snapshotFailureCount = 0;
  let pauseAutoSnapshotsUntil = 0;
  let tabInfo = null;
  let fallbackTabId = "";
  let cachedSettings = {
    gatewayUrl: DEFAULT_GATEWAY,
    sessionId: DEFAULT_SESSION,
    token: "",
    actionApprovalMode: "confirm"
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

  function fallbackPageTabId() {
    if (fallbackTabId) return fallbackTabId;
    try {
      const key = "hermesBrowserOperatorFallbackTabId";
      fallbackTabId = window.sessionStorage.getItem(key) || "";
      if (!fallbackTabId) {
        fallbackTabId = `page:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 10)}`;
        window.sessionStorage.setItem(key, fallbackTabId);
      }
    } catch (_err) {
      fallbackTabId = `page:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 10)}`;
    }
    return fallbackTabId;
  }

  async function getTabInfo(refresh = false) {
    const fallback = {
      tabId: fallbackPageTabId(),
      title: document.title || "",
      url: location.href,
      active: document.visibilityState === "visible",
      extensionVersion: EXTENSION_VERSION
    };
    if (!refresh && tabInfo && tabInfo.tabId) return tabInfo;
    if (!extensionContextAvailable() || !chrome.runtime || typeof chrome.runtime.sendMessage !== "function") {
      tabInfo = fallback;
      return tabInfo;
    }
    try {
      const response = await chrome.runtime.sendMessage({ type: "hermes:getTabInfo" });
      if (response && response.ok && response.tab && response.tab.tabId) {
        tabInfo = {
          ...response.tab,
          title: response.tab.title || document.title || "",
          url: response.tab.url || location.href,
          extensionVersion: EXTENSION_VERSION
        };
        return tabInfo;
      }
    } catch (err) {
      handleAsyncError(err);
    }
    tabInfo = fallback;
    return tabInfo;
  }

  async function focusCurrentTab() {
    if (!extensionContextAvailable() || !chrome.runtime || typeof chrome.runtime.sendMessage !== "function") {
      return { ok: false, error: "Extension background is unavailable." };
    }
    try {
      return await chrome.runtime.sendMessage({ type: "hermes:focusSelf" });
    } catch (err) {
      handleAsyncError(err);
      return { ok: false, error: String(err && err.message ? err.message : err) };
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
    const roots = [queryRoot];
    try {
      const start = queryRoot.nodeType === Node.DOCUMENT_NODE ? document.documentElement : queryRoot;
      if (start && typeof document.createTreeWalker === "function") {
        const walker = document.createTreeWalker(start, NodeFilter.SHOW_ELEMENT);
        let node = walker.currentNode;
        while (node && roots.length < 80) {
          if (node.shadowRoot && typeof node.shadowRoot.querySelectorAll === "function") {
            roots.push(node.shadowRoot);
          }
          node = walker.nextNode();
        }
      }
    } catch (_err) {
      // Shadow DOM traversal is best-effort; ordinary DOM querying still works.
    }
    for (const searchRoot of roots) {
      for (const rawSelector of selectorList) {
        const selector = String(rawSelector || "").trim();
        if (!selector) continue;
        try {
          for (const el of Array.from(searchRoot.querySelectorAll(selector) || [])) {
            if (seen.has(el)) continue;
            seen.add(el);
            elements.push(el);
          }
        } catch (_err) {
          // Some pages patch or break DOM querying. Skip the bad selector and keep collecting.
        }
      }
    }
    return elements;
  }

  function querySelectorDeep(selector, root = document) {
    for (const el of safeQuerySelectorAll(selector, root)) {
      try {
        if (visible(el)) return el;
      } catch (_err) {
        // Continue to the next deep candidate.
      }
    }
    return null;
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

  function textByIdList(ids) {
    const parts = [];
    for (const id of String(ids || "").split(/\s+/).filter(Boolean)) {
      try {
        const node = document.getElementById(id);
        const text = textOf(node, 220);
        if (text) parts.push(text);
      } catch (_err) {
        // Ignore broken or transient aria references.
      }
    }
    return parts.join(" ").trim();
  }

  function dataTestId(el) {
    return (
      safeAttr(el, "data-testid") ||
      safeAttr(el, "data-test-id") ||
      safeAttr(el, "data-test") ||
      safeAttr(el, "data-cy")
    );
  }

  function associatedLabelText(el) {
    try {
      const id = safeAttr(el, "id");
      if (id) {
        const labels = Array.from(document.querySelectorAll(`label[for="${cssString(id)}"]`) || []);
        const text = labels.map((label) => textOf(label, 220)).filter(Boolean).join(" ");
        if (text) return text;
      }
      const parentLabel = safeClosest(el, "label");
      if (parentLabel) return textOf(parentLabel, 220);
    } catch (_err) {
      // Fall through to the caller's other label sources.
    }
    return "";
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
      return [
        associatedLabelText(el),
        safeAttr(el, "aria-label"),
        textByIdList(safeAttr(el, "aria-labelledby")),
        safeAttr(el, "title"),
        safeAttr(el, "aria-description"),
        textByIdList(safeAttr(el, "aria-describedby")),
        safeAttr(el, "alt"),
        dataTestId(el)
      ].filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
    } catch (_err) {
      return "";
    }
  }

  function isButtonLike(el) {
    const tag = el && el.tagName ? el.tagName.toLowerCase() : "";
    const role = safeAttr(el, "role").toLowerCase();
    const type = safeAttr(el, "type").toLowerCase();
    return (
      tag === "button" ||
      tag === "a" ||
      ["button", "link", "menuitem", "option", "tab", "switch", "checkbox", "radio"].includes(role) ||
      ["button", "submit", "reset", "image"].includes(type) ||
      Boolean(safeAttr(el, "onclick"))
    );
  }

  function isTextEntry(el) {
    const tag = el && el.tagName ? el.tagName.toLowerCase() : "";
    const role = safeAttr(el, "role").toLowerCase();
    const type = safeAttr(el, "type").toLowerCase();
    return (
      tag === "textarea" ||
      tag === "select" ||
      el.isContentEditable ||
      ["textbox", "searchbox", "combobox"].includes(role) ||
      (tag === "input" && !["button", "submit", "reset", "checkbox", "radio", "file", "hidden", "image"].includes(type))
    );
  }

  function isDisabled(el) {
    try {
      return Boolean(el.disabled) || safeAttr(el, "aria-disabled").toLowerCase() === "true";
    } catch (_err) {
      return false;
    }
  }

  function buttonValueText(el) {
    const tag = el && el.tagName ? el.tagName.toLowerCase() : "";
    const type = safeAttr(el, "type").toLowerCase();
    try {
      if (tag === "input" && ["button", "submit", "reset"].includes(type)) {
        return String(el.value || "").slice(0, 220);
      }
    } catch (_err) {
      return "";
    }
    return "";
  }

  function stableSelector(el) {
    try {
      if (!el || !(el instanceof Element)) return "";
      const id = safeAttr(el, "id");
      if (id) return `#${cssEscape(id)}`;
      for (const attr of ["data-testid", "data-test-id", "data-test", "data-cy"]) {
        const testId = safeAttr(el, attr);
        if (testId) return `${el.tagName.toLowerCase()}[${attr}="${cssString(testId)}"]`;
      }
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
      const parent = safeClosest(el, "section, article, form, [role='dialog'], [role='group'], [role='listitem'], div, li") || el.parentElement;
      const before = el.previousElementSibling ? textOf(el.previousElementSibling, 120) : "";
      const after = el.nextElementSibling ? textOf(el.nextElementSibling, 120) : "";
      return [before, textOf(parent, 360), after].filter(Boolean).join(" ").slice(0, 500);
    } catch (_err) {
      return "";
    }
  }

  function elementSummary(el, ref = "") {
    const type = safeAttr(el, "type").toLowerCase();
    const isPassword = type === "password";
    const text = isPassword ? "" : textOf(el);
    const label = isPassword ? "" : elementLabel(el);
    const role = safeAttr(el, "role");
    const tag = el.tagName.toLowerCase();
    return {
      ref,
      tag,
      role,
      type,
      text: text || buttonValueText(el),
      ariaLabel: isPassword ? "" : safeAttr(el, "aria-label"),
      ariaDescription: isPassword ? "" : safeAttr(el, "aria-description"),
      labelledBy: isPassword ? "" : textByIdList(safeAttr(el, "aria-labelledby")),
      describedBy: isPassword ? "" : textByIdList(safeAttr(el, "aria-describedby")),
      placeholder: isPassword ? "" : safeAttr(el, "placeholder"),
      label,
      name: safeAttr(el, "name"),
      id: safeAttr(el, "id"),
      testId: dataTestId(el),
      autocomplete: isPassword ? "" : safeAttr(el, "autocomplete"),
      inputMode: isPassword ? "" : safeAttr(el, "inputmode"),
      selector: stableSelector(el),
      href: el instanceof HTMLAnchorElement ? el.href : "",
      visible: true,
      enabled: !isDisabled(el),
      buttonLike: isButtonLike(el),
      textEntry: isTextEntry(el),
      rect: safeRect(el),
      nearText: isPassword ? "" : nearText(el),
      hasValue: !isPassword && "value" in el ? Boolean(el.value) : undefined
    };
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
        elements.push(elementSummary(el, `e${elements.length + 1}`));
      } catch (_err) {
        // One hostile or transient element should not prevent the page snapshot.
      }
    }
    return elements;
  }

  function collectLinks(root) {
    const links = [];
    const seen = new Set();
    for (const link of safeQuerySelectorAll("a[href]", root)) {
      try {
        const href = link.href || safeAttr(link, "href");
        if (!href || seen.has(href)) continue;
        seen.add(href);
        links.push({ text: textOf(link, 180), href });
        if (links.length >= 20) break;
      } catch (_err) {
        // Ignore transient link nodes.
      }
    }
    return links;
  }

  function collectNumbers(text) {
    const matches = String(text || "").match(/(?:^|\s)(?:\d+[\d.,]*\s*(?:K|M|тыс\.?|млн\.?)?|\d+)(?=\s|$)/gi) || [];
    return Array.from(new Set(matches.map((item) => item.trim()).filter(Boolean))).slice(0, 40);
  }

  function collectRegionControls(root) {
    const controls = [];
    const seen = new Set();
    for (const el of safeQuerySelectorAll(INTERACTIVE_SELECTORS, root)) {
      if (controls.length >= MAX_REGION_CONTROLS) break;
      try {
        if (seen.has(el) || !visible(el)) continue;
        seen.add(el);
        const item = elementSummary(el, `c${controls.length + 1}`);
        controls.push({
          ref: item.ref,
          tag: item.tag,
          role: item.role,
          type: item.type,
          text: item.text,
          label: item.label,
          ariaLabel: item.ariaLabel,
          placeholder: item.placeholder,
          testId: item.testId,
          selector: item.selector,
          buttonLike: item.buttonLike,
          textEntry: item.textEntry,
          enabled: item.enabled,
          rect: item.rect
        });
      } catch (_err) {
        // Ignore controls that disappear during SPA re-render.
      }
    }
    return controls;
  }

  function collectRegions() {
    const selectors = [
      "article",
      "[role='article']",
      "[data-testid='cellInnerDiv']",
      "[data-pressable-container='true']",
      "form",
      "[role='dialog']",
      "[role='menu']",
      "[role='listbox']",
      "[role='feed'] > *",
      "section",
      "li"
    ];
    const regions = [];
    const seen = new Set();
    for (const el of safeQuerySelectorAll(selectors)) {
      if (regions.length >= MAX_REGIONS) break;
      try {
        if (seen.has(el) || !visible(el)) continue;
        seen.add(el);
        const rect = safeRect(el);
        const text = textOf(el, 2000);
        if (text.length < 20) continue;
        if (rect.width <= 0 || rect.height <= 0) continue;
        if (rect.height > window.innerHeight * 2.5 && safeAttr(el, "role") !== "article") continue;
        regions.push({
          ref: `r${regions.length + 1}`,
          tag: el.tagName.toLowerCase(),
          role: safeAttr(el, "role"),
          selector: stableSelector(el),
          text,
          ariaLabel: safeAttr(el, "aria-label"),
          label: elementLabel(el),
          rect,
          links: collectLinks(el),
          numbers: collectNumbers(text),
          controls: collectRegionControls(el)
        });
      } catch (_err) {
        // Keep the snapshot alive even on highly dynamic feeds.
      }
    }
    return regions;
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
      cachedSettings.actionApprovalMode = cachedSettings.actionApprovalMode === "auto" ? "auto" : "confirm";
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
      const currentTab = await getTabInfo(true);
      const elements = collectElements();
      const regions = collectRegions();
      const snapshot = {
        sessionId: cfg.sessionId || DEFAULT_SESSION,
        tabId: currentTab.tabId || fallbackPageTabId(),
        browserTab: currentTab,
        extensionVersion: EXTENSION_VERSION,
        operatorSettings: {
          actionApprovalMode: cfg.actionApprovalMode === "auto" ? "auto" : "confirm"
        },
        url: location.href,
        title: document.title,
        text: visibleText(),
        elements,
        regions,
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
        textByIdList(safeAttr(el, "aria-labelledby")),
        textByIdList(safeAttr(el, "aria-describedby")),
        safeAttr(el, "name"),
        safeAttr(el, "id"),
        safeAttr(el, "role"),
        safeAttr(el, "type"),
        safeAttr(el, "autocomplete"),
        dataTestId(el),
        buttonValueText(el),
        nearText(el)
      ].join(" ").toLowerCase();
    } catch (_err) {
      return "";
    }
  }

  function targetWords(target) {
    const base = String(target || "").toLowerCase();
    const words = base.match(/[\p{L}\p{N}_-]+/gu) || [];
    const synonyms = {
      "комментарий": ["comment", "reply", "ответ", "textbox", "textarea", "editor"],
      "коммент": ["comment", "reply", "ответ", "textbox", "textarea", "editor"],
      "comment": ["комментарий", "reply", "ответ", "textbox", "textarea", "editor"],
      "ответ": ["reply", "comment", "комментарий", "textbox", "textarea", "editor"],
      "поле": ["input", "field", "textbox", "textarea", "editor"],
      "field": ["поле", "input", "textbox", "textarea", "editor"],
      "кнопка": ["button", "submit", "click"],
      "button": ["кнопка", "submit", "click"],
      "опубликовать": ["publish", "post", "submit", "share", "отправить", "разместить"],
      "публикация": ["publish", "post", "submit", "share", "отправить", "разместить"],
      "publish": ["опубликовать", "post", "submit", "share", "отправить"],
      "send": ["отправить", "submit", "publish", "post"],
      "отправить": ["send", "submit", "publish", "post"],
      "поиск": ["search", "find"],
      "search": ["поиск", "find"],
      "название": ["title", "name"],
      "title": ["название", "name"],
      "описание": ["description", "caption", "bio"],
      "description": ["описание", "caption"],
      "загрузить": ["upload", "file", "attach"],
      "upload": ["загрузить", "file", "attach"]
    };
    const expanded = new Set(words);
    for (const word of words) {
      for (const synonym of synonyms[word] || []) expanded.add(synonym);
    }
    return Array.from(expanded);
  }

  function scoreElement(el, target) {
    const label = elementScoreText(el);
    const targetText = String(target || "").toLowerCase();
    const words = targetWords(target);
    let score = 0;
    if (target && label.includes(targetText)) score += 4;
    for (const word of words) {
      if (word.length > 1 && label.includes(word)) score += 1;
    }
    if (!visible(el)) score -= 3;
    if (isDisabled(el)) score -= 1;
    if (isButtonLike(el) || isTextEntry(el)) {
      score += 0.25;
    }
    if (/(comment|reply|коммент|ответ|field|поле|text|текст|title|название|description|описание|search|поиск)/i.test(targetText) && isTextEntry(el)) {
      score += 2.0;
    }
    if (/(button|кнопка|click|нажми|publish|post|submit|send|share|опубликов|отправ|размест|save|сохран)/i.test(targetText) && isButtonLike(el)) {
      score += 2.0;
    }
    if (/(upload|file|attach|загруз|файл|прикреп)/i.test(targetText) && safeAttr(el, "type").toLowerCase() === "file") {
      score += 3.0;
    }
    return score;
  }

  function selectorFromTarget(target) {
    const raw = String(target || "").trim();
    const prefixed = raw.match(/^(?:css|selector)\s*[:=]\s*(.+)$/i);
    if (prefixed) return prefixed[1].trim();
    if (/^(#[\w-]+|\.[\w-]+|[a-z][\w-]*(?:[#.\[]|$)|\[[^\]]+\])/.test(raw)) {
      return raw;
    }
    return "";
  }

  function resolveElement(target) {
    const selector = selectorFromTarget(target);
    if (selector) {
      try {
        const el = querySelectorDeep(selector);
        if (el && visible(el)) return { el, score: 100 };
      } catch (_err) {
        // If the target only looked like a selector, fall back to semantic scoring.
      }
    }
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
      return "Прокрутил к началу страницы.";
    }
    if (direction === "bottom") {
      window.scrollTo({ top: document.documentElement.scrollHeight, left: window.scrollX, behavior: "smooth" });
      return "Прокрутил к концу страницы.";
    }
    window.scrollBy({
      top: direction === "up" ? -pixels : pixels,
      left: 0,
      behavior: "smooth"
    });
    return `Прокрутил ${direction === "up" ? "вверх" : "вниз"} на ${Math.round(pixels)} пикс.`;
  }

  function actionNameRu(type) {
    const names = {
      navigate: "перейти по адресу",
      back: "вернуться назад",
      forward: "перейти вперёд",
      reload: "обновить страницу",
      focus_tab: "переключиться на вкладку",
      scroll: "прокрутить страницу",
      auth_probe: "проверить авторизацию",
      highlight: "подсветить элемент",
      click: "нажать",
      fill: "заполнить",
      select: "выбрать"
    };
    return names[type] || String(type || "действие");
  }

  function actionApprovalMode(cfg) {
    return cfg && cfg.actionApprovalMode === "auto" ? "auto" : "confirm";
  }

  function isConfirmableAction(action) {
    return ["click", "fill", "select", "navigate", "back", "forward", "reload"].includes(String(action && action.type || ""));
  }

  function confirmAction(cfg, action, label) {
    if (actionApprovalMode(cfg) === "auto") return true;
    if (!isConfirmableAction(action)) return true;
    const reason = action.reason ? `\n\nПричина: ${action.reason}` : "";
    return window.confirm(`Hermes хочет выполнить действие: ${label}.${reason}`);
  }

  async function executeAction(action) {
    const cfg = await settings();
    const actionId = action.id;
    try {
      if (action.type === "navigate") {
        const url = String(action.value || action.target || "");
        if (!url) throw new Error("No URL provided.");
        if (!confirmAction(cfg, action, `перейти по адресу:\n${url}`)) {
          throw new Error("Пользователь отменил переход.");
        }
        location.href = url;
        await postJson("/v1/actions/result", {
          actionId,
          sessionId: cfg.sessionId || DEFAULT_SESSION,
          ok: true,
          status: "done",
          message: "Переход начат."
        });
        return;
      }

      if (["back", "forward", "reload"].includes(action.type)) {
        if (!confirmAction(cfg, action, actionNameRu(action.type))) {
          throw new Error(`Пользователь отменил действие: ${actionNameRu(action.type)}.`);
        }
        await postJson("/v1/actions/result", {
          actionId,
          sessionId: cfg.sessionId || DEFAULT_SESSION,
          ok: true,
          status: "done",
          message: `${actionNameRu(action.type)}: начато.`
        });
        if (action.type === "back") history.back();
        if (action.type === "forward") history.forward();
        if (action.type === "reload") location.reload();
        setTimeout(() => quiet(() => sendSnapshot(true)), 1000);
        return;
      }

      if (action.type === "focus_tab") {
        const focused = await focusCurrentTab();
        await postJson("/v1/actions/result", {
          actionId,
          sessionId: cfg.sessionId || DEFAULT_SESSION,
          ok: Boolean(focused && focused.ok),
          status: focused && focused.ok ? "done" : "failed",
          message: focused && focused.ok ? "Вкладка выведена на передний план." : "",
          error: focused && focused.ok ? "" : String((focused && focused.error) || "Не удалось вывести вкладку на передний план.")
        });
        setTimeout(() => quiet(() => sendSnapshot(true)), 500);
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
          message: "Снимок авторизации обновлён."
        });
        return;
      }

      const found = resolveElement(action.target || "");
      if (!found.el || found.score < 1) {
        throw new Error(`Цель не найдена: ${action.target || "(пусто)"}`);
      }
      highlight(found.el);

      if (action.type === "highlight") {
        await postJson("/v1/actions/result", {
          actionId,
          sessionId: cfg.sessionId || DEFAULT_SESSION,
          ok: true,
          status: "done",
          message: "Элемент подсвечен.",
          element: { score: found.score, label: elementScoreText(found.el).slice(0, 300) }
        });
        return;
      }

      if (!confirmAction(cfg, action, `${actionNameRu(action.type)}:\n${action.target || "(цель)"}`)) {
        throw new Error("Пользователь отменил действие.");
      }

      let ok = false;
      if (action.type === "fill") ok = setValue(found.el, action.value || "");
      if (action.type === "select") ok = selectValue(found.el, action.value || "");
      if (action.type === "click") {
        found.el.click();
        ok = true;
      }
      if (!ok) throw new Error(`Не удалось выполнить действие: ${actionNameRu(action.type)}`);
      await postJson("/v1/actions/result", {
        actionId,
        sessionId: cfg.sessionId || DEFAULT_SESSION,
        ok: true,
        status: "done",
        message: `${actionNameRu(action.type)}: выполнено.`,
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
    const currentTab = await getTabInfo(true);
    const sessionId = encodeURIComponent(cfg.sessionId || DEFAULT_SESSION);
    const tabId = encodeURIComponent(currentTab.tabId || fallbackPageTabId());
    try {
      const response = await getJson(`/v1/actions/next?sessionId=${sessionId}&tabId=${tabId}`);
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
