// app.js - Frontend logic. Talks to the FastAPI backend with fetch().

const $ = (sel) => document.querySelector(sel);
const rupees = (n) => (n == null ? "Not found" : "Rs " + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 }));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- Tabs ----------
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => { t.classList.remove("active"); t.setAttribute("aria-selected", "false"); });
    tab.classList.add("active"); tab.setAttribute("aria-selected", "true");
    document.querySelectorAll(".view").forEach((v) => (v.hidden = true));
    $("#view-" + tab.dataset.view).hidden = false;
    if (tab.dataset.view === "review") loadDashboard();
  });
});

// ---------- Receipt card (used by both views) ----------
function receiptHTML(c) {
  const flags = c.flags.length
    ? `<ul class="flags">${c.flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`
    : `<p class="clean">All checks passed</p>`;
  const items = c.items.length ? `<ul>${c.items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul><hr>` : "";
  const conf = c.ocr_confidence != null ? ` · scan ${Math.round(c.ocr_confidence * 100)}%` : "";
  return `
    <article class="receipt reveal">
      <span class="stamp ${c.status}">${c.status.toUpperCase()}</span>
      <div class="vendor">${esc(c.vendor || "Unknown vendor")}</div>
      <div class="sub">Claim #${c.id} · ${esc(c.employee_name)}</div>
      <hr>
      <div class="row"><span>Date</span><span>${esc(c.bill_date || "Not found")}</span></div>
      <div class="row"><span>Category</span><span>${esc(c.category)}</span></div>
      <p class="reason">${esc(c.ai_reason)}${conf}</p>
      <hr>
      ${items}
      <div class="row total"><span>Total</span><span>${rupees(c.amount)}</span></div>
      <hr>
      ${flags}
      ${c.hr_comment ? `<hr><p class="reason">HR: ${esc(c.hr_comment)}</p>` : ""}
    </article>`;
}

