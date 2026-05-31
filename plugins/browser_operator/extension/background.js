chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== "object") return false;

  if (message.type === "hermes:getTabInfo") {
    const tab = sender.tab || {};
    sendResponse({
      ok: true,
      tab: {
        tabId: tab.id != null ? `chrome-tab:${tab.id}` : "",
        chromeTabId: tab.id,
        windowId: tab.windowId,
        index: tab.index,
        active: Boolean(tab.active),
        highlighted: Boolean(tab.highlighted),
        pinned: Boolean(tab.pinned),
        audible: Boolean(tab.audible),
        title: tab.title || "",
        url: tab.url || "",
        favIconUrl: tab.favIconUrl || ""
      }
    });
    return true;
  }

  if (message.type === "hermes:focusSelf") {
    const tab = sender.tab || {};
    if (tab.id == null) {
      sendResponse({ ok: false, error: "No sender tab." });
      return true;
    }

    const focusWindow = tab.windowId == null
      ? Promise.resolve()
      : chrome.windows.update(tab.windowId, { focused: true });

    Promise.resolve(focusWindow)
      .then(() => chrome.tabs.update(tab.id, { active: true }))
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: String(err && err.message ? err.message : err) }));
    return true;
  }

  return false;
});
