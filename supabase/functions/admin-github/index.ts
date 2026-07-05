import {
  audit,
  envAny,
  jsonResponse,
  optionsResponse,
  readJsonBody,
  requireAdmin,
} from "../_shared/admin.ts";

type WorkflowDef = {
  label: string;
  workflow: string;
  inputs?: Record<string, string | boolean>;
};

type WorkflowRun = {
  id?: number;
  status?: string;
  conclusion?: string | null;
  head_branch?: string;
  event?: string;
  created_at?: string;
  updated_at?: string;
  html_url?: string;
};

function repoName(): string {
  return envAny(["GITHUB_REPO", "GITHUB_REPOSITORY"]) || "MINHOYEOM/grm-api-intake";
}

function githubToken(): string {
  return envAny(["GITHUB_ACTIONS_TOKEN", "GITHUB_TOKEN"]);
}

function gitRef(): string {
  return envAny(["GITHUB_REF", "GITHUB_BRANCH"]) || "main";
}

function workflowMap(publishDate = ""): Record<string, WorkflowDef> {
  return {
    newsletter_send: {
      label: "뉴스레터 실발송",
      workflow: envAny(["NEWSLETTER_WORKFLOW_ID"]) || "grm-newsletter-send.yml",
      inputs: { publish_date: publishDate, mode: "send" },
    },
    web_deploy: {
      label: "웹 재배포",
      workflow: envAny(["WEB_DEPLOY_WORKFLOW_ID"]) || "grm-web-deploy.yml",
      inputs: {},
    },
    intake_run: {
      label: "수집 실행",
      workflow: envAny(["INTAKE_WORKFLOW_ID"]) || "grm-intake.yml",
      inputs: {},
    },
    brief_audit: {
      label: "브리프 감사",
      workflow: envAny(["BRIEF_AUDIT_WORKFLOW_ID"]) || "grm-brief-audit.yml",
      inputs: {},
    },
  };
}

