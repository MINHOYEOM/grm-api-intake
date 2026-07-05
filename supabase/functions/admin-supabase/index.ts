import {
  audit,
  clampNumber,
  jsonResponse,
  optionsResponse,
  readJsonBody,
  requireAdmin,
} from "../_shared/admin.ts";

function publicUser(user: Record<string, unknown>) {
  return {
    id: user.id,
    email: user.email,
    created_at: user.created_at,
    confirmed_at: user.confirmed_at,
    email_confirmed_at: user.email_confirmed_at,
    last_sign_in_at: user.last_sign_in_at,
    banned_until: user.banned_until,
  };
}

async function listUsers(ctx: Awaited<ReturnType<typeof requireAdmin>>, limit: number) {
  if ("error" in ctx) return { users: [], count: 0 };
  const { data, error } = await ctx.supabase.auth.admin.listUsers({ page: 1, perPage: limit });
  if (error) throw error;
  const users = (data?.users || []).map((u: unknown) => publicUser(u as Record<string, unknown>));
  return { users, count: users.length };
}

async function readDispatches(ctx: Awaited<ReturnType<typeof requireAdmin>>) {
  if ("error" in ctx) return [];
  const { data, error } = await ctx.supabase
    .from("newsletter_dispatch_log")
    .select("id,publish_date,mode,workflow,ref,github_status,github_run_url,github_run_id,github_run_status,github_run_conclusion,created_at")
    .order("created_at", { ascending: false })
    .limit(20);
  if (error) return [];
  return data || [];
}

async function readAudit(ctx: Awaited<ReturnType<typeof requireAdmin>>) {
  if ("error" in ctx) return [];
  const { data, error } = await ctx.supabase
    .from("admin_audit_log")
    .select("id,action,target_type,target_id,details,created_at")
    .order("created_at", { ascending: false })
    .limit(40);
  if (error) return [];
  return data || [];
}

async function readReactions(ctx: Awaited<ReturnType<typeof requireAdmin>>) {
  if ("error" in ctx) return { totals: {}, topCards: [] };
  const { data, error } = await ctx.supabase
    .from("reaction")
    .select("card_id,kind,created_at")
    .limit(5000);
  if (error || !data) return { totals: {}, topCards: [] };

  const totals: Record<string, number> = {};
  const byCard: Record<string, { card_id: string; heart: number; scrap: number; total: number }> = {};
  for (const row of data as Array<{ card_id?: string; kind?: string }>) {
    const kind = row.kind || "unknown";
    const cardId = row.card_id || "";
    totals[kind] = (totals[kind] || 0) + 1;
    if (!cardId) continue;
    byCard[cardId] ||= { card_id: cardId, heart: 0, scrap: 0, total: 0 };
    if (kind === "heart") byCard[cardId].heart += 1;
    if (kind === "scrap") byCard[cardId].scrap += 1;
    byCard[cardId].total += 1;
  }
  const topCards = Object.values(byCard)
    .sort((a, b) => b.total - a.total || a.card_id.localeCompare(b.card_id))
    .slice(0, 20);
  return { totals, topCards };
}

