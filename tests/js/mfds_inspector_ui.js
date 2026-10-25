/* Isolated DOM/fetch harness; no network and no real names. */
const fs = require("fs"), vm = require("vm");
const scenario = JSON.parse(process.argv[3]);
class Node {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.hidden = true; this.value = ""; this.text = ""; this.listeners = {}; this.classes = new Set(); this.classList = { add: c => this.classes.add(c), remove: c => this.classes.delete(c) }; }
  appendChild(node) { this.children.push(node); return node; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(c => c.textContent).join(""); }
  set innerHTML(value) { if (value !== "") throw new Error("Unsafe HTML insertion"); this.text = ""; this.children = []; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name] || ""; }
  removeAttribute(name) { delete this.attrs[name]; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  querySelectorAll(selector) { return walk(this).filter(n => selector === "[data-mfds-name]" && n.attrs["data-mfds-name"]); }
}
function walk(node) { return node.children.flatMap(c => [c, ...walk(c)]); }
const ids = {}, get = id => ids[id] || (ids[id] = new Node("div"));
const config = { "data-url": "https://example.test", "data-key": "public-test-key", "data-root": "../../", "data-asset-root": "../../" };
get("grm-inspector-cfg").attrs = config;
get("mfds-doc-attendees").attrs = config;
if (scenario.documentNames) scenario.documentNames.forEach(name => { const n = new Node("span"); n.setAttribute("data-mfds-name", name); n.textContent = name; get("mfds-doc-attendees").appendChild(n); });
const document = { getElementById: get, documentElement: { lang: scenario.lang || "ko" }, createElement: tag => new Node(tag) };
const calls = [];
const fetch = async (url, options) => {
  calls.push({ url, body: options && options.body });
  const method = url.split("/").pop();
  const value = method === "doc-pages.json" ? scenario.membership : scenario.responses[method];
  return { ok: !scenario.fail, status: scenario.fail ? 503 : 200, json: async () => value };
};
(async () => {
  vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), { document, window: { location: { search: scenario.query || "?source=mfds" } }, fetch, URLSearchParams, Promise, setTimeout, clearTimeout });
  await new Promise(resolve => setTimeout(resolve, 15));
  const output = { calls, indexText: get("mfds-index").textContent,
    indexLinks: walk(get("mfds-index")).filter(n => n.tagName === "A").map(n => ({ text: n.textContent, href: n.href })),
    profileHidden: get("mfds-profile").hidden, state: get("mfds-state").textContent,
    fdaNoticeHidden: get("ip-fda-ai-facts").hidden, mfdsNoticeHidden: get("mfds-ai-facts").hidden,
    years: get("mfds-years").textContent,
    docLinks: walk(get("mfds-docs")).filter(n => n.tagName === "A").map(n => n.href),
    documentLinks: walk(get("mfds-doc-attendees")).filter(n => n.tagName === "A").map(n => ({ text: n.textContent, href: n.href })),
    documentText: get("mfds-doc-attendees").textContent };
  process.stdout.write(JSON.stringify(output));
})().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