async function githubFetch(path: string, init: RequestInit = {}) {
  const token = githubToken();
  if (!token) return { ok: false, status: 500, payload: { error: "github_not_configured" } };
  const res = await fetch(`https://api.github.com/repos/${repoName()}${path}`, {
    ...init,
    headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "grm-admin-console",
      "X-GitHub-Api-Version": "2022-11-28",
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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function runIsRecent(run: WorkflowRun, startedAt: string): boolean {
  if (!run.created_at) return false;
  const runTime = Date.parse(run.created_at);
  const started = Date.parse(startedAt) - 10000;
  return Number.isFinite(runTime) && Number.isFinite(started) && runTime >= started;
}

async function findLatestWorkflowRun(workflow: string, startedAt: string): Promise<WorkflowRun | null> {
  for (let i = 0; i < 5; i += 1) {
    await sleep(i === 0 ? 700 : 1400);
    const path = `/actions/workflows/${encodeURIComponent(workflow)}/runs?branch=${encodeURIComponent(gitRef())}&event=workflow_dispatch&per_page=10`;
    const res = await githubFetch(path);
    if (!res.ok) continue;
    const runs = (res.payload as Record<string, unknown>).workflow_runs as WorkflowRun[] | undefined;
    const recent = (runs || []).find((run) => runIsRecent(run, startedAt));
    if (recent) return recent;
  }
  return null;
}

async function listRuns() {
  const defs = workflowMap();
  const out: Array<Record<string, unknown>> = [];
  for (const def of Object.values(defs)) {
    const path = `/actions/workflows/${encodeURIComponent(def.workflow)}/runs?per_page=6`;
    const res = await githubFetch(path);
    if (!res.ok) continue;
    const runs = (res.payload as Record<string, unknown>).workflow_runs as Array<Record<string, unknown>> | undefined;
    for (const run of runs || []) {
      out.push({
        workflow_id: def.workflow,
        workflow_name: def.label,
        id: run.id,
        status: run.status,
        conclusion: run.conclusion,
        head_branch: run.head_branch,
        event: run.event,
        created_at: run.created_at,
        updated_at: run.updated_at,
        html_url: run.html_url,
      });
    }
  }
  out.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  return out;
}

async function workflowHealth() {
  const defs = workflowMap();
  const checks = [];
  for (const [action, def] of Object.entries(defs)) {
    const res = await githubFetch(`/actions/workflows/${encodeURIComponent(def.workflow)}`);
    const payload = res.payload as Record<string, unknown>;
    checks.push({
      action,
      label: def.label,
      workflow: def.workflow,
      ok: res.ok,
      status: res.status,
      state: payload.state || null,
      html_url: payload.html_url || null,
    });
  }
  return checks;
}

async function previousSuccessfulNewsletterDispatch(ctx: Awaited<ReturnType<typeof requireAdmin>>, publishDate: string) {
  if ("error" in ctx) return null;
  const { data, error } = await ctx.supabase
    .from("newsletter_dispatch_log")
    .select("id,publish_date,mode,github_status,github_run_url,github_run_id,created_at")
    .eq("publish_date", publishDate)
    .eq("mode", "send")
    .gte("github_status", 200)
    .lt("github_status", 300)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) return null;
  return data;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return optionsResponse();
  const ctx = await requireAdmin(req);
  if ("error" in ctx) return ctx.error;

  const url = new URL(req.url);
  if (req.method === "GET") {
    const action = url.searchParams.get("action") || "runs";
    if (action === "health") {
      if (!githubToken()) {
        return jsonResponse({
          ok: false,
          error: "github_not_configured",
          repo: repoName(),
          ref: gitRef(),
          configured: false,
          workflows: [],
        }, 500);
      }
      const workflows = await workflowHealth();
      return jsonResponse({
        ok: workflows.every((item) => item.ok),
        repo: repoName(),
        ref: gitRef(),
        configured: true,
        workflows,
      });
    }
    if (action !== "runs") return jsonResponse({ error: "unknown_action" }, 400);
    if (!githubToken()) return jsonResponse({ error: "github_not_configured", runs: [] }, 500);
    return jsonResponse({ ok: true, repo: repoName(), ref: gitRef(), runs: await listRuns() });
  }

  if (req.method !== "POST") return jsonResponse({ error: "method_not_allowed" }, 405);

  const body = await readJsonBody(req);
  const action = String(body.action || "").trim();
  const publishDate = String(body.publish_date || "").trim();
  const force = body.force === true || String(body.force || "").toLowerCase() === "true";
  const defs = workflowMap(publishDate);
  const def = defs[action];
  if (!def) return jsonResponse({ error: "unknown_action" }, 400);
  if (action === "newsletter_send" && !/^\d{4}-\d{2}-\d{2}$/.test(publishDate)) {
    return jsonResponse({ error: "invalid_publish_date" }, 400);
  }
  if (!githubToken()) return jsonResponse({ error: "github_not_configured" }, 500);

  if (action === "newsletter_send" && !force) {
    const previous = await previousSuccessfulNewsletterDispatch(ctx, publishDate);
    if (previous) {
      return jsonResponse({
        error: "newsletter_already_dispatched",
        existing: previous,
      }, 409);
    }
  }

  const startedAt = new Date().toISOString();
  const dispatch = await githubFetch(`/actions/workflows/${encodeURIComponent(def.workflow)}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: gitRef(), inputs: def.inputs || {} }),
  });
  const run = dispatch.ok ? await findLatestWorkflowRun(def.workflow, startedAt) : null;
  const workflowUrl = run?.html_url || `https://github.com/${repoName()}/actions/workflows/${def.workflow}`;

  await audit(ctx, "github.dispatch", {
    action,
    workflow: def.workflow,
    ref: gitRef(),
    status: dispatch.status,
    run_id: run?.id || null,
    run_url: run?.html_url || null,
    response: dispatch.payload,
  }, "github.workflow", def.workflow);

  if (action === "newsletter_send") {
    await ctx.supabase.from("newsletter_dispatch_log").insert({
      actor_user_id: ctx.user.id,
      publish_date: publishDate,
      mode: "send",
      workflow: def.workflow,
      ref: gitRef(),
      github_status: dispatch.status,
      github_run_url: workflowUrl,
      github_run_id: run?.id || null,
      github_run_status: run?.status || null,
      github_run_conclusion: run?.conclusion || null,
      github_response: dispatch.payload,
    });
  }

  if (!dispatch.ok) {
    return jsonResponse({
      error: "github_dispatch_failed",
      status: dispatch.status,
      details: dispatch.payload,
    }, 502);
  }

  return jsonResponse({
    ok: true,
    action,
    label: def.label,
    workflow: def.workflow,
    repo: repoName(),
    ref: gitRef(),
    github_status: dispatch.status,
    github_run_id: run?.id || null,
    github_run_status: run?.status || null,
    github_run_conclusion: run?.conclusion || null,
    github_run_url: workflowUrl,
  });
});