async function health(ctx: Awaited<ReturnType<typeof requireAdmin>>) {
  if ("error" in ctx) return { ok: false };
  const checks = [];
  const adminRows = await ctx.supabase
    .from("admin_user")
    .select("user_id", { count: "exact", head: true })
    .is("revoked_at", null);
  checks.push({
    name: "admin_user",
    ok: !adminRows.error && (adminRows.count || 0) > 0,
    count: adminRows.count || 0,
    error: adminRows.error?.message || null,
  });

  const dispatchRows = await ctx.supabase
    .from("newsletter_dispatch_log")
    .select("id", { count: "exact", head: true });
  checks.push({
    name: "newsletter_dispatch_log",
    ok: !dispatchRows.error,
    count: dispatchRows.count || 0,
    error: dispatchRows.error?.message || null,
  });

  const auditRows = await ctx.supabase
    .from("admin_audit_log")
    .select("id", { count: "exact", head: true });
  checks.push({
    name: "admin_audit_log",
    ok: !auditRows.error,
    count: auditRows.count || 0,
    error: auditRows.error?.message || null,
  });

  const reactionRows = await ctx.supabase
    .from("reaction")
    .select("user_id", { count: "exact", head: true });
  checks.push({
    name: "reaction",
    ok: !reactionRows.error,
    count: reactionRows.count || 0,
    error: reactionRows.error?.message || null,
  });

  return {
    ok: checks.every((check) => check.ok),
    user: publicUser(ctx.user as Record<string, unknown>),
    admin: ctx.admin,
    checks,
  };
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return optionsResponse();

  const ctx = await requireAdmin(req);
  if ("error" in ctx) return ctx.error;

  const url = new URL(req.url);
  const action = url.searchParams.get("action") || "overview";
  const limit = clampNumber(url.searchParams.get("limit"), 100, 1, 1000);

  try {
    if (req.method === "GET" && action === "me") {
      return jsonResponse({ ok: true, user: publicUser(ctx.user as Record<string, unknown>), admin: ctx.admin });
    }

    if (req.method === "GET" && action === "health") {
      return jsonResponse(await health(ctx));
    }

    if (req.method === "GET" && action === "users") {
      const users = await listUsers(ctx, limit);
      return jsonResponse({ ok: true, ...users });
    }

    if (req.method === "GET" && action === "logs") {
      const [dispatches, auditRows] = await Promise.all([readDispatches(ctx), readAudit(ctx)]);
      return jsonResponse({ ok: true, dispatches, audit: auditRows });
    }

    if (req.method === "GET" && action === "reactions") {
      return jsonResponse({ ok: true, reactions: await readReactions(ctx) });
    }

    if (req.method === "GET" && action === "overview") {
      const [users, dispatches, auditRows, reactions] = await Promise.all([
        listUsers(ctx, limit),
        readDispatches(ctx),
        readAudit(ctx),
        readReactions(ctx),
      ]);
      return jsonResponse({
        ok: true,
        counts: { users: users.count },
        users: users.users,
        dispatches,
        audit: auditRows,
        reactions,
      });
    }

    if (req.method === "POST") {
      const body = await readJsonBody(req);
      const postAction = String(body.action || "").trim();
      const userId = String(body.user_id || "").trim();
      if (!userId) return jsonResponse({ error: "missing_user_id" }, 400);
      if (postAction === "ban_user") {
        if (userId === ctx.user.id) return jsonResponse({ error: "cannot_ban_self" }, 400);
        const { data, error } = await ctx.supabase.auth.admin.updateUserById(userId, {
          ban_duration: String(body.duration || "876000h"),
        });
        if (error) return jsonResponse({ error: "user_action_failed", message: error.message }, 502);
        await audit(ctx, "user.ban", { duration: body.duration || "876000h" }, "auth.users", userId);
        return jsonResponse({ ok: true, user: data?.user ? publicUser(data.user as unknown as Record<string, unknown>) : null });
      }
      if (postAction === "unban_user") {
        const { data, error } = await ctx.supabase.auth.admin.updateUserById(userId, {
          ban_duration: "none",
        });
        if (error) return jsonResponse({ error: "user_action_failed", message: error.message }, 502);
        await audit(ctx, "user.unban", {}, "auth.users", userId);
        return jsonResponse({ ok: true, user: data?.user ? publicUser(data.user as unknown as Record<string, unknown>) : null });
      }
      if (postAction === "confirm_user") {
        const { data, error } = await ctx.supabase.auth.admin.updateUserById(userId, {
          email_confirm: true,
        });
        if (error) return jsonResponse({ error: "user_action_failed", message: error.message }, 502);
        await audit(ctx, "user.confirm_email", {}, "auth.users", userId);
        return jsonResponse({ ok: true, user: data?.user ? publicUser(data.user as unknown as Record<string, unknown>) : null });
      }
      return jsonResponse({ error: "unknown_action" }, 400);
    }
  } catch (error) {
    return jsonResponse({ error: "admin_supabase_failed", message: String((error as Error).message || error) }, 502);
  }

  return jsonResponse({ error: "method_not_allowed" }, 405);
});
