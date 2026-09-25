"use strict";

const HOST_NAME = "com.desktop_agent.browser";
const SCHEMA_VERSION = 1;
const MANAGED_TAB_KEY = "desktopAgentManagedTab";
const SAFE_SITES = Object.freeze({
  youtube: "https://www.youtube.com/",
  google: "https://www.google.com/",
  github: "https://github.com/",
  spotify: "https://open.spotify.com/",
});
const YOUTUBE_ORIGIN = "https://www.youtube.com";
let nativePort = null;
let reconnectTimer = null;
let requestQueue = Promise.resolve();

function browserKind() {
  return navigator.userAgent.includes("OPR/") ? "opera_gx" : "chrome";
}

function connectNative() {
  if (nativePort !== null) return;
  try {
    nativePort = chrome.runtime.connectNative(HOST_NAME);
    nativePort.onMessage.addListener((message) => {
      requestQueue = requestQueue.then(() => handleRequest(message)).catch(() => {});
    });
    nativePort.onDisconnect.addListener(() => {
      nativePort = null;
      scheduleReconnect();
    });
    nativePort.postMessage({
      schema_version: SCHEMA_VERSION,
      type: "hello",
      browser: browserKind(),
    });
  } catch (_error) {
    nativePort = null;
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (reconnectTimer !== null) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectNative();
  }, 2000);
}

async function managedTabId() {
  const value = await chrome.storage.session.get(MANAGED_TAB_KEY);
  const candidate = value[MANAGED_TAB_KEY];
  if (!Number.isInteger(candidate)) return null;
  try {
    const tab = await chrome.tabs.get(candidate);
    const url = new URL(tab.url || "");
    const allowed = Object.values(SAFE_SITES).some((site) => new URL(site).origin === url.origin);
    if (!allowed) {
      await chrome.storage.session.remove(MANAGED_TAB_KEY);
      return null;
    }
    return candidate;
  } catch (_error) {
    await chrome.storage.session.remove(MANAGED_TAB_KEY);
    return null;
  }
}

async function openManagedUrl(url) {
  let tabId = await managedTabId();
  let tab;
  if (tabId === null) {
    const windows = (await chrome.windows.getAll({windowTypes: ["normal"]}))
      .filter((window) => !window.incognito && Number.isInteger(window.id));
    const targetWindow = windows.find((window) => window.focused) || windows[0];
    if (targetWindow) {
      tab = await chrome.tabs.create({ url, active: true, windowId: targetWindow.id });
    } else {
      // Chrome puede estar en background/selector de perfiles sin ventana normal.
      // Se crea una ventana en el perfil de ESTA extensión, sin elegir otro perfil.
      const created = await chrome.windows.create({ url, type: "normal", focused: true, incognito: false });
      tab = created?.tabs?.[0];
      if (!tab) throw new Error("window_unavailable");
    }
    tabId = tab.id;
    if (!Number.isInteger(tabId)) throw new Error("tab_unavailable");
    await chrome.storage.session.set({ [MANAGED_TAB_KEY]: tabId });
  } else {
    tab = await chrome.tabs.update(tabId, { url, active: true });
  }
  // Navegación + DOM deben caber en los 8 s del puente Python.
  await waitForComplete(tabId, 4000);
  return chrome.tabs.get(tabId);
}

async function waitForComplete(tabId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  do {
    const current = await chrome.tabs.get(tabId);
    if (current.status === "complete") return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  } while (Date.now() < deadline);
  throw new Error("navigation_timeout");
}

function youtubePage(tab) {
  const parsed = new URL(tab.url);
  if (parsed.origin !== YOUTUBE_ORIGIN) throw new Error("unexpected_origin");
  return { url: tab.url, title: tab.title || "YouTube" };
}

async function executeInManagedTab(func) {
  const tabId = await managedTabId();
  if (tabId === null) throw new Error("tab_unavailable");
  youtubePage(await chrome.tabs.get(tabId));
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func,
  });
  if (!Array.isArray(results) || results.length !== 1) {
    throw new Error("script_result_invalid");
  }
  return results[0].result;
}