// ---------- Employee: upload ----------
const fileInput = $("#file");
const dropzone = $("#dropzone");
fileInput.addEventListener("change", () => {
  $("#drop-text").textContent = fileInput.files[0] ? fileInput.files[0].name : "Drop a JPG, PNG or PDF here, or click to choose";
});
["dragover", "dragenter"].forEach((e) => dropzone.addEventListener(e, (ev) => { ev.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach((e) => dropzone.addEventListener(e, () => dropzone.classList.remove("drag")));
dropzone.addEventListener("drop", (ev) => {
  ev.preventDefault();
  if (ev.dataTransfer.files.length) { fileInput.files = ev.dataTransfer.files; fileInput.dispatchEvent(new Event("change")); }
});

$("#upload-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const btn = $("#scan-btn");
  $("#upload-error").textContent = "";
  btn.disabled = true; btn.textContent = "Scanning…";
  try {
    const res = await fetch("/api/claims", { method: "POST", body: new FormData(ev.target) });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "Upload failed");
    $("#result").innerHTML = receiptHTML(body);
    fileInput.value = ""; fileInput.dispatchEvent(new Event("change"));
  } catch (err) {
    $("#upload-error").textContent = err.message;
  } finally {
    btn.disabled = false; btn.textContent = "Scan bill";
  }
});

// ---------- HR: dashboard ----------
async function loadDashboard() {
  const status = $("#status-filter").value;
  const [summary, claims] = await Promise.all([
    fetch("/api/summary").then((r) => r.json()),
    fetch("/api/claims" + (status ? `?status=${status}` : "")).then((r) => r.json()),
  ]);

  $("#stats").innerHTML = `
    <div class="stat"><b>${summary.pending}</b><span>Waiting for review</span></div>
    <div class="stat"><b>${summary.flagged_pending}</b><span>Waiting, with warnings</span></div>
    <div class="stat"><b>${rupees(summary.approved_amount)}</b><span>Approved so far</span></div>
    <div class="stat"><b>${summary.pending_rebalances}</b><span>Limit changes to review</span></div>`;

  $("#claims-body").innerHTML = claims.length
    ? claims.map((c) => `
      <tr data-id="${c.id}" tabindex="0">
        <td>${c.id}</td>
        <td>${esc(c.employee_name)}</td>
        <td>${esc(c.vendor || "Unknown")}</td>
        <td>${esc(c.category)}</td>
        <td class="num">${rupees(c.amount)}</td>
        <td>${c.flags.length ? `<span class="pill warn">${c.flags.length} warning${c.flags.length > 1 ? "s" : ""}</span>` : `<span class="pill ok">Clean</span>`}</td>
        <td><span class="pill ${c.status}">${c.status}</span></td>
      </tr>`).join("")
    : `<tr><td colspan="7" class="empty-row">No claims here yet. Submit a bill from the other tab to see it appear.</td></tr>`;

  document.querySelectorAll("#claims-body tr[data-id]").forEach((row) => {
    row.addEventListener("click", () => openClaim(row.dataset.id));
    row.addEventListener("keydown", (e) => { if (e.key === "Enter") openClaim(row.dataset.id); });
  });

  await loadHrMoves(); // limit change requests (Smart Benefit Rebalancer)
}
$("#status-filter").addEventListener("change", loadDashboard);

// ---------- HR: claim detail ----------
let currentClaim = null;
const dialog = $("#claim-dialog");

async function openClaim(id) {
  const c = await fetch(`/api/claims/${id}`).then((r) => r.json());
  currentClaim = c;
  const url = `/api/claims/${id}/bill`;
  $("#bill-preview").innerHTML = c.content_type === "application/pdf"
    ? `<iframe src="${url}" title="Bill PDF"></iframe>`
    : `<img src="${url}" alt="Uploaded bill for claim ${id}">`;
  $("#dialog-receipt").innerHTML = receiptHTML(c);
  $("#hr-comment").value = "";
  $("#decision-error").textContent = "";
  const pending = c.status === "pending";
  $("#approve-btn").hidden = !pending;
  $("#reject-btn").hidden = !pending;
  $("#hr-comment").disabled = !pending;
  dialog.showModal();
}

async function decide(status) {
  $("#decision-error").textContent = "";
  const res = await fetch(`/api/claims/${currentClaim.id}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, comment: $("#hr-comment").value }),
  });
  const body = await res.json();
  if (!res.ok) { $("#decision-error").textContent = body.detail; return; }
  dialog.close();
  loadDashboard();
}
$("#approve-btn").addEventListener("click", () => decide("approved"));
$("#reject-btn").addEventListener("click", () => decide("rejected"));
$("#close-dialog").addEventListener("click", () => dialog.close());

// ================= My benefits + Smart Benefit Rebalancer =================
let benefitEmployee = "";
let balanceByCat = {};

// Reuse the name typed on the bill tab
document.querySelector('.tab[data-view="benefits"]').addEventListener("click", () => {
  if (!$("#benefit-employee").value && $("#employee").value) $("#benefit-employee").value = $("#employee").value;
});

$("#lookup-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  benefitEmployee = $("#benefit-employee").value.trim();
  loadBenefits();
});

async function loadBenefits() {
  if (!benefitEmployee) return;
  const [bal, moves] = await Promise.all([
    fetch(`/api/benefits?employee=${encodeURIComponent(benefitEmployee)}`).then((r) => r.json()),
    fetch(`/api/rebalance?employee=${encodeURIComponent(benefitEmployee)}`).then((r) => r.json()),
  ]);
  $("#benefits-area").hidden = false;
  const monthName = new Date(bal.month + "-01").toLocaleString("en-IN", { month: "long", year: "numeric" });
  $("#balance-title").textContent = `${monthName}`;
  $("#total-budget").textContent = `Total budget ${rupees(bal.total_budget)} (never changes)`;

  balanceByCat = {};
  $("#balance-list").innerHTML = bal.categories.map((c) => {
    balanceByCat[c.category] = c;
    const usedPct = c.limit ? Math.min(100, (c.used / c.limit) * 100) : 0;
    const heldPct = c.limit ? Math.min(100 - usedPct, (c.pending_out / c.limit) * 100) : 0;
    const diff = c.limit - c.default_limit;
    const changed = diff !== 0 ? ` <span class="changed">(${diff > 0 ? "+" : ""}${rupees(diff)} moved)</span>` : "";
    const notes = [
      c.pending_out ? `${rupees(c.pending_out)} waiting to move out` : "",
      c.pending_in ? `${rupees(c.pending_in)} waiting to move in` : "",
    ].filter(Boolean).join(", ");
    return `
      <div class="balance-row">
        <div class="cat">${esc(c.category)}<small>Limit ${rupees(c.limit)}${changed}</small></div>
        <div>
          <div class="bar" role="img" aria-label="${esc(c.category)}: ${rupees(c.used)} used of ${rupees(c.limit)}">
            <span class="used" style="width:${usedPct}%"></span><span class="held" style="width:${heldPct}%"></span>
          </div>
          ${notes ? `<p class="hint" style="margin:.35rem 0 0">${notes}</p>` : ""}
        </div>
        <div class="left"><b>${rupees(c.available)}</b><span>left of ${rupees(c.limit)}</span></div>
      </div>`;
  }).join("");

  // Fill the "from" / "to" dropdowns
  const cats = bal.categories.map((c) => c.category);
  const keepFrom = $("#from-cat").value, keepTo = $("#to-cat").value;
  $("#from-cat").innerHTML = cats.map((c) => `<option>${esc(c)}</option>`).join("");
  $("#to-cat").innerHTML = cats.map((c) => `<option>${esc(c)}</option>`).join("");
  $("#from-cat").value = cats.includes(keepFrom) ? keepFrom : bal.categories.reduce((a, b) => (b.available > a.available ? b : a)).category;
  $("#to-cat").value = cats.includes(keepTo) && keepTo !== $("#from-cat").value ? keepTo : cats.find((c) => c !== $("#from-cat").value);
  updateMoveHint();

  $("#my-moves").innerHTML = moves.length
    ? moves.map((m) => `
      <li>
        <div class="move-top">
          <span class="move-what">${rupees(m.amount)}: ${esc(m.from_category)} to ${esc(m.to_category)}</span>
          <span class="pill ${m.status}">${m.status}</span>
        </div>
        <p class="move-why">${esc(m.reason)}</p>
        ${m.hr_comment ? `<p class="move-why">HR: ${esc(m.hr_comment)}</p>` : ""}
      </li>`).join("")
    : `<li class="empty-note">No requests yet. When you move money between categories, it shows up here.</li>`;
}

function updateMoveHint() {
  const c = balanceByCat[$("#from-cat").value];
  if (!c) return;
  $("#move-hint").textContent = `You can move up to ${rupees(c.available)} from ${c.category}.`;
  $("#move-amount").max = c.available;
}
$("#from-cat").addEventListener("change", updateMoveHint);

$("#move-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("#move-error").textContent = "";
  const btn = $("#move-btn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/rebalance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        employee_name: benefitEmployee,
        from_category: $("#from-cat").value,
        to_category: $("#to-cat").value,
        amount: Number($("#move-amount").value),
        reason: $("#move-reason").value,
      }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Check the amount and reason, then try again.");
    $("#move-amount").value = ""; $("#move-reason").value = "";
    await loadBenefits();
  } catch (err) {
    $("#move-error").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

// ---------- HR side: limit change requests ----------
async function loadHrMoves() {
  const moves = await fetch("/api/rebalance?status=pending").then((r) => r.json());
  $("#hr-moves").innerHTML = moves.length
    ? moves.map((m) => `
      <li data-id="${m.id}">
        <div class="move-top">
          <span class="move-what">${esc(m.employee_name)} wants to move ${rupees(m.amount)} from ${esc(m.from_category)} to ${esc(m.to_category)}</span>
          <span class="pill pending">pending</span>
        </div>
        <p class="move-why">${esc(m.reason)}</p>
        ${m.available_now < m.amount ? `<p class="warn-text">Only ${rupees(m.available_now)} is free in ${esc(m.from_category)} now, so this can't be approved.</p>` : ""}
        <div class="hr-actions">
          <input placeholder="Comment (required to reject)" aria-label="Comment for request ${m.id}">
          <button type="button" class="danger" data-act="rejected">Reject</button>
          <button type="button" class="primary" data-act="approved">Approve</button>
        </div>
        <p class="error"></p>
      </li>`).join("")
    : `<li class="empty-note">No limit change requests are waiting.</li>`;

  document.querySelectorAll("#hr-moves li[data-id]").forEach((li) => {
    li.querySelectorAll("button[data-act]").forEach((btn) => btn.addEventListener("click", async () => {
      const res = await fetch(`/api/rebalance/${li.dataset.id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: btn.dataset.act, comment: li.querySelector("input").value }),
      });
      const body = await res.json();
      if (!res.ok) { li.querySelector(".error").textContent = body.detail; return; }
      loadDashboard();
    }));
  });
}
