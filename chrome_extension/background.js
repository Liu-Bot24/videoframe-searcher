const BRIDGE_BASE_CANDIDATES = ["http://127.0.0.1:38999", "http://localhost:38999"];
const ENABLED_KEY = "vfs_plugin_enabled";
const GOOGLE_HOME_URL = "https://www.google.com/?hl=zh-CN";
const GOOGLE_LENS_HOME_URL = "https://lens.google.com/";
const GOOGLE_LENS_UPLOAD_URL = "https://lens.google.com/upload";
const HEARTBEAT_ALARM = "vfs_bridge_heartbeat";

const BOT_MARKERS = ["google.com/sorry", "unusual traffic", "异常流量", "異常流量"];
const UNAVAILABLE_MARKERS = ["以圖搜尋功能無法使用", "以图搜图功能无法使用", "无法按图搜索", "無法按圖搜尋"];
const NOT_ASSOCIATED_MARKERS = ["图片未找到", "圖片未找到", "未与您的账号关联", "未與您的帳號關聯"];

let pluginEnabled = false;
let processing = false;
let activeBridgeBase = BRIDGE_BASE_CANDIDATES[0];
let lastSync = { ok: false, error: "not_synced", at: 0 };

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isGooglePage(url) {
  const text = String(url || "").toLowerCase();
  return text.startsWith("https://www.google.") || text.startsWith("https://lens.google.");
}

async function createTabWithWindowFallback(url, active) {
  try {
    const tab = await chrome.tabs.create({ url, active: Boolean(active) });
    if (!tab || !tab.id) {
      throw new Error("创建标签页失败");
    }
    return tab.id;
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    if (!message.includes("No current window")) {
      throw error;
    }
    const createdWindow = await chrome.windows.create({ url, focused: true, type: "normal" });
    const firstTab = createdWindow && createdWindow.tabs && createdWindow.tabs[0];
    if (!firstTab || !firstTab.id) {
      throw new Error("创建浏览器窗口失败");
    }
    return firstTab.id;
  }
}

async function bridgeRequest(path, options = {}) {
  const bases = [activeBridgeBase, ...BRIDGE_BASE_CANDIDATES.filter((b) => b !== activeBridgeBase)];
  let lastError = null;
  for (const base of bases) {
    try {
      const response = await fetch(`${base}${path}`, options);
      if (!response.ok) {
        throw new Error(`桥接请求失败 ${path}, HTTP ${response.status}`);
      }
      activeBridgeBase = base;
      return await response.json();
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("桥接请求失败");
}

async function apiGet(path) {
  return bridgeRequest(path, { cache: "no-store" });
}

async function apiPost(path, payload) {
  return bridgeRequest(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {})
  });
}

function detectStatus(url, text) {
  const lowerUrl = String(url || "").toLowerCase();
  const lowerText = String(text || "").toLowerCase();
  if (BOT_MARKERS.some((m) => lowerUrl.includes(m.toLowerCase()) || lowerText.includes(m.toLowerCase()))) {
    return "风控/验证码";
  }
  if (UNAVAILABLE_MARKERS.some((m) => lowerText.includes(m.toLowerCase()))) {
    return "功能不可用";
  }
  if (NOT_ASSOCIATED_MARKERS.some((m) => lowerText.includes(m.toLowerCase()))) {
    return "会话未关联/链接失效";
  }
  if (lowerUrl.includes("/search?")) {
    return "正常/未知";
  }
  return "未知";
}

async function readEnabledState() {
  const data = await chrome.storage.local.get([ENABLED_KEY]);
  pluginEnabled = Boolean(data[ENABLED_KEY]);
}

async function setEnabledState(enabled) {
  pluginEnabled = Boolean(enabled);
  await chrome.storage.local.set({ [ENABLED_KEY]: pluginEnabled });
  syncPluginState().catch(() => {});
  return { enabled: pluginEnabled };
}

async function sendHeartbeat() {
  await apiPost("/heartbeat", { enabled: pluginEnabled });
}

async function syncPluginState() {
  try {
    await apiPost("/plugin-enabled", { enabled: pluginEnabled });
    if (pluginEnabled) {
      await sendHeartbeat();
    }
    lastSync = { ok: true, error: "", at: Date.now() };
  } catch (error) {
    lastSync = {
      ok: false,
      error: error && error.message ? error.message : String(error),
      at: Date.now()
    };
    throw error;
  }
}

async function fetchNextTask() {
  const payload = await apiPost("/next-task", {});
  if (!payload.ok || !payload.has_task) {
    return null;
  }
  return payload;
}