async function handleOpenSite(argumentsValue) {
  if (!argumentsValue || Object.keys(argumentsValue).length !== 1) {
    throw new Error("invalid_arguments");
  }
  const siteKey = argumentsValue.site_key;
  if (!Object.hasOwn(SAFE_SITES, siteKey)) throw new Error("site_not_allowed");
  const url = SAFE_SITES[siteKey];
  if (typeof url !== "string") throw new Error("site_not_allowed");
  if (siteKey === "youtube") {
    const existingId = await managedTabId();
    if (existingId !== null) {
      const existing = await chrome.tabs.get(existingId);
      if (new URL(existing.url).origin === YOUTUBE_ORIGIN) return youtubePage(existing);
    }
  }
  const tab = await openManagedUrl(url);
  if (siteKey === "youtube") return youtubePage(tab);
  return { site_key: siteKey };
}

async function handleYoutubeSearch(argumentsValue) {
  if (!argumentsValue || Object.keys(argumentsValue).length !== 1) {
    throw new Error("invalid_arguments");
  }
  const query = argumentsValue.query;
  if (typeof query !== "string" || query.length < 1 || query.length > 200) {
    throw new Error("invalid_query");
  }
  const url = `${YOUTUBE_ORIGIN}/results?search_query=${encodeURIComponent(query)}`;
  const tab = await openManagedUrl(url);
  const resultCount = await executeInManagedTab(async () => {
    const deadline = Date.now() + 2500;
    do {
      const count = document.querySelectorAll("ytd-video-renderer a#video-title[href*='/watch']").length;
      if (count > 0) return count;
      if (document.querySelector("ytd-background-promo-renderer")) return 0;
      await new Promise((resolve) => setTimeout(resolve, 100));
    } while (Date.now() < deadline);
    // Ausencia del DOM esperado no demuestra una búsqueda legítimamente vacía.
    throw new Error("search_dom_unavailable");
  });
  if (!Number.isInteger(resultCount) || resultCount < 0) {
    throw new Error("result_count_invalid");
  }
  return { ...youtubePage(tab), result_count: resultCount };
}

async function handleYoutubeSelectFirst() {
  const href = await executeInManagedTab(() => {
    const element = document.querySelector(
      "ytd-video-renderer a#video-title[href*='/watch']",
    );
    return element instanceof HTMLAnchorElement ? element.href : null;
  });
  if (typeof href !== "string") throw new Error("no_results");
  const parsed = new URL(href);
  const videoId = parsed.searchParams.get("v");
  if (
    parsed.origin !== YOUTUBE_ORIGIN ||
    parsed.pathname !== "/watch" ||
    !/^[A-Za-z0-9_-]{6,32}$/.test(videoId || "")
  ) {
    throw new Error("result_not_allowed");
  }
  const safeUrl = `${YOUTUBE_ORIGIN}/watch?v=${encodeURIComponent(videoId)}`;
  return youtubePage(await openManagedUrl(safeUrl));
}

function readVideoState() {
  const video = document.querySelector("video.html5-main-video");
  if (!(video instanceof HTMLVideoElement)) return null;
  return {
    paused: video.paused,
    muted: video.muted,
    volume: Number.isFinite(video.volume) ? video.volume : null,
    current_time: Number.isFinite(video.currentTime) ? video.currentTime : 0,
  };
}

async function handleYoutubeStart() {
  const tabId = await managedTabId();
  if (tabId === null) throw new Error("tab_unavailable");
  youtubePage(await chrome.tabs.get(tabId));
  // El silencio de la pestaña es independiente de video.muted.
  await chrome.tabs.update(tabId, { muted: false });
  const state = await executeInManagedTab(async () => {
    const deadline = Date.now() + 4000;
    let video;
    do {
      video = document.querySelector("video.html5-main-video");
      if (video instanceof HTMLVideoElement && video.readyState >= 1) break;
      await new Promise((resolve) => setTimeout(resolve, 100));
    } while (Date.now() < deadline);
    if (!(video instanceof HTMLVideoElement)) return null;
    video.muted = false;
    if (video.volume <= 0) video.volume = 0.5;
    if (video.paused) await Promise.race([
      video.play(),
      new Promise((_, reject) => setTimeout(() => reject(new Error("playback_timeout")), 1500)),
    ]);
    return {
      paused: video.paused,
      muted: video.muted,
      volume: Number.isFinite(video.volume) ? video.volume : null,
      current_time: Number.isFinite(video.currentTime) ? video.currentTime : 0,
    };
  });
  if (!state || state.paused || state.muted) throw new Error("playback_not_started");
  const tab = await chrome.tabs.get(tabId);
  if (tab.mutedInfo?.muted) throw new Error("tab_muted");
  return { ...youtubePage(tab), ...state };
}

