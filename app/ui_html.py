INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="theme-color" content="#07090d" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="apple-mobile-web-app-title" content="Perplexio" />
  <link rel="manifest" href="/manifest.webmanifest" />
  <link rel="icon" href="/icons/icon.png" type="image/png" />
  <link rel="apple-touch-icon" href="/icons/icon.png" />
  <title>Perplexio</title>
  <style>
    :root {
      --bg: #07090d;
      --bg-soft: #0d1219;
      --panel: #111722;
      --panel-2: #171f2d;
      --line: #243147;
      --ink: #eaf0ff;
      --muted: #93a0bc;
      --accent: #16c79a;
      --accent-2: #0e9f7c;
      --error: #ff7f7f;
      --shadow: 0 24px 70px rgba(0, 0, 0, 0.45);
    }
    * { box-sizing: border-box; }
    body {
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(1200px 800px at 85% -20%, rgba(22, 199, 154, 0.15), transparent 60%),
        radial-gradient(800px 500px at -20% 120%, rgba(91, 132, 255, 0.12), transparent 60%),
        var(--bg);
      color: var(--ink);
    }
    .layout {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: 100vh;
    }
    .sidebar {
      border-right: 1px solid #1c212b;
      background: #0e1116;
      padding: 24px 16px;
      display: flex;
      flex-direction: column;
      gap: 24px;
    }
    .brand {
      font-size: 22px;
      font-weight: 500;
      letter-spacing: -0.5px;
      margin: 0 0 4px 0;
      color: #e5e7eb;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .hint {
      margin: 0;
      color: #6b7280;
      font-size: 13px;
    }
    .panel {
      background: transparent;
      border: none;
      border-radius: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .panel h3 {
      margin: 0;
      font-size: 11px;
      color: #737f94;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }
    .btn-new-chat {
      background: #eaf0ff;
      color: #000;
      border: none;
      border-radius: 20px;
      padding: 10px 16px;
      font-weight: 500;
      font-size: 14px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      transition: opacity 0.2s;
      width: 100%;
    }
    .btn-new-chat:hover { opacity: 0.9; }
    .history, .filelist {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 2px;
      max-height: 280px;
      overflow-y: auto;
      overflow-x: hidden;
    }
    .history::-webkit-scrollbar, .filelist::-webkit-scrollbar { width: 4px; }
    .history::-webkit-scrollbar-thumb, .filelist::-webkit-scrollbar-thumb { background: #2a3342; border-radius: 4px; }
    .history button {
      width: 100%;
      text-align: left;
      padding: 8px 12px;
      background: transparent;
      color: #d1d5db;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      transition: background 0.2s, color 0.2s;
    }
    .history button:hover { background: #1f242d; color: #fff; }
    .filelist li {
      color: #d1d5db;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      padding: 6px 8px;
      border-radius: 8px;
    }
    .filelist li:hover { background: #1f242d; }
    .filelist label {
      display: flex;
      align-items: center;
      gap: 8px;
      width: 100%;
      cursor: pointer;
    }
    .filelist span {
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .main {
      padding: 26px;
      display: grid;
      grid-template-rows: 1fr auto;
      gap: 16px;
    }
    .thread {
      background: linear-gradient(180deg, rgba(16,22,33,0.94), rgba(10,14,22,0.96));
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 18px;
      overflow: auto;
      max-height: calc(100vh - 190px);
    }
    .message {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      margin-bottom: 12px;
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }
    .query {
      font-weight: 600;
      margin-bottom: 10px;
      white-space: pre-wrap;
    }
    .answer {
      line-height: 1.6;
      white-space: pre-wrap;
    }
    .sources {
      margin-top: 12px;
      display: grid;
      gap: 8px;
    }
    .source-card {
      border: 1px solid var(--line);
      background: #0e1522;
      border-radius: 10px;
      padding: 10px;
    }
    .source-card a {
      color: #b8e9dc;
      text-decoration: none;
      font-weight: 600;
      font-size: 13px;
    }
    .source-card p {
      margin: 6px 0 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    .composer {
      border: 1px solid var(--line);
      background: rgba(12, 18, 28, 0.95);
      border-radius: 16px;
      padding: 12px;
      display: grid;
      gap: 10px;
    }
    textarea {
      width: 100%;
      min-height: 74px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #0b111b;
      color: var(--ink);
      padding: 10px;
      font: inherit;
    }
    textarea:focus, input[type="file"]:focus, button:focus {
      outline: 2px solid rgba(22, 199, 154, 0.25);
      outline-offset: 1px;
    }
    .row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }
    input[type="file"] {
      color: var(--muted);
      font-size: 12px;
      max-width: 180px;
    }
    input[type="file"]::file-selector-button {
      background: #1f242d;
      color: #eaf0ff;
      border: 1px solid #2a3342;
      border-radius: 6px;
      padding: 4px 8px;
      margin-right: 8px;
      cursor: pointer;
      font-size: 12px;
      transition: background 0.2s;
    }
    input[type="file"]::file-selector-button:hover { background: #2a3342; }
    .sidebar button.secondary {
      background: transparent;
      color: #9ca3af;
      border: none;
      padding: 4px 8px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.2s, color 0.2s;
    }
    .sidebar button.secondary:hover { background: #1f242d; color: #eaf0ff; }
    .sidebar button.primary-small {
      background: #1f242d;
      color: #eaf0ff;
      border: 1px solid #2a3342;
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 12px;
      cursor: pointer;
      transition: background 0.2s;
    }
    .sidebar button.primary-small:hover { background: #2a3342; }
    button {
      border: 0;
      border-radius: 10px;
      background: linear-gradient(90deg, var(--accent), var(--accent-2));
      color: #04110d;
      padding: 9px 13px;
      cursor: pointer;
      font-weight: 700;
      font-size: 13px;
    }
    button.secondary {
      background: #23304a;
      color: #dbe7ff;
      border: 1px solid #2f4468;
    }
    #uploadStatus {
      font-size: 12px;
      color: var(--muted);
      min-height: 16px;
    }
    #status {
      color: var(--muted);
      font-size: 13px;
      min-height: 18px;
    }
    .error { color: var(--error); }
    .auth-overlay {
      position: fixed;
      inset: 0;
      background: rgba(3, 5, 9, 0.86);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 16px;
    }
    .auth-card {
      width: min(420px, 95vw);
      background: #0f1725;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 16px;
      box-shadow: var(--shadow);
    }
    .auth-card h2 {
      margin: 0 0 8px 0;
      font-size: 20px;
    }
    .auth-card p {
      margin: 0 0 12px 0;
      color: var(--muted);
      font-size: 13px;
    }
    .auth-card input {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #0b111b;
      color: var(--ink);
      padding: 10px;
      font: inherit;
      margin-bottom: 10px;
    }
    @media (max-width: 980px) {
      .layout { grid-template-columns: 1fr; }
      .main { order: 1; padding: 12px; gap: 10px; }
      .sidebar { order: 2; border-right: 0; border-top: 1px solid var(--line); padding: 12px; }
      .thread { max-height: 56vh; border-radius: 14px; padding: 12px; }
      .composer { position: sticky; bottom: 8px; z-index: 10; border-radius: 12px; }
    }
  </style>
</head>
<body>
  <div id="authOverlay" class="auth-overlay">
    <div class="auth-card">
      <h2>Protected Workspace</h2>
      <p>Enter password to access this instance.</p>
      <input id="passwordInput" type="password" placeholder="Password" />
      <div class="row">
        <button onclick="login()">Login</button>
      </div>
      <div id="authStatus" class="hint"></div>
    </div>
  </div>
  <div class="layout">
    <aside class="sidebar">
      <div>
        <h1 class="brand">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 4px; color: #16c79a;"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/></svg>
          Perplexio
        </h1>
        <p class="hint">Search + Grounded QA</p>
      </div>

      <button class="btn-new-chat" onclick="newChat()">
        New Thread
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
      </button>

      <div class="panel">
        <h3>Library</h3>
        <ul id="history" class="history"></ul>
        <div class="row" style="margin-top:4px;">
          <button class="secondary" onclick="loadChats()">Refresh</button>
          <button class="secondary" onclick="exportThread()">Export</button>
          <button class="secondary" onclick="purgeAllData()">Delete All</button>
        </div>
      </div>

      <div class="panel">
        <h3>Knowledge</h3>
        <div class="row" style="margin-bottom: 4px;">
          <input id="fileInput" type="file" />
          <button class="primary-small" onclick="upload()">Save</button>
          <button class="secondary" onclick="saveThreadFiles()">Link</button>
        </div>
        <div id="uploadStatus"></div>
        <div id="fileSelectionStatus" class="hint"></div>
        <ul id="fileList" class="filelist"></ul>
      </div>

      <div class="panel" style="margin-top: auto;">
        <h3>System</h3>
        <div class="row">
          <button class="secondary" onclick="loadJobs()">Jobs</button>
          <button class="secondary" onclick="loadBackups()">Backups</button>
          <button class="secondary" onclick="createBackup()">Backup DB</button>
          <button class="secondary" onclick="logout()">Logout</button>
        </div>
        <ul id="jobList" class="filelist" style="max-height: 80px;"></ul>
        <ul id="backupList" class="filelist" style="max-height: 80px;"></ul>
      </div>
    </aside>
    <main class="main">
      <section id="thread" class="thread"></section>
      <section class="composer">
        <textarea id="q" placeholder="Ask the web, your uploaded docs, or both..."></textarea>
        <div class="row">
          <label style="color:var(--muted);font-size:13px;">
            <input id="includeFiles" type="checkbox" checked />
            include uploaded files
          </label>
          <label style="color:var(--muted);font-size:13px;">
            search
            <select id="searchMode" style="margin-left:6px;background:#0b111b;color:#eaf0ff;border:1px solid #243147;border-radius:8px;padding:4px;">
              <option value="all" selected>all</option>
              <option value="web">web</option>
              <option value="social">social</option>
            </select>
          </label>
          <button onclick="ask()">Ask</button>
        </div>
        <div id="followups" class="row"></div>
        <div id="status"></div>
      </section>
    </main>
  </div>
  <template id="welcomeTpl">
    <div class="message">
      <div class="meta">Ready</div>
      <div class="answer">Ask a question to start. Upload text/PDF files to include local context.</div>
    </div>
  </template>
  <script>
    let activeThreadId = null;
    let activeChatId = null;
    const selectedFileIds = new Set();
    let allFiles = [];
    let authEnabled = false;
    let jobsPollTimer = null;

    if ("serviceWorker" in navigator) {
      window.addEventListener("load", () => {
        navigator.serviceWorker.register("/sw.js").catch(() => {});
      });
    }

    function formatTime(iso) {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso || "";
      return d.toLocaleString();
    }

    function setStatus(text, isError=false) {
      const el = document.getElementById("status");
      el.textContent = text || "";
      el.className = isError ? "error" : "";
    }

    function setUploadStatus(text, isError=false) {
      const el = document.getElementById("uploadStatus");
      el.textContent = text || "";
      el.className = isError ? "error" : "";
    }

    function setFileSelectionStatus(text, isError=false) {
      const el = document.getElementById("fileSelectionStatus");
      el.textContent = text || "";
      el.className = isError ? "hint error" : "hint";
    }

    function setAuthStatus(text, isError=false) {
      const el = document.getElementById("authStatus");
      el.textContent = text || "";
      el.className = isError ? "hint error" : "hint";
    }

    function showAuthOverlay(show) {
      const el = document.getElementById("authOverlay");
      el.style.display = show ? "flex" : "none";
    }

    async function apiFetch(url, options={}) {
      const res = await fetch(url, options);
      if (res.status === 401) {
        showAuthOverlay(true);
        throw new Error("Unauthorized");
      }
      return res;
    }

    async function ensureAuth() {
      const res = await fetch("/auth/me");
      if (!res.ok) {
        throw new Error("Auth check failed");
      }
      const data = await res.json();
      authEnabled = !!data.auth_enabled;
      showAuthOverlay(authEnabled && !data.authenticated);
      return data;
    }

    async function login() {
      const input = document.getElementById("passwordInput");
      const password = input.value;
      setAuthStatus("Signing in...");
      try {
        const res = await fetch("/auth/login", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ password })
        });
        if (!res.ok) throw new Error(await res.text());
        showAuthOverlay(false);
        input.value = "";
        setAuthStatus("");
        await initializeApp();
      } catch (err) {
        setAuthStatus("Login failed: " + err.message, true);
      }
    }

    async function logout() {
      await fetch("/auth/logout", { method: "POST" });
      if (jobsPollTimer !== null) {
        clearInterval(jobsPollTimer);
        jobsPollTimer = null;
      }
      if (authEnabled) {
        showAuthOverlay(true);
      }
    }

    async function purgeAllData() {
      const ok = window.confirm("Delete all chats and uploaded files from this instance?");
      if (!ok) return;
      setStatus("Purging data...");
      try {
        const res = await apiFetch("/api/admin/purge", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ confirm: true })
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        activeThreadId = null;
        activeChatId = null;
        selectedFileIds.clear();
        allFiles = [];
        clearThread();
        await loadChats();
        await loadFiles();
        setFileSelectionStatus("No thread selected. File choices apply to the next ask.");
        setStatus(
          `Purged chats=${data.deleted_chat_count}, files=${data.deleted_file_count}, chunks=${data.deleted_chunk_count}`
        );
      } catch (err) {
        setStatus("Purge failed: " + err.message, true);
      }
    }

    async function exportThread() {
      if (activeThreadId === null) {
        setStatus("No active thread to export.", true);
        return;
      }
      const url = `/api/threads/${activeThreadId}/export?format=markdown`;
      window.open(url, "_blank", "noopener,noreferrer");
    }

    async function loadJobs() {
      const list = document.getElementById("jobList");
      list.innerHTML = "";
      try {
        const res = await apiFetch("/api/jobs?limit=10");
        if (!res.ok) throw new Error(await res.text());
        const jobs = await res.json();
        jobs.forEach(j => {
          const li = document.createElement("li");
          li.textContent = `#${j.id} ${j.job_type} ${j.status} ${Math.round((j.progress || 0) * 100)}%`;
          list.appendChild(li);
        });
        if (jobs.length === 0) {
          const li = document.createElement("li");
          li.textContent = "No jobs yet";
          list.appendChild(li);
        }
      } catch (err) {
        const li = document.createElement("li");
        li.textContent = "Failed to load jobs";
        list.appendChild(li);
      }
    }

    async function loadBackups() {
      const list = document.getElementById("backupList");
      list.innerHTML = "";
      try {
        const res = await apiFetch("/api/admin/backups");
        if (!res.ok) throw new Error(await res.text());
        const backups = await res.json();
        backups.forEach(b => {
          const li = document.createElement("li");
          const wrap = document.createElement("div");
          wrap.style.display = "flex";
          wrap.style.gap = "6px";
          wrap.style.alignItems = "center";
          const name = document.createElement("span");
          name.textContent = `${b.name} (${Math.ceil(b.size_bytes / 1024)} KB)`;
          const dl = document.createElement("button");
          dl.className = "secondary";
          dl.textContent = "DL";
          dl.onclick = () => window.open(`/api/admin/backups/${encodeURIComponent(b.name)}/download`, "_blank");
          const rs = document.createElement("button");
          rs.className = "secondary";
          rs.textContent = "Restore";
          rs.onclick = async () => {
            const ok = window.confirm(`Restore backup ${b.name}? This overwrites current DB.`);
            if (!ok) return;
            const r = await apiFetch(`/api/admin/backups/${encodeURIComponent(b.name)}/restore`, {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({confirm: true})
            });
            if (!r.ok) throw new Error(await r.text());
            setStatus(`Restored backup ${b.name}. Reloading...`);
            setTimeout(() => window.location.reload(), 1200);
          };
          wrap.appendChild(name);
          wrap.appendChild(dl);
          wrap.appendChild(rs);
          li.appendChild(wrap);
          list.appendChild(li);
        });
        if (backups.length === 0) {
          const li = document.createElement("li");
          li.textContent = "No backups yet";
          list.appendChild(li);
        }
      } catch (err) {
        const li = document.createElement("li");
        li.textContent = "Failed to load backups";
        list.appendChild(li);
      }
    }

    async function createBackup() {
      setStatus("Creating backup...");
      try {
        const res = await apiFetch("/api/admin/backups/create", { method: "POST" });
        if (!res.ok) throw new Error(await res.text());
        const b = await res.json();
        setStatus(`Backup created: ${b.name}`);
        await loadBackups();
      } catch (err) {
        setStatus("Backup failed: " + err.message, true);
      }
    }

    function truncate(s, n=70) {
      if (!s) return "";
      return s.length <= n ? s : (s.slice(0, n - 1) + "...");
    }

    function sortedSelectedFileIds() {
      return Array.from(selectedFileIds).sort((a, b) => a - b);
    }

    function appendSources(target, citations) {
      if (!Array.isArray(citations) || citations.length === 0) return;
      const s = document.createElement("div");
      s.className = "sources";
      citations.forEach((c, i) => {
        const card = document.createElement("div");
        card.className = "source-card";
        card.id = `source-${i + 1}`;
        const link = document.createElement("a");
        link.href = c.url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = `[${i + 1}] ${c.title || c.url}`;
        card.appendChild(link);
        try {
          const u = new URL(c.url, window.location.origin);
          const host = document.createElement("p");
          host.textContent = u.hostname;
          card.appendChild(host);
        } catch {}
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "snippet";
        details.appendChild(summary);
        const p = document.createElement("p");
        p.textContent = c.snippet || "";
        details.appendChild(p);
        card.appendChild(details);
        s.appendChild(card);
      });
      target.appendChild(s);
    }

    function setAnswerContent(answerEl, text, citations) {
      answerEl.innerHTML = "";
      const lines = String(text || "").split("\\n");
      lines.forEach((line, idx) => {
        const row = document.createElement("div");
        let last = 0;
        const regex = /\\[(\\d+)\\]/g;
        let m;
        while ((m = regex.exec(line)) !== null) {
          const before = line.slice(last, m.index);
          if (before) row.appendChild(document.createTextNode(before));
          const refNum = Number(m[1]);
          const a = document.createElement("a");
          a.href = `#source-${refNum}`;
          a.textContent = `[${refNum}]`;
          a.style.color = "#9cd8ff";
          a.style.textDecoration = "none";
          a.onclick = (ev) => {
            ev.preventDefault();
            const card = document.getElementById(`source-${refNum}`);
            if (card) {
              card.scrollIntoView({ behavior: "smooth", block: "center" });
              card.style.outline = "1px solid #5da3ff";
              setTimeout(() => { card.style.outline = ""; }, 1200);
            }
          };
          row.appendChild(a);
          last = regex.lastIndex;
        }
        const rest = line.slice(last);
        if (rest) row.appendChild(document.createTextNode(rest));
        answerEl.appendChild(row);
        if (idx < lines.length - 1) {
          answerEl.appendChild(document.createElement("br"));
        }
      });
    }

    async function loadFollowups(chatId) {
      const box = document.getElementById("followups");
      box.innerHTML = "";
      if (!chatId) return;
      try {
        const res = await apiFetch(`/api/chats/${chatId}/followups?limit=4`);
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        (data.suggestions || []).forEach(s => {
          const b = document.createElement("button");
          b.className = "secondary";
          b.textContent = s;
          b.onclick = () => { document.getElementById("q").value = s; };
          box.appendChild(b);
        });
      } catch (_err) {}
    }

    function renderMessage(query, answer, citations, metaText) {
      const thread = document.getElementById("thread");
      const msg = document.createElement("article");
      msg.className = "message";

      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = metaText || "";
      msg.appendChild(meta);

      const q = document.createElement("div");
      q.className = "query";
      q.textContent = query;
      msg.appendChild(q);

      const a = document.createElement("div");
      a.className = "answer";
      setAnswerContent(a, answer || "", citations || []);
      msg.appendChild(a);

      appendSources(msg, citations);
      thread.appendChild(msg);
      thread.scrollTop = thread.scrollHeight;
      return { msg, answerEl: a };
    }

    function clearThread() {
      const thread = document.getElementById("thread");
      thread.innerHTML = "";
      thread.appendChild(document.getElementById("welcomeTpl").content.cloneNode(true));
    }

    function clearThreadEmpty() {
      const thread = document.getElementById("thread");
      thread.innerHTML = "";
    }

    function renderFiles() {
      const fileList = document.getElementById("fileList");
      fileList.innerHTML = "";
      allFiles.forEach(file => {
        const li = document.createElement("li");
        const label = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = selectedFileIds.has(file.id);
        cb.onchange = () => {
          if (cb.checked) selectedFileIds.add(file.id);
          else selectedFileIds.delete(file.id);
          setFileSelectionStatus(
            activeThreadId === null
              ? `${selectedFileIds.size} file(s) selected for next new thread`
              : `${selectedFileIds.size} file(s) selected for thread #${activeThreadId} (click Save to persist)`
          );
        };
        const text = document.createElement("span");
        text.textContent = `${file.original_name} (${Math.ceil(file.size_bytes / 1024)} KB)`;
        label.appendChild(cb);
        label.appendChild(text);
        li.appendChild(label);
        fileList.appendChild(li);
      });
    }

    async function loadChats() {
      const history = document.getElementById("history");
      history.innerHTML = "";
      try {
        const res = await apiFetch("/api/chats?limit=40");
        if (!res.ok) throw new Error(await res.text());
        const chats = await res.json();
        chats.forEach(chat => {
          const li = document.createElement("li");
          const btn = document.createElement("button");
          btn.textContent = truncate(chat.query || "(empty)");
          btn.onclick = () => loadThread(chat.thread_id);
          li.appendChild(btn);
          history.appendChild(li);
        });
      } catch (err) {
        const li = document.createElement("li");
        li.textContent = "Failed to load chats";
        history.appendChild(li);
      }
    }

    async function loadFiles() {
      try {
        const res = await apiFetch("/api/files?limit=50");
        if (!res.ok) throw new Error(await res.text());
        allFiles = await res.json();
        renderFiles();
      } catch (err) {
        const fileList = document.getElementById("fileList");
        fileList.innerHTML = "";
        const li = document.createElement("li");
        li.textContent = "Failed to load files";
        fileList.appendChild(li);
      }
    }

    async function loadThread(threadId) {
      setStatus("Loading thread...");
      try {
        const res = await apiFetch(`/api/threads/${threadId}`);
        if (!res.ok) throw new Error(await res.text());
        const payload = await res.json();
        activeThreadId = payload.thread_id;
        selectedFileIds.clear();
        (payload.attached_file_ids || []).forEach(fid => selectedFileIds.add(fid));
        renderFiles();
        clearThreadEmpty();
        payload.chats.forEach(turn => {
          renderMessage(
            turn.query,
            turn.answer,
            turn.citations || [],
            formatTime(turn.created_at)
          );
          activeChatId = turn.id;
        });
        setFileSelectionStatus(`${selectedFileIds.size} file(s) attached to thread #${payload.thread_id}`);
        setStatus(`Loaded thread #${payload.thread_id}`);
        await loadFollowups(activeChatId);
      } catch (err) {
        setStatus("Failed to load thread", true);
      }
    }

    async function saveThreadFiles() {
      if (activeThreadId === null) {
        setFileSelectionStatus("Ask first to create a thread, then save attachments.", true);
        return;
      }
      try {
        const res = await apiFetch(`/api/threads/${activeThreadId}/files`, {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ file_ids: sortedSelectedFileIds() })
        });
        if (!res.ok) throw new Error(await res.text());
        const payload = await res.json();
        selectedFileIds.clear();
        (payload.file_ids || []).forEach(fid => selectedFileIds.add(fid));
        renderFiles();
        setFileSelectionStatus(`${selectedFileIds.size} file(s) saved to thread #${activeThreadId}`);
      } catch (err) {
        setFileSelectionStatus("Failed to save selection: " + err.message, true);
      }
    }

    function newChat() {
      activeThreadId = null;
      activeChatId = null;
      selectedFileIds.clear();
      renderFiles();
      clearThread();
      setFileSelectionStatus("No thread selected. File choices apply to the next ask.");
      setStatus("New chat ready");
      document.getElementById("followups").innerHTML = "";
    }

    function parseSseBlock(block) {
      const lines = block.split("\\n");
      let event = "message";
      let data = "";
      for (const line of lines) {
        if (line.startsWith("event:")) {
          event = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          data += line.slice(5).trim();
        }
      }
      if (!data) return null;
      try {
        return { event, payload: JSON.parse(data) };
      } catch {
        return null;
      }
    }

    async function ask() {
      const q = document.getElementById("q").value.trim();
      if (!q) return;
      setStatus("Generating answer...");
      const includeFiles = document.getElementById("includeFiles").checked;
      const fileIds = sortedSelectedFileIds();
      const searchMode = document.getElementById("searchMode").value || "all";

      if (activeThreadId === null) {
        clearThreadEmpty();
      }
      const rendered = renderMessage(q, "", [], "thinking...");
      try {
        const res = await apiFetch("/api/ask", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            query: q,
            include_files: includeFiles,
            thread_id: activeThreadId,
            file_ids: fileIds,
            search_mode: searchMode
          })
        });
        if (!res.ok) throw new Error(await res.text());
        const payload = await res.json();
        const answer = String(payload.answer || "");
        const citations = payload.citations || [];
        setAnswerContent(rendered.answerEl, answer, citations);
        appendSources(rendered.msg, citations);
        activeThreadId = payload.thread_id;
        activeChatId = payload.chat_id;
        setFileSelectionStatus(`${selectedFileIds.size} file(s) attached to thread #${activeThreadId}`);
        setStatus(`Saved to thread #${payload.thread_id} (chat #${payload.chat_id})`);
        loadFollowups(activeChatId);
        document.getElementById("q").value = "";
        await loadChats();
      } catch (err) {
        setStatus("Request failed: " + err.message, true);
      }
    }

    async function upload() {
      const input = document.getElementById("fileInput");
      if (!input.files || input.files.length === 0) {
        setUploadStatus("Choose a file first.");
        return;
      }
      const form = new FormData();
      form.append("file", input.files[0]);
      setUploadStatus("Uploading...");
      try {
        const res = await apiFetch("/api/files/upload", { method: "POST", body: form });
        const text = await res.text();
        if (!res.ok) throw new Error(text);
        const data = JSON.parse(text);
        const jobTxt = data.job_id ? `, job #${data.job_id}` : "";
        setUploadStatus(`Uploaded: ${data.filename} (#${data.file_id}${jobTxt})`);
        input.value = "";
        await loadFiles();
        await loadJobs();
      } catch (err) {
        setUploadStatus("Upload failed: " + err.message, true);
      }
    }

    async function initializeApp() {
      clearThread();
      document.getElementById("followups").innerHTML = "";
      await loadChats();
      await loadFiles();
      await loadJobs();
      await loadBackups();
      setFileSelectionStatus("No thread selected. File choices apply to the next ask.");
      if (jobsPollTimer === null) {
        jobsPollTimer = setInterval(() => { loadJobs(); }, 5000);
      }
    }

    const queryInput = document.getElementById("q");
    queryInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        ask();
      }
    });

    ensureAuth().then((info) => {
      if (!info.auth_enabled || info.authenticated) {
        initializeApp();
      }
    }).catch(() => {
      setStatus("Initialization failed.", true);
    });
  </script>
</body>
</html>
"""

