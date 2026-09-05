"use strict";

const $ = (sel) => document.querySelector(sel);
const form = $("#qa-form");
const runBtn = $("#run");
const resetBtn = $("#reset");
const progressEl = $("#progress");
const errorEl = $("#error");
const errorBody = $("#error-body");
const reportEl = $("#report");
const statusEl = $("#status");
const statusLabel = statusEl.querySelector(".label");
const elapsedEl = $("#progress-elapsed");

const SEVERITY_CLASS = { Critical: "critical", Minor: "minor", Info: "info" };

let elapsedTimer = null;

function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }

function setStatus(state, text) {
  statusEl.classList.remove("status--ok", "status--bad", "status--unknown");
  statusEl.classList.add(`status--${state}`);
  statusLabel.textContent = text;
}

async function refreshHealth() {
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    $("#be-url").textContent = location.origin;
    // Two backends answer this: the local Starlette server (which reports MCP
    // reachability) and the AWS Lambda API (which runs the pipeline directly
    // and has no MCP server to reach).
    if (j.mode === "pipeline" && !("mcp_reachable" in j)) {
      $("#mcp-url").textContent = `lambda • ${j.model || "model unset"}`;
      setStatus("ok", "pipeline ready");
    } else if (j.mcp_reachable) {
      $("#mcp-url").textContent = j.mcp_url || "(unset)";
      setStatus("ok", `MCP reachable (${j.mcp_status})`);
    } else {
      $("#mcp-url").textContent = j.mcp_url || "(unset)";
      setStatus("bad", `MCP unreachable: ${j.mcp_status}`);
    }
  } catch (err) {
    setStatus("bad", `health failed: ${err}`);
  }
}

function startTimer() {
  const t0 = Date.now();
  elapsedEl.textContent = "elapsed: 0s";
  elapsedTimer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    elapsedEl.textContent = `elapsed: ${s}s`;
  }, 1000);
}
function stopTimer() {
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = null;
}

function severityCounts(issues) {
  const c = { Critical: 0, Minor: 0, Info: 0, Other: 0 };
  for (const i of issues) {
    const s = i.severity || "Other";
    if (s in c) c[s]++; else c.Other++;
  }
  return c;
}

