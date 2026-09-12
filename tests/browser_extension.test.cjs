"use strict";
// Testea el worker real sin Chrome, red, cuenta, audio ni contenido del usuario.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../browser_extension/service_worker.js"), "utf8");

function harness() {
  const saved = {};
  const tabs = new Map();
  const updates = [];
  const responses = [];
  const normalWindows = [{ id: 1, focused: true, incognito: false }];
  const createdWindows = [];
  let nextId = 1;
  let onMessage;
  let searches = 0;
  class Video {
    paused = true; muted = true; volume = 0; currentTime = 7; readyState = 4;
    async play() { this.paused = false; }
    pause() { this.paused = true; }
  }
  class Anchor { href = "https://www.youtube.com/watch?v=example123"; }
  const video = new Video();
  const anchor = new Anchor();
  const context = vm.createContext({
    URL, navigator: { userAgent: "Chrome" }, HTMLVideoElement: Video, HTMLAnchorElement: Anchor,
    setTimeout: (fn, delay) => {
      const timer = setTimeout(fn, delay === 100 ? 0 : delay);
      if (delay > 100) timer.unref();
      return timer;
    },
    document: {
      querySelector: (selector) => selector === "video.html5-main-video" ? video : selector.includes("a#video-title") ? anchor : null,
      querySelectorAll: () => ++searches >= 3 ? [anchor] : [],
    },
    chrome: {
      windows: {
        async getAll() { return normalWindows; },
        async create(options) {
          createdWindows.push(options);
          const tab = await context.chrome.tabs.create({url: options.url});
          normalWindows.push({ id: 2, focused: true, incognito: false });
          return { id: 2, tabs: [tab] };
        },
      },
      storage: { session: {
        async get(key) { return { [key]: saved[key] }; },
        async set(values) { Object.assign(saved, values); },
        async remove(key) { delete saved[key]; },
      } },
      tabs: {
        async get(id) { if (!tabs.has(id)) throw new Error("not_found"); return { ...tabs.get(id) }; },
        async create({ url }) {
          const tab = { id: nextId++, url, status: "complete", title: "Synthetic page", mutedInfo: { muted: true } };
          tabs.set(tab.id, tab); return { ...tab };
        },
        async update(id, values) {
          updates.push({ id, ...values });
          const tab = tabs.get(id);
          if ("url" in values) tab.url = values.url;
          if ("muted" in values) tab.mutedInfo = { muted: values.muted };
          return { ...tab };
        },
      },
      scripting: { async executeScript({ func }) { return [{ result: await func() }]; } },
      runtime: {
        onStartup: { addListener() {} }, onInstalled: { addListener() {} },
        connectNative: () => ({
          onMessage: { addListener(fn) { onMessage = fn; } }, onDisconnect: { addListener() {} },
          postMessage: value => responses.push(value),
        }),
      },
    },
  });
  vm.runInContext(source + "\nglobalThis.api = {dispatch, handleRequest};", context);
  return { api: context.api, context, tabs, saved, updates, video, anchor, responses, normalWindows, createdWindows,
    send: (operation, args = {}) => context.api.dispatch(operation, args),
    message: value => onMessage(value), queue: () => vm.runInContext("requestQueue", context),
  };
}

test("playback unmutes both browser tab and player", async () => {
  const h = harness();
  await h.send("open_site", { site_key: "youtube" });
  const result = await h.send("youtube_start");
  assert.equal(result.paused, false);
  assert.equal(result.muted, false);
  assert.equal(result.volume, .5);
  assert.ok(h.updates.some(update => update.muted === false));
});

test("verification detects browser tab muted independently of video", async () => {
  const h = harness();
  await h.send("open_site", { site_key: "youtube" });
  await h.send("youtube_start");
  h.tabs.get(1).mutedInfo.muted = true;
  assert.equal((await h.send("youtube_read")).muted, true);
  assert.equal(h.video.muted, false);
});

test("existing managed YouTube tab is reused without navigating home", async () => {
  const h = harness();
  await h.send("open_site", { site_key: "youtube" });
  h.tabs.get(1).url = "https://www.youtube.com/watch?v=example123";
  const result = await h.send("open_site", { site_key: "youtube" });
  assert.match(result.url, /watch/);
  assert.equal(h.updates.length, 0);
  assert.equal(h.tabs.size, 1);
});

test("does not take over a managed tab navigated to an unrelated site", async () => {
  const h = harness();
  await h.send("open_site", { site_key: "youtube" });
  h.tabs.get(1).url = "https://example.com/private";
  await assert.rejects(h.send("youtube_start"), /tab_unavailable/);
  await h.send("open_site", { site_key: "youtube" });
  assert.equal(h.tabs.get(1).url, "https://example.com/private");
  assert.equal(h.tabs.size, 2);
});

test("search waits for dynamically loaded results and encodes only query data", async () => {
  const h = harness();
  const result = await h.send("youtube_search", { query: "música & calma" });
  assert.equal(result.result_count, 1);
  assert.equal(new URL(result.url).searchParams.get("search_query"), "música & calma");
  const selected = await h.send("youtube_select_first");
  assert.equal(selected.url, h.anchor.href);
});

test("rejects external links and inherited object keys", async () => {
  const h = harness();
  await assert.rejects(h.send("open_site", { site_key: "__proto__" }), /site_not_allowed/);
  await h.send("open_site", { site_key: "youtube" });
  h.anchor.href = "https://example.com/watch?v=example123";
  await assert.rejects(h.send("youtube_select_first"), /result_not_allowed/);
  await assert.rejects(h.send("youtube_start", { script: "malicious" }), /invalid_arguments/);
});

test("stop pauses without closing the user's tab", async () => {
  const h = harness();
  await h.send("open_site", { site_key: "youtube" });
  await h.send("youtube_start");
  assert.equal((await h.send("youtube_stop")).paused, true);
  assert.equal(h.tabs.size, 1);
});

test("background Chrome without a normal window creates one in the extension profile", async () => {
  const h = harness();
  h.normalWindows.length = 0;
  const result = await h.send("open_site", {site_key: "youtube"});
  assert.equal(result.url, "https://www.youtube.com/");
  assert.equal(h.createdWindows.length, 1);
  assert.equal(h.createdWindows[0].incognito, false);
  assert.equal(h.createdWindows[0].type, "normal");
  await h.send("open_site", {site_key: "youtube"});
  assert.equal(h.createdWindows.length, 1);
});

test("native requests are serialized and errors disclose no arbitrary text", async () => {
  const h = harness();
  const message = (id, operation, args = {}) => ({ schema_version: 1, type: "request", request_id: id, operation, arguments: args });
  h.message(message("1", "open_site", { site_key: "youtube" }));
  h.message(message("2", "youtube_start"));
  await h.queue();
  assert.deepEqual(h.responses.slice(1).map(value => [value.request_id, value.success]), [["1", true], ["2", true]]);
  h.video.play = async () => { throw new Error("PRIVATE DETAILS secret"); };
  h.video.paused = true;
  await h.api.handleRequest(message("3", "youtube_start"));
  assert.equal(h.responses.at(-1).error_code, "extension_failure");
  assert.equal(h.responses.at(-1).success, false);
});
