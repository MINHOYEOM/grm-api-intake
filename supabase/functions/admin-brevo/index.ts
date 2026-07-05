import {
  audit,
  clampNumber,
  envAny,
  jsonResponse,
  optionsResponse,
  readJsonBody,
  requireAdmin,
} from "../_shared/admin.ts";

function apiKey(): string {
  return envAny(["BREVO_API_KEY", "NEWSLETTER_API_KEY"]);
}

function listId(): string {
  return envAny(["BREVO_LIST_ID", "GRM_NEWSLETTER_LIST_ID"]);
}

function brevoHeaders(): HeadersInit {
  return {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "api-key": apiKey(),
  };
}

async function brevo(path: string, init: RequestInit = {}) {
  if (!apiKey()) return { ok: false, status: 500, payload: { error: "brevo_not_configured" } };
  const res = await fetch(`https://api.brevo.com/v3${path}`, {
    ...init,
    headers: {
      ...brevoHeaders(),
      ...(init.headers || {}),
    },
  });
  const text = await res.text();
  let payload: unknown = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (_) {
      payload = { raw: text.slice(0, 1600) };
    }
  }
  return { ok: res.ok, status: res.status, payload };
}

function cleanEmail(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return optionsResponse();
  const ctx = await requireAdmin(req);
  if ("error" in ctx) return ctx.error;

  const url = new URL(req.url);
  const action = url.searchParams.get("action") || "subscribers";
  const limit = clampNumber(url.searchParams.get("limit"), 50, 1, 500);
  const offset = clampNumber(url.searchParams.get("offset"), 0, 0, 100000);

  if (req.method === "GET") {
    if (!apiKey()) return jsonResponse({ error: "brevo_not_configured" }, 500);
    if (action === "health") {
      if (!listId()) {
        return jsonResponse({
          ok: false,
          configured: true,
          list_configured: false,
          error: "brevo_list_not_configured",
        }, 500);
      }
      const [list, contacts, campaigns] = await Promise.all([
        brevo(`/contacts/lists/${encodeURIComponent(listId())}`),
        brevo(`/contacts/lists/${encodeURIComponent(listId())}/contacts?limit=1&offset=0`),
        brevo(`/emailCampaigns?type=classic&limit=5&offset=0&sort=desc`),
      ]);
      return jsonResponse({
        ok: list.ok && contacts.ok && campaigns.ok,
        configured: true,
        list_configured: true,
        list_id: listId(),
        list: list.payload,
        contacts: contacts.payload,
        campaigns: campaigns.payload,
        statuses: {
          list: list.status,
          contacts: contacts.status,
          campaigns: campaigns.status,
        },
      }, list.ok && contacts.ok && campaigns.ok ? 200 : 502);
    }
    if (action === "subscribers") {
      if (!listId()) return jsonResponse({ error: "brevo_list_not_configured" }, 500);
      const res = await brevo(`/contacts/lists/${encodeURIComponent(listId())}/contacts?limit=${limit}&offset=${offset}`);
      if (!res.ok) return jsonResponse({ error: "brevo_request_failed", status: res.status, details: res.payload }, 502);
      return jsonResponse(res.payload);
    }
    if (action === "campaigns") {
      const res = await brevo(`/emailCampaigns?type=classic&limit=${Math.min(limit, 100)}&offset=${offset}&sort=desc`);
      if (!res.ok) return jsonResponse({ error: "brevo_request_failed", status: res.status, details: res.payload }, 502);
      return jsonResponse(res.payload);
    }
    if (action === "summary") {
      if (!listId()) return jsonResponse({ error: "brevo_list_not_configured" }, 500);
      const [contacts, campaigns] = await Promise.all([
        brevo(`/contacts/lists/${encodeURIComponent(listId())}/contacts?limit=1&offset=0`),
        brevo(`/emailCampaigns?type=classic&limit=5&offset=0&sort=desc`),
      ]);
      return jsonResponse({ ok: contacts.ok && campaigns.ok, contacts: contacts.payload, campaigns: campaigns.payload });
    }
    return jsonResponse({ error: "unknown_action" }, 400);
  }

  if (req.method !== "POST") return jsonResponse({ error: "method_not_allowed" }, 405);
  if (!apiKey()) return jsonResponse({ error: "brevo_not_configured" }, 500);
  if (!listId()) return jsonResponse({ error: "brevo_list_not_configured" }, 500);

  const body = await readJsonBody(req);
  const postAction = String(body.action || "").trim();
  const email = cleanEmail(body.email);
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return jsonResponse({ error: "invalid_email" }, 400);
  }

  if (postAction === "subscribe") {
    const res = await brevo("/contacts", {
      method: "POST",
      body: JSON.stringify({
        email,
        listIds: [Number(listId())],
        updateEnabled: true,
      }),
    });
    await audit(ctx, "brevo.subscribe", { status: res.status }, "brevo.contact", email);
    if (!res.ok) return jsonResponse({ error: "brevo_request_failed", status: res.status, details: res.payload }, 502);
    return jsonResponse({ ok: true, contact: res.payload });
  }

  if (postAction === "remove_from_list") {
    const res = await brevo(`/contacts/lists/${encodeURIComponent(listId())}/contacts/remove`, {
      method: "POST",
      body: JSON.stringify({ emails: [email] }),
    });
    await audit(ctx, "brevo.remove_from_list", { status: res.status }, "brevo.contact", email);
    if (!res.ok) return jsonResponse({ error: "brevo_request_failed", status: res.status, details: res.payload }, 502);
    return jsonResponse({ ok: true, process: res.payload });
  }

  return jsonResponse({ error: "unknown_action" }, 400);
});