function renderIssue(issue, index) {
  const el = document.createElement("article");
  el.className = `issue ${SEVERITY_CLASS[issue.severity] || "info"}`;

  const head = document.createElement("div");
  head.className = "issue__head";
  head.innerHTML = `
    <span class="issue__type">${index + 1}. ${escapeHtml(issue.type || "Issue")}</span>
    <span class="tag ${SEVERITY_CLASS[issue.severity] || "info"}">${escapeHtml(issue.severity || "Info")}</span>
    ${issue.ruleId ? `<span class="tag">rule ${escapeHtml(issue.ruleId)}</span>` : ""}
  `;
  el.appendChild(head);

  if (issue.description) {
    const f = document.createElement("p");
    f.className = "issue__field";
    f.innerHTML = `<b>Description:</b> ${escapeHtml(issue.description)}`;
    el.appendChild(f);
  }
  if (issue.excerpt) {
    const f = document.createElement("p");
    f.className = "issue__field issue__excerpt";
    f.textContent = `“${issue.excerpt}”`;
    el.appendChild(f);
  }
  if (issue.suggestion) {
    const f = document.createElement("p");
    f.className = "issue__field";
    f.innerHTML = `<b>Suggestion:</b> ${escapeHtml(issue.suggestion)}`;
    el.appendChild(f);
  }
  if (issue.screenshot && /^[A-Za-z0-9+/=]+$/.test(issue.screenshot.slice(0, 40))) {
    // Caption first — for reference-diff findings the image shows the
    // REFERENCE page (what to add), and the reviewer must know that before
    // reading the crop as the page under review.
    if (issue.screenshot_caption) {
      const cap = document.createElement("p");
      cap.className = "issue__field muted";
      cap.textContent = issue.screenshot_caption;
      el.appendChild(cap);
    }
    const img = document.createElement("img");
    img.className = "issue__shot";
    img.alt = issue.screenshot_caption
      || `Evidence for: ${issue.excerpt || issue.description || ""}`;
    img.loading = "lazy";
    img.src = `data:image/png;base64,${issue.screenshot}`;
    el.appendChild(img);
  }
  return el;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderReport(payload) {
  const { report, json_url, pdf_url, pdf_error } = payload;
  $("#report-title").textContent = report.course_name || "QA Report";
  const meta = [];
  if (report.url) meta.push(report.url);
  if (report.generated_at) meta.push(`generated ${report.generated_at}`);
  if (report.template_summary) meta.push(`template: ${report.template_summary}`);
  $("#report-meta").textContent = meta.join(" • ");

  const issues = report.issues || [];
  const counts = severityCounts(issues);
  const summary = $("#report-summary");
  summary.innerHTML = `
    <span class="pill"><strong>${issues.length}</strong> total</span>
    <span class="pill critical"><strong>${counts.Critical}</strong> critical</span>
    <span class="pill minor"><strong>${counts.Minor}</strong> minor</span>
    <span class="pill info"><strong>${counts.Info}</strong> info</span>
  `;

  const failures = report.tool_failures || [];
  const failuresEl = $("#report-failures");
  if (failures.length) {
    failuresEl.innerHTML = `
      <h3>Tool failures (${failures.length})</h3>
      <ul>${failures.map(f => `<li>${escapeHtml(f)}</li>`).join("")}</ul>
    `;
    show(failuresEl);
  } else {
    hide(failuresEl);
  }

  const reasoningEl = $("#report-reasoning");
  const reasoning = report.reasoning;
  if (reasoning && typeof reasoning === "object") {
    const verdict = String(reasoning.verdict || "").toUpperCase();
    const verdictEl = $("#reasoning-verdict");
    verdictEl.textContent = verdict || "—";
    verdictEl.className = `verdict verdict--${(verdict || "partial").toLowerCase()}`;
    $("#reasoning-summary").textContent = reasoning.summary || "(no summary)";
    const followed = $("#reasoning-followed");
    const gaps = $("#reasoning-gaps");
    followed.innerHTML = (reasoning.instructions_followed || [])
      .map(s => `<li>${escapeHtml(s)}</li>`).join("");
    gaps.innerHTML = (reasoning.gaps || [])
      .map(s => `<li>${escapeHtml(s)}</li>`).join("");
    show(reasoningEl);
  } else {
    hide(reasoningEl);
  }

  const issuesEl = $("#report-issues");
  issuesEl.innerHTML = "";
  if (!issues.length) {
    issuesEl.innerHTML = `<p class="muted">No issues reported.</p>`;
  } else {
    issues.forEach((iss, i) => issuesEl.appendChild(renderIssue(iss, i)));
  }

  const dlJson = $("#dl-json");
  const dlPdf = $("#dl-pdf");
  if (json_url) { dlJson.href = json_url; dlJson.classList.remove("hidden"); }
  else { dlJson.classList.add("hidden"); }
  if (pdf_url) { dlPdf.href = pdf_url; dlPdf.classList.remove("hidden"); dlPdf.textContent = "Download PDF"; }
  else if (pdf_error) { dlPdf.classList.add("hidden"); }
  else { dlPdf.classList.add("hidden"); }

  show(reportEl);
}

function pickedFile(id) {
  const input = document.querySelector(`#${id}`);
  return input && input.files && input.files.length ? input.files[0] : null;
}

async function readJson(r) {
  const text = await r.text();
  try { return JSON.parse(text); } catch { return { _raw: text }; }
}

function failure(r, json) {
  return new Error(
    (json && (json.detail || json.error || json._raw)) ||
    `${r.status} ${r.statusText}`
  );
}

/** Local Starlette backend: one blocking multipart POST that returns the report. */
async function runDirect() {
  const fd = new FormData(form);
  for (const name of ["template_document", "spec_document"]) {
    if (!pickedFile(name)) fd.delete(name);
  }
  const r = await fetch("/api/qa", { method: "POST", body: fd });
  const json = await readJson(r);
  if (!r.ok) throw failure(r, json);
  return json;
}

/**
 * AWS backend: uploads go straight to S3 on presigned URLs, the run happens in
 * a worker Lambda, and we poll for the outcome. Nothing large crosses the API
 * function, so a report full of screenshots is no problem.
 */
async function runOnAws(presigned) {
  const files = {
    template: pickedFile("template_document"),
    spec: pickedFile("spec_document"),
  };

  for (const [slot, target] of Object.entries(presigned.uploads || {})) {
    const file = files[slot];
    if (!file) continue;
    const put = await fetch(target.url, {
      method: "PUT",
      headers: { "content-type": target.content_type },
      body: file,
    });
    if (!put.ok) throw new Error(`upload of the ${slot} document failed (${put.status})`);
  }

  const start = await fetch("/api/qa", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      job_id: presigned.job_id,
      url: form.querySelector("#url").value.trim(),
      reference_url: (form.querySelector("#reference_url") || {}).value?.trim() || "",
      template_text: (form.querySelector("#template_text") || {}).value?.trim() || "",
      template_key: presigned.uploads?.template?.key || "",
      spec_key: presigned.uploads?.spec?.key || "",
    }),
  });
  const started = await readJson(start);
  if (!start.ok) throw failure(start, started);

  // The run takes minutes; poll gently so a long job costs a handful of requests.
  const deadline = Date.now() + 16 * 60 * 1000;
  while (Date.now() < deadline) {
    await new Promise((res) => setTimeout(res, 5000));
    const r = await fetch(`/api/jobs/${started.job_id}`);
    const job = await readJson(r);
    if (!r.ok) throw failure(r, job);
    if (job.status === "error") throw new Error(job.error || "the QA run failed");
    if (job.status === "done") {
      const report = await (await fetch(job.report_url)).json();
      return {
        report,
        json_url: job.json_url,
        pdf_url: job.pdf_url,
        pdf_error: job.pdf_error,
      };
    }
  }
  throw new Error("timed out waiting for the QA run to finish");
}

async function submitForm(ev) {
  ev.preventDefault();
  hide(errorEl);
  hide(reportEl);
  show(progressEl);
  runBtn.disabled = true;
  startTimer();

  try {
    // /api/uploads exists only on the AWS deployment. Its absence is how we
    // detect the local server, so one build of this file serves both.
    const probe = await fetch("/api/uploads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        template_filename: pickedFile("template_document")?.name || "",
        template_size: pickedFile("template_document")?.size || 0,
        spec_filename: pickedFile("spec_document")?.name || "",
        spec_size: pickedFile("spec_document")?.size || 0,
      }),
    });

    let payload;
    if (probe.ok) {
      payload = await runOnAws(await readJson(probe));
    } else if (probe.status === 404 || probe.status === 405) {
      payload = await runDirect();
    } else {
      throw failure(probe, await readJson(probe));
    }
    renderReport(payload);
  } catch (err) {
    errorBody.textContent = err && err.message ? err.message : String(err);
    show(errorEl);
  } finally {
    hide(progressEl);
    runBtn.disabled = false;
    stopTimer();
  }
}

function resetForm() {
  form.reset();
  hide(reportEl);
  hide(errorEl);
}

form.addEventListener("submit", submitForm);
resetBtn.addEventListener("click", resetForm);

refreshHealth();
setInterval(refreshHealth, 15000);