async function waitForTabComplete(tabId, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    let done = false;
    let timer = null;

    function cleanup() {
      if (done) {
        return;
      }
      done = true;
      if (timer) {
        clearTimeout(timer);
      }
      chrome.tabs.onUpdated.removeListener(onUpdated);
    }

    function onUpdated(updatedTabId, changeInfo, tab) {
      if (updatedTabId !== tabId) {
        return;
      }
      if (changeInfo.status === "complete") {
        cleanup();
        resolve((tab && tab.url) || "");
      }
    }

    timer = setTimeout(() => {
      cleanup();
      reject(new Error("等待标签页加载超时"));
    }, timeoutMs);

    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.tabs
      .get(tabId)
      .then((tab) => {
        if (tab && tab.status === "complete") {
          cleanup();
          resolve(tab.url || "");
        }
      })
      .catch(() => {});
  });
}

async function createGoogleTab(active) {
  const tabId = await createTabWithWindowFallback(GOOGLE_HOME_URL, active);
  await waitForTabComplete(tabId, 20000).catch(() => {});
  return tabId;
}

async function navigateTab(tabId, url) {
  const tab = await chrome.tabs.update(tabId, { url });
  if (!tab || !tab.id) {
    throw new Error("更新目标标签页失败");
  }
  await waitForTabComplete(tab.id, 20000).catch(() => {});
  return tab.id;
}

async function ensureTargetTab(preferTabId, allowPrefer) {
  if (allowPrefer && preferTabId) {
    try {
      const tab = await chrome.tabs.get(preferTabId);
      if (tab && tab.id && isGooglePage(tab.url || "")) {
        return tab.id;
      }
    } catch (_) {
      // ignore
    }
  }
  // 后续任务强制走新标签，避免在同一结果页连续覆盖。
  return createGoogleTab(false);
}

async function injectUpload(tabId, frame) {
  const fileName = frame.file_name || "frame.jpg";
  const mimeType = frame.mime_type || "image/jpeg";
  const base64Data = frame.base64_data;
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: async (name, mime, b64) => {
      const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

      function decodeBase64ToBytes(base64) {
        const raw = atob(base64);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i += 1) {
          bytes[i] = raw.charCodeAt(i);
        }
        return bytes;
      }

      async function ensureFileInput() {
        let input = document.querySelector("input[type='file'][name='encoded_image']");
        if (!input) {
          const lensTriggers = [
            "div[data-base-lens-url]",
            "a[aria-label*='搜索图片']",
            "a[aria-label*='Search images']",
            "a[href*='/imghp']",
            "button[aria-label*='Google Lens']",
            "button[aria-label*='Lens']",
            "[role='button'][aria-label*='Lens']"
          ];
          for (const selector of lensTriggers) {
            const trigger = document.querySelector(selector);
            if (trigger) {
              trigger.click();
              for (let i = 0; i < 12; i += 1) {
                await sleep(400);
                input = document.querySelector("input[type='file'][name='encoded_image']");
                if (input) {
                  return input;
                }
              }
            }
          }
        }
        if (!input) {
          input = document.querySelector("input[type='file']");
        }
        return input;
      }

      const fileInput = await ensureFileInput();
      if (!fileInput) {
        return { ok: false, error: "未找到 Google 文件上传控件" };
      }

      const bytes = decodeBase64ToBytes(b64);
      const file = new File([bytes], name, { type: mime });
      const dt = new DataTransfer();
      dt.items.add(file);
      fileInput.files = dt.files;
      fileInput.dispatchEvent(new Event("input", { bubbles: true }));
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
      return { ok: true };
    },
    args: [fileName, mimeType, base64Data]
  });
  const first = results && results.length > 0 ? results[0].result : null;
  if (!first || !first.ok) {
    throw new Error((first && first.error) || "页面注入失败");
  }
}

