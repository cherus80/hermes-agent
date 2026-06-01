(async function () {
  const defaults = {
    gatewayUrl: "http://127.0.0.1:8765",
    sessionId: "default",
    token: "",
    actionApprovalMode: "confirm"
  };

  const els = {
    gatewayUrl: document.getElementById("gatewayUrl"),
    sessionId: document.getElementById("sessionId"),
    token: document.getElementById("token"),
    actionApprovalMode: document.getElementById("actionApprovalMode"),
    save: document.getElementById("save"),
    snapshot: document.getElementById("snapshot"),
    status: document.getElementById("status")
  };

  function setStatus(text) {
    els.status.textContent = text;
  }

  const stored = await chrome.storage.local.get(defaults);
  els.gatewayUrl.value = stored.gatewayUrl || defaults.gatewayUrl;
  els.sessionId.value = stored.sessionId || defaults.sessionId;
  els.token.value = stored.token || "";
  els.actionApprovalMode.value = stored.actionApprovalMode === "auto" ? "auto" : "confirm";

  els.save.addEventListener("click", async () => {
    await chrome.storage.local.set({
      gatewayUrl: els.gatewayUrl.value.trim() || defaults.gatewayUrl,
      sessionId: els.sessionId.value.trim() || defaults.sessionId,
      token: els.token.value,
      actionApprovalMode: els.actionApprovalMode.value === "auto" ? "auto" : "confirm"
    });
    setStatus(els.actionApprovalMode.value === "auto" ? "Сохранено. Авторежим включён." : "Сохранено. Режим подтверждений включён.");
  });

  els.snapshot.addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      setStatus("Нет активной вкладки.");
      return;
    }
    try {
      const response = await chrome.tabs.sendMessage(tab.id, { type: "hermes:snapshotNow" });
      setStatus(response && response.ok ? "Снимок отправлен." : "Не удалось отправить снимок.");
    } catch (err) {
      setStatus("Обнови страницу и попробуй снова.");
    }
  });
})();
