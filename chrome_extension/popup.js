const toggleBtn = document.getElementById("toggleBtn");
const statusEl = document.getElementById("status");

let currentEnabled = false;

function setStatus(text) {
  statusEl.textContent = text;
}

function renderButton() {
  toggleBtn.textContent = currentEnabled ? "关闭插件" : "开启插件";
  toggleBtn.style.background = currentEnabled ? "#dc2626" : "#2563eb";
}

function callBackground(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (response) => {
      const err = chrome.runtime.lastError;
      if (err) {
        reject(new Error(err.message || String(err)));
        return;
      }
      resolve(response);
    });
  });
}

async function refreshState() {
  const result = await callBackground({ type: "get-plugin-state" });
  if (!result || !result.ok) {
    throw new Error((result && result.error) || "获取插件状态失败");
  }
  currentEnabled = Boolean(result.enabled);
  renderButton();
  if (result.sync_ok === false) {
    setStatus(
      [
        currentEnabled ? "当前状态：已开启" : "当前状态：已关闭",
        "桥接同步：未连接（启动主程序后会自动恢复）"
      ].join("\n")
    );
    return;
  }
  setStatus(currentEnabled ? "当前状态：已开启" : "当前状态：已关闭");
}

toggleBtn.addEventListener("click", async () => {
  toggleBtn.disabled = true;
  setStatus("正在切换状态...");
  try {
    const nextEnabled = !currentEnabled;
    const result = await callBackground({ type: "set-plugin-state", enabled: nextEnabled });
    if (!result || !result.ok) {
      throw new Error((result && result.error) || "切换状态失败");
    }
    currentEnabled = Boolean(result.enabled);
    renderButton();
    if (result.sync_ok === false) {
      setStatus(
        [
          currentEnabled ? "当前状态：已开启" : "当前状态：已关闭",
          "桥接同步：未连接（启动主程序后会自动恢复）"
        ].join("\n")
      );
    } else {
      setStatus(currentEnabled ? "当前状态：已开启" : "当前状态：已关闭");
    }
  } catch (error) {
    setStatus(`失败：${error.message || error}`);
  } finally {
    toggleBtn.disabled = false;
  }
});

refreshState().catch((error) => {
  setStatus(`失败：${error.message || error}`);
});