async function handleYoutubeRead() {
  const state = await executeInManagedTab(readVideoState);
  if (!state) throw new Error("video_unavailable");
  const tabId = await managedTabId();
  const tab = await chrome.tabs.get(tabId);
  return { ...youtubePage(tab), ...state, muted: state.muted || Boolean(tab.mutedInfo?.muted) };
}

async function handleYoutubeStop() {
  const managedId = await managedTabId();
  if (managedId === null) throw new Error("tab_unavailable");
  const state = await executeInManagedTab(() => {
    const video = document.querySelector("video.html5-main-video");
    if (!(video instanceof HTMLVideoElement)) return null;
    video.pause();
    return {
      paused: video.paused,
      muted: video.muted,
      volume: Number.isFinite(video.volume) ? video.volume : null,
      current_time: Number.isFinite(video.currentTime) ? video.currentTime : 0,
    };
  });
  if (state && !state.paused) throw new Error("playback_not_stopped");
  const tabId = await managedTabId();
  const page = youtubePage(await chrome.tabs.get(tabId));
  if (!state) return { ...page, paused: true, media_present: false };
  return { ...page, ...state, media_present: true };
}

async function dispatch(operation, argumentsValue) {
  if (operation === "open_site") return handleOpenSite(argumentsValue);
  if (operation === "youtube_search") return handleYoutubeSearch(argumentsValue);
  if (!argumentsValue || Array.isArray(argumentsValue) || Object.keys(argumentsValue).length !== 0) {
    throw new Error("invalid_arguments");
  }
  if (operation === "youtube_select_first") return handleYoutubeSelectFirst();
  if (operation === "youtube_start") return handleYoutubeStart();
  if (operation === "youtube_read") return handleYoutubeRead();
  if (operation === "youtube_stop") return handleYoutubeStop();
  throw new Error("operation_not_allowed");
}

function safeErrorCode(error) {
  const code = error instanceof Error ? error.message : "extension_failure";
  // Un texto privado también puede tener sólo minúsculas: no basta una regex.
  const known = new Set([
    "window_unavailable", "tab_unavailable", "navigation_timeout",
    "unexpected_origin", "script_result_invalid", "invalid_arguments",
    "site_not_allowed", "invalid_query", "search_dom_unavailable",
    "result_count_invalid", "no_results", "result_not_allowed",
    "playback_timeout", "playback_not_started", "tab_muted",
    "video_unavailable", "playback_not_stopped", "operation_not_allowed",
  ]);
  return known.has(code) ? code : "extension_failure";
}

async function handleRequest(message) {
  if (
    !message ||
    message.schema_version !== SCHEMA_VERSION ||
    message.type !== "request" ||
    typeof message.request_id !== "string" ||
    typeof message.operation !== "string" ||
    typeof message.arguments !== "object"
  ) {
    return;
  }
  try {
    const payload = await dispatch(message.operation, message.arguments);
    nativePort?.postMessage({
      schema_version: SCHEMA_VERSION,
      type: "response",
      request_id: message.request_id,
      success: true,
      payload,
      error_code: null,
    });
  } catch (error) {
    nativePort?.postMessage({
      schema_version: SCHEMA_VERSION,
      type: "response",
      request_id: message.request_id,
      success: false,
      payload: null,
      error_code: safeErrorCode(error),
    });
  }
}

chrome.runtime.onStartup.addListener(connectNative);
chrome.runtime.onInstalled.addListener(connectNative);
connectNative();