async function submitViaDirectLensForm(tabId, frame) {
  const fileName = frame.file_name || "frame.jpg";
  const mimeType = frame.mime_type || "image/jpeg";
  const base64Data = frame.base64_data;
  let results = null;
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId },
      func: async (name, mime, b64, uploadUrl) => {
        function decodeBase64ToBytes(base64) {
          const raw = atob(base64);
          const bytes = new Uint8Array(raw.length);
          for (let i = 0; i < raw.length; i += 1) {
            bytes[i] = raw.charCodeAt(i);
          }
          return bytes;
        }

        const existing = document.getElementById("vfs-direct-upload-form");
        if (existing) {
          existing.remove();
        }

        const form = document.createElement("form");
        form.id = "vfs-direct-upload-form";
        form.method = "POST";
        form.action = uploadUrl;
        form.enctype = "multipart/form-data";
        form.style.display = "none";

        const input = document.createElement("input");
        input.type = "file";
        input.name = "encoded_image";
        input.accept = "image/*";

        const bytes = decodeBase64ToBytes(b64);
        const file = new File([bytes], name, { type: mime });
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;

        form.appendChild(input);
        document.body.appendChild(form);
        form.submit();
        return { ok: true };
      },
      args: [fileName, mimeType, base64Data, GOOGLE_LENS_UPLOAD_URL]
    });
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    if (message.includes("Frame with ID 0 was removed.")) {
      return;
    }
    throw error;
  }
  const first = results && results.length > 0 ? results[0].result : null;
  if (!first || !first.ok) {
    throw new Error((first && first.error) || "直传 Google Lens 失败");
  }
}

async function uploadFrame(tabId, task) {
  const currentTab = await chrome.tabs.get(tabId).catch(() => null);
  const currentUrl = (currentTab && currentTab.url) || "";
  if (!String(currentUrl).toLowerCase().startsWith("https://lens.google.")) {
    await navigateTab(tabId, GOOGLE_LENS_HOME_URL);
    await delay(800);
  }
  await submitViaDirectLensForm(tabId, task);
}

async function waitForLanding(tabId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let latestUrl = "";
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId);
    latestUrl = tab.url || latestUrl;
    const lower = latestUrl.toLowerCase();
    if (
      lower.includes("/search?") ||
      lower.includes("lens.google.com/search") ||
      lower.includes("google.com/sorry")
    ) {
      break;
    }
    await delay(1000);
  }
  return latestUrl;
}

async function readPageText(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => (document.body ? document.body.innerText.slice(0, 30000) : "")
    });
    return (results && results[0] && results[0].result) || "";
  } catch (_) {
    return "";
  }
}

async function processNextTask(triggerTabId) {
  if (!pluginEnabled || processing) {
    return;
  }
  processing = true;
  try {
    await syncPluginState().catch(() => {});
    let first = true;
    let preferTabId = triggerTabId;
    while (pluginEnabled) {
      const next = await fetchNextTask();
      if (!next) {
        break;
      }
      const task = next.task || {};
      try {
        const tabId = await ensureTargetTab(preferTabId, first);
        first = false;
        preferTabId = null;
        await delay(700);
        await uploadFrame(tabId, task);
        const url = await waitForLanding(tabId, 30000);
        const text = await readPageText(tabId);
        const status = detectStatus(url, text);
        let note = "";
        if (status === "会话未关联/链接失效") {
          note = "页面提示图片未与账号关联。";
        } else if (status === "功能不可用") {
          note = "Google 返回按图搜索不可用。";
        } else if (status === "风控/验证码") {
          note = "命中 Google 风控页。";
        }
        await apiPost("/task-result", { task_id: task.task_id || "", status, url, note });
      } catch (error) {
        await apiPost("/task-result", {
          task_id: task.task_id || "",
          status: "扩展执行失败",
          url: "",
          note: error && error.message ? error.message : String(error)
        }).catch(() => {});
      }
    }
  } catch (error) {
    await apiPost("/task-result", {
      task_id: "",
      status: "扩展执行失败",
      url: "",
      note: error && error.message ? error.message : String(error)
    }).catch(() => {});
  } finally {
    processing = false;
  }
}

async function initialize() {
  await readEnabledState();
  await syncPluginState().catch(() => {});
  chrome.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: 1 });
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete") {
    return;
  }
  if (!isGooglePage(tab.url || "")) {
    return;
  }
  processNextTask(tabId);
});

chrome.runtime.onInstalled.addListener(() => {
  initialize();
});

chrome.runtime.onStartup.addListener(() => {
  initialize();
});

initialize();

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== HEARTBEAT_ALARM) {
    return;
  }
  if (!pluginEnabled) {
    return;
  }
  syncPluginState().catch(() => {});
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || !message.type) {
    return false;
  }

  if (message.type === "get-plugin-state") {
    syncPluginState().catch(() => {});
    sendResponse({
      ok: true,
      enabled: pluginEnabled,
      sync_ok: lastSync.ok,
      sync_error: lastSync.error,
      sync_at: lastSync.at
    });
    return false;
  }

  if (message.type === "set-plugin-state") {
    setEnabledState(Boolean(message.enabled))
      .then((state) =>
        sendResponse({
          ok: true,
          enabled: state.enabled,
          sync_ok: lastSync.ok,
          sync_error: lastSync.error,
          sync_at: lastSync.at
        })
      )
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  return false;
});
