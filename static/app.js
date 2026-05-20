const state = {
  defaults: {},
  raw: null,
  normalized: null,
  outputFiles: [],
  selectedOutputPath: "",
  staticMode: false,
  sourceName: "未加载",
  currentJobId: null,
  loginJobId: null,
  pollTimer: null,
  selectedItemId: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function formatNumber(value) {
  const n = Number(value || 0);
  if (n >= 10000) return `${(n / 10000).toFixed(n >= 100000 ? 0 : 1)}万`;
  return n.toLocaleString("zh-CN");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#039;",
  }[ch]));
}

function setView(id) {
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === id));
  $$(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === id));
}

function setStopEnabled(enabled) {
  setDisabled("#stopJob", !enabled);
}

function setDisabled(selector, disabled) {
  const el = $(selector);
  if (el) el.disabled = disabled;
}

function enterStaticMode() {
  state.staticMode = true;
  const crawlNav = document.querySelector('[data-view="crawl"]');
  if (crawlNav) crawlNav.classList.add("hidden");
  const crawlView = $("#crawl");
  if (crawlView) crawlView.classList.add("hidden");
  setDisabled("#refreshOutputs", true);
  setDisabled("#refreshImportFiles", true);
  setDisabled("#loadSelectedFile", true);
  setDisabled("#deleteSelectedFile", true);
  setDisabled("#deleteCheckedFiles", true);
  setDisabled("#selectAllFiles", true);
  const fileList = $("#outputFiles");
  if (fileList) fileList.innerHTML = "<p class='schema-info'>线上展示版不连接后端。请使用“本地导入”上传 JSON。</p>";
  const info = $("#selectedFileInfo");
  if (info) info.textContent = "静态展示模式：不读取服务器 outputs/，不支持删除线上文件。";
  const fileCount = $("#fileCount");
  if (fileCount) fileCount.textContent = "静态模式";
  setView("import");
}

function downloadJson(data, filename) {
  if (!data) return;
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

function safeFileName(name, fallback) {
  return String(name || fallback).replace(/[^\w\u4e00-\u9fa5.-]+/g, "_");
}

function inferArray(data) {
  if (!data || typeof data !== "object") return [];
  if (Array.isArray(data)) return data;
  const preferred = ["videos", "posts", "items", "results", "data"];
  for (const key of preferred) {
    if (Array.isArray(data[key])) return data[key];
  }
  return Object.values(data).find(Array.isArray) || [];
}

function normalizeComment(comment, item, platform, isReply = false) {
  const content = comment.message ?? comment.content ?? comment.text ?? comment.raw_text ?? "";
  const author = comment.username ?? comment.author ?? comment.user_name ?? comment.nickname ?? "";
  const like = comment.like ?? comment.like_count ?? comment.likes ?? comment.digg_count ?? 0;
  const time = comment.ctime_str ?? comment.time_text ?? comment.created_at ?? comment.create_time ?? "";
  const location = String(comment.location ?? comment.ip_location ?? "").replace(/^IP属地：?/, "");
  return {
    id: String(comment.rpid ?? comment.comment_id ?? comment.id ?? `${item.id}-${Math.random()}`),
    platform,
    itemId: item.id,
    itemTitle: item.title,
    itemUrl: item.url,
    author,
    content: String(content || ""),
    like: Number(like || 0),
    time,
    location,
    isReply,
    raw: comment,
  };
}

function normalizeThread(comment, item, platform) {
  const root = normalizeComment(comment, item, platform, false);
  const replies = Array.isArray(comment.replies)
    ? comment.replies.map((reply) => normalizeComment(reply, item, platform, true)).filter((reply) => reply.content)
    : [];
  root.replies = replies;
  return root;
}

function normalizeData(data, sourceName = "导入数据") {
  const platform = data?.videos ? "bilibili" : data?.posts ? "xhs" : "generic";
  const rawItems = inferArray(data);
  const items = rawItems.map((raw, index) => {
    const id = raw.bvid ?? raw.note_id ?? raw.aweme_id ?? raw.id ?? `item-${index + 1}`;
    const title = raw.title ?? raw.desc ?? raw.description ?? raw.name ?? `未命名 ${index + 1}`;
    const commentsRaw = Array.isArray(raw.comments)
      ? raw.comments
      : Array.isArray(raw.top_comments)
        ? raw.top_comments
        : Array.isArray(raw.top_10_comments)
          ? raw.top_10_comments
          : [];
    const item = {
      id: String(id),
      rank: raw.rank ?? index + 1,
      platform,
      title: String(title || `未命名 ${index + 1}`),
      author: raw.author ?? raw.username ?? raw.nickname ?? "",
      url: raw.url ?? raw.share_url ?? "",
      play: Number(raw.play ?? raw.view_count ?? raw.play_count ?? 0),
      likes: Number(raw.like_count ?? raw.likes ?? raw.favorite ?? raw.favorites ?? 0),
      commentDeclared: Number(raw.comment ?? raw.comment_count ?? raw.comments_count ?? raw.review ?? 0),
      raw,
      comments: [],
      rootComments: [],
    };
    item.rootComments = commentsRaw.map((comment) => normalizeThread(comment, item, platform)).filter((comment) => comment.content);
    item.comments = item.rootComments.flatMap((comment) => [comment, ...(comment.replies || [])]);
    item.replyCount = item.rootComments.reduce((sum, comment) => sum + (comment.replies?.length || 0), 0);
    item.rootCommentCount = item.rootComments.length;
    item.totalInteraction = item.comments.reduce((sum, comment) => sum + comment.like, 0);
    return item;
  });
  const comments = items.flatMap((item) => item.comments);
  const sortedComments = [...comments].sort((a, b) => b.like - a.like);
  return {
    sourceName,
    platform,
    keyword: data?.keyword ?? "",
    generatedAt: data?.crawl_time ?? data?.generated_at ?? "",
    items,
    comments,
    topComments: sortedComments.slice(0, 50),
    raw: data,
  };
}

function countBy(values) {
  const map = new Map();
  values.filter(Boolean).forEach((value) => map.set(value, (map.get(value) || 0) + 1));
  return [...map.entries()].sort((a, b) => b[1] - a[1]);
}

function wordFrequency(comments) {
  const stop = new Set(["这个", "就是", "不是", "什么", "没有", "哈哈", "真的", "可以", "还是", "感觉", "因为", "所以", "但是", "如果", "一个", "我们", "他们", "你们", "回复", "时候", "都是", "自己", "个人", "看到", "评论", "视频"]);
  const counts = new Map();
  for (const comment of comments) {
    const text = comment.content.replace(/\[[^\]]+\]/g, " ");
    const chunks = text.match(/[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}/g) || [];
    for (const chunk of chunks) {
      if (/^[a-zA-Z]+$/.test(chunk)) {
        const key = chunk.toLowerCase();
        if (!stop.has(key)) counts.set(key, (counts.get(key) || 0) + 1);
      } else if (chunk.length <= 4) {
        if (!stop.has(chunk)) counts.set(chunk, (counts.get(chunk) || 0) + 1);
      } else {
        for (let i = 0; i < chunk.length - 1; i += 1) {
          const key = chunk.slice(i, i + 2);
          if (!stop.has(key)) counts.set(key, (counts.get(key) || 0) + 1);
        }
      }
    }
  }
  return [...counts.entries()].filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]).slice(0, 40);
}

function shortText(text, max = 72) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function renderInsightList(target, rows, emptyText = "暂无可展示洞察") {
  target.innerHTML = rows.length ? rows.map((row) => `
    <article class="insight-item">
      <div class="insight-head">
        <strong>${escapeHtml(row.title)}</strong>
        <span>${escapeHtml(row.meta || "")}</span>
      </div>
      <p>${escapeHtml(row.body || "")}</p>
      ${row.foot ? `<small>${escapeHtml(row.foot)}</small>` : ""}
    </article>
  `).join("") : `<p class='schema-info'>${escapeHtml(emptyText)}</p>`;
}

function buildThemeInsights(data) {
  const words = wordFrequency(data.comments).slice(0, 8);
  return words.map(([word, count]) => {
    const related = data.comments
      .filter((comment) => comment.content.includes(word))
      .sort((a, b) => b.like - a.like);
    const top = related[0];
    const items = new Set(related.map((comment) => comment.itemId));
    return {
      title: `「${word}」相关讨论`,
      meta: `${count}次 · ${items.size}个${itemTypeLabel(data.platform)}`,
      body: shortText(top?.content || ""),
      foot: top ? `${top.author || "匿名"} · ${formatNumber(top.like)}赞 · ${top.itemTitle}` : "",
    };
  });
}

function buildQuestionInsights(data) {
  const patterns = [
    /[？?]/,
    /(为什么|咋|怎么|谁|哪里|是不是|难道|真的吗|真的假的|不懂|求问)/,
    /(不是|不对|问题|争议|离谱|夸张|造谣|辟谣|反转)/,
  ];
  return data.comments
    .filter((comment) => patterns.some((pattern) => pattern.test(comment.content)))
    .sort((a, b) => (b.like + (b.isReply ? 0 : 3)) - (a.like + (a.isReply ? 0 : 3)))
    .slice(0, 8)
    .map((comment) => ({
      title: comment.isReply ? "回复里的疑问/争议" : "主评论里的疑问/争议",
      meta: `${formatNumber(comment.like)}赞`,
      body: shortText(comment.content, 92),
      foot: `${comment.author || "匿名"} · ${comment.itemTitle}`,
    }));
}

function buildReplyInsights(data) {
  return data.items
    .flatMap((item) => (item.rootComments || []).map((comment) => ({ item, comment })))
    .filter(({ comment }) => (comment.replies || []).length > 0)
    .sort((a, b) => (b.comment.replies.length - a.comment.replies.length) || (b.comment.like - a.comment.like))
    .slice(0, 8)
    .map(({ item, comment }) => ({
      title: `${comment.replies.length}条回复 · ${comment.author || "匿名"}`,
      meta: `${formatNumber(comment.like)}赞`,
      body: shortText(comment.content, 92),
      foot: item.title,
    }));
}

function renderBars(target, rows, color = "var(--accent-3)") {
  const max = Math.max(1, ...rows.map((row) => row.value));
  target.innerHTML = rows.length ? rows.map((row) => `
    <div class="bar-row" title="${escapeHtml(row.label)}">
      <div class="bar-label">${escapeHtml(row.label)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(3, row.value / max * 100)}%; background:${color}"></div></div>
      <div class="bar-value">${formatNumber(row.value)}</div>
    </div>
  `).join("") : "<p class='schema-info'>暂无可展示数据</p>";
}

function itemScore(item) {
  return item.comments.length || item.commentDeclared || item.likes || item.play || 0;
}

function itemTypeLabel(platform) {
  if (platform === "xhs") return "帖子";
  if (platform === "bilibili") return "视频";
  return "条目";
}

function renderItemList() {
  const data = state.normalized;
  if (!data) return;
  if (!state.selectedItemId && data.items[0]) {
    state.selectedItemId = data.items[0].id;
  }
  const sortedItems = [...data.items].sort((a, b) => (a.rank || 0) - (b.rank || 0));
  $("#itemList").innerHTML = sortedItems.map((item) => {
    const active = item.id === state.selectedItemId ? " active" : "";
    return `
      <button class="item-row${active}" data-item-id="${escapeHtml(item.id)}" type="button">
        <span class="item-rank">#${escapeHtml(item.rank || "")}</span>
        <span class="item-main">
          <strong>${escapeHtml(item.title || "未命名")}</strong>
          <small>${escapeHtml(item.author || "未知作者")} · 主评论 ${formatNumber(item.rootCommentCount || 0)} · 回复 ${formatNumber(item.replyCount || 0)}</small>
        </span>
        <span class="item-count">${formatNumber(itemScore(item))}</span>
      </button>
    `;
  }).join("") || "<p class='schema-info'>暂无帖子/视频数据</p>";

  $$("#itemList .item-row").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedItemId = button.dataset.itemId;
      renderItemList();
      renderItemDetail();
    });
  });
}

function renderItemDetail() {
  const data = state.normalized;
  if (!data) return;
  const item = data.items.find((entry) => entry.id === state.selectedItemId) || data.items[0];
  if (!item) {
    $("#itemDetailHeader").innerHTML = "<p class='schema-info'>请选择一个条目</p>";
    $("#itemComments").innerHTML = "";
    return;
  }
  const urlButton = item.url
    ? `<a class="open-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">打开原${itemTypeLabel(data.platform)}</a>`
    : "";
  const itemCommentsTotal = (item.rootCommentCount || 0) + (item.replyCount || 0);
  $("#itemDetailHeader").innerHTML = `
    <div class="item-title-block">
      <div class="item-title-line">
        <h3>${escapeHtml(item.title)}</h3>
        ${urlButton}
      </div>
      <p>${escapeHtml(item.author || "未知作者")} · 当前选择的${itemTypeLabel(data.platform)}</p>
      <div class="item-local-metrics">
        <span><strong>${formatNumber(itemCommentsTotal)}</strong>评论与回复</span>
        <span><strong>${formatNumber(item.rootCommentCount || 0)}</strong>主评论</span>
        <span><strong>${formatNumber(item.replyCount || 0)}</strong>回复</span>
        <span><strong>${formatNumber(item.totalInteraction || 0)}</strong>评论获赞</span>
      </div>
    </div>
  `;

  const comments = [...(item.rootComments || [])].sort((a, b) => b.like - a.like);
  $("#itemComments").innerHTML = comments.map((comment, index) => `
    <article class="thread-item">
      <div class="thread-head">
        <strong>${index + 1}. ${escapeHtml(comment.author || "匿名")}</strong>
        <span>${formatNumber(comment.like)}赞${comment.location ? ` · ${escapeHtml(comment.location)}` : ""}</span>
      </div>
      <p>${escapeHtml(comment.content)}</p>
      ${(comment.replies || []).length ? `
        <div class="reply-block">
          ${(comment.replies || []).map((reply) => `
            <div class="reply-item">
              <strong>${escapeHtml(reply.author || "匿名")}</strong>
              <span>${escapeHtml(reply.content)}</span>
              <small>${formatNumber(reply.like)}赞${reply.location ? ` · ${escapeHtml(reply.location)}` : ""}</small>
            </div>
          `).join("")}
        </div>
      ` : "<small class='muted'>暂无回复</small>"}
    </article>
  `).join("") || "<p class='schema-info'>这个条目暂无评论数据</p>";
}

function renderDashboard() {
  const data = state.normalized;
  if (!data) return;
  const totalComments = data.comments.length;
  const rootComments = data.comments.filter((c) => !c.isReply).length;
  const replyComments = totalComments - rootComments;
  const totalLikes = data.comments.reduce((sum, c) => sum + c.like, 0);
  const itemsWithComments = data.items.filter((item) => item.rootCommentCount > 0).length;
  $("#datasetSummary").innerHTML = `
    <div>
      <strong>数据集概览</strong>
      <span>${escapeHtml(data.sourceName)} · ${escapeHtml(data.keyword || "未提供关键词")}</span>
    </div>
    <p>共 ${formatNumber(data.items.length)} 个${itemTypeLabel(data.platform)}，其中 ${formatNumber(itemsWithComments)} 个包含评论；合计 ${formatNumber(rootComments)} 条主评论、${formatNumber(replyComments)} 条回复、${formatNumber(totalLikes)} 个评论赞。</p>
  `;

  const itemRows = [...data.items]
    .map((item) => ({ label: item.title, value: itemScore(item) }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 12);
  renderBars($("#itemsChart"), itemRows, "var(--accent-3)");
  renderItemList();
  renderItemDetail();

  $("#topComments").innerHTML = data.topComments.slice(0, 10).map((comment) => `
    <div class="comment-item">
      <strong><span>${escapeHtml(comment.author || "匿名")}</span><span>${formatNumber(comment.like)}赞</span></strong>
      <p>${escapeHtml(comment.content)}</p>
      <small>${escapeHtml(comment.itemTitle)} ${comment.location ? ` · ${escapeHtml(comment.location)}` : ""}</small>
    </div>
  `).join("") || "<p class='schema-info'>暂无评论</p>";
}

function renderReport() {
  const data = state.normalized;
  if (!data) return;
  const topItem = [...data.items].sort((a, b) => b.comments.length - a.comments.length)[0];
  const locations = countBy(data.comments.map((comment) => comment.location || "未知")).slice(0, 5);
  const words = wordFrequency(data.comments).slice(0, 12);
  const topComments = data.topComments.slice(0, 5);
  const replyComments = data.comments.filter((c) => c.isReply).length;
  const itemsWithComments = data.items.filter((item) => item.rootCommentCount > 0).length;
  $("#insightReport").innerHTML = `
    <h3>概览</h3>
    <p>本次数据识别为 ${data.platform} 格式，共拉取 ${data.items.length} 个${itemTypeLabel(data.platform)}，其中 ${itemsWithComments} 个包含评论数据。归一化后包含 ${data.comments.length} 条评论/回复，其中 ${replyComments} 条为子回复。关键词为「${escapeHtml(data.keyword || "未提供")}」。</p>
    <h3>互动重点</h3>
    <p>评论最集中的内容是「${escapeHtml(topItem?.title || "暂无")}」，当前样本内评论数为 ${topItem?.comments.length || 0}。高赞评论通常代表用户最容易共鸣或争议的观点，可优先用于人工解读。</p>
    <h3>地区分布</h3>
    <ul>${locations.map(([name, value]) => `<li>${escapeHtml(name)}：${value} 条</li>`).join("") || "<li>暂无地区信息</li>"}</ul>
    <h3>高频表达</h3>
    <p>${words.map(([word]) => `「${escapeHtml(word)}」`).join("、") || "暂无足够文本"}。</p>
    <h3>代表性评论</h3>
    <ul>${topComments.map((comment) => `<li>${formatNumber(comment.like)}赞｜${escapeHtml(comment.author || "匿名")}：${escapeHtml(comment.content)}</li>`).join("") || "<li>暂无评论</li>"}</ul>
  `;
}

function renderSchema() {
  const data = state.normalized;
  if (!data) return;
  $("#formatBadge").textContent = data.platform;
  $("#schemaInfo").innerHTML = `
    <p>平台：<strong>${escapeHtml(data.platform)}</strong></p>
    <p>关键词：${escapeHtml(data.keyword || "未提供")}</p>
    <p>条目：${data.items.length}，评论：${data.comments.length}</p>
    <p>评论字段已归一化为：author、content、like、time、location、itemTitle。</p>
  `;
}

function setData(raw, sourceName) {
  state.raw = raw;
  state.sourceName = sourceName;
  state.normalized = normalizeData(raw, sourceName);
  state.selectedItemId = state.normalized.items[0]?.id || null;
  $("#sourceName").textContent = sourceName;
  $("#sourceMeta").textContent = `${state.normalized.platform} · ${state.normalized.items.length} 条目 · ${state.normalized.comments.length} 评论`;
  $("#jsonPreview").textContent = JSON.stringify(raw, null, 2);
  $("#jsonSize").textContent = `${Math.ceil(new Blob([JSON.stringify(raw)]).size / 1024)} KB`;
  renderSchema();
  renderDashboard();
  renderReport();
  updateExportButtons();
  setView("dashboard");
}

function updateExportButtons() {
  const hasData = Boolean(state.raw && state.normalized);
  setDisabled("#downloadNormalized", !hasData);
  setDisabled("#downloadRaw", !hasData);
  setDisabled("#downloadNormalizedImport", !hasData);
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${value} B`;
}

function updateSelectedFileInfo() {
  const file = state.outputFiles.find((entry) => entry.path === state.selectedOutputPath);
  const info = $("#selectedFileInfo");
  if (!info) return;
  if (!file) {
    info.textContent = "暂无结果文件";
    setDisabled("#loadSelectedFile", true);
    setDisabled("#deleteSelectedFile", true);
    return;
  }
  info.innerHTML = `
    <strong>${escapeHtml(file.name)}</strong>
    <span>${escapeHtml(file.modified_at)} · ${formatBytes(file.size)}</span>
    <small>${file.deletable ? "可删除：outputs/ 结果文件" : "只读：示例或外部文件"}</small>
  `;
  setDisabled("#loadSelectedFile", false);
  setDisabled("#deleteSelectedFile", !file.deletable);
  setDisabled("#deleteCheckedFiles", !getCheckedOutputPaths().length);
}

function getCheckedOutputPaths() {
  return $$("#outputFiles input[type='checkbox']:checked")
    .map((input) => input.value)
    .filter(Boolean);
}

function renderOutputFiles() {
  const list = $("#outputFiles");
  if (!list) return;
  $("#fileCount").textContent = `${state.outputFiles.length} 个文件`;
  if (!state.outputFiles.length) {
    list.innerHTML = "<p class='schema-info'>暂无 JSON 结果文件</p>";
    state.selectedOutputPath = "";
    updateSelectedFileInfo();
    return;
  }
  if (!state.outputFiles.some((file) => file.path === state.selectedOutputPath)) {
    state.selectedOutputPath = state.outputFiles[0].path;
  }
  list.innerHTML = state.outputFiles.map((file) => {
    const active = file.path === state.selectedOutputPath ? " active" : "";
    return `
      <div class="file-row${active}" data-path="${escapeHtml(file.path)}">
        <input class="file-check" type="checkbox" value="${escapeHtml(file.path)}" ${file.deletable ? "" : "disabled"} />
        <button class="file-select" type="button">
          <strong>${escapeHtml(file.name)}</strong>
          <span>${escapeHtml(file.modified_at)} · ${formatBytes(file.size)}${file.deletable ? "" : " · 只读"}</span>
        </button>
      </div>
    `;
  }).join("");
  $$("#outputFiles .file-select").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedOutputPath = button.closest(".file-row")?.dataset.path || "";
      renderOutputFiles();
    });
  });
  $$("#outputFiles .file-check").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const checked = getCheckedOutputPaths();
      const deletableCount = state.outputFiles.filter((file) => file.deletable).length;
      $("#selectAllFiles").checked = Boolean(checked.length && checked.length === deletableCount);
      setDisabled("#deleteCheckedFiles", !checked.length);
    });
  });
  updateSelectedFileInfo();
}

async function loadOutputs() {
  if (state.staticMode) {
    renderOutputFiles();
    return;
  }
  const res = await fetch("/api/outputs");
  const data = await res.json();
  state.outputFiles = data.files || [];
  renderOutputFiles();
}

async function loadFile(path) {
  if (!path) return;
  if (state.staticMode) {
    throw new Error("静态展示模式无法从服务器读取文件，请使用本地导入。");
  }
  const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "读取失败");
  setData(data, path.split("/").pop());
}

async function deleteSelectedFile() {
  if (state.staticMode) return;
  const path = state.selectedOutputPath;
  const file = state.outputFiles.find((entry) => entry.path === path);
  if (!file || !file.deletable) return;
  if (!confirm(`确认删除 ${file.name}？`)) return;
  const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "删除失败");
    return;
  }
  if (state.sourceName === file.name) {
    state.raw = null;
    state.normalized = null;
    state.selectedItemId = null;
    $("#sourceName").textContent = "未加载";
    $("#sourceMeta").textContent = "等待爬取或导入 JSON";
    $("#jsonPreview").textContent = "暂无数据";
    $("#jsonSize").textContent = "0 KB";
    updateExportButtons();
  }
  await loadOutputs();
}

async function deleteCheckedFiles() {
  if (state.staticMode) return;
  const paths = getCheckedOutputPaths();
  if (!paths.length) return;
  if (!confirm(`确认删除选中的 ${paths.length} 个 JSON 文件？`)) return;
  const res = await fetch("/api/files/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "批量删除失败");
    return;
  }
  const deletedNames = new Set((data.deleted || []).map((item) => item.name));
  if (deletedNames.has(state.sourceName)) {
    state.raw = null;
    state.normalized = null;
    state.selectedItemId = null;
    $("#sourceName").textContent = "未加载";
    $("#sourceMeta").textContent = "等待爬取或导入 JSON";
    $("#jsonPreview").textContent = "暂无数据";
    $("#jsonSize").textContent = "0 KB";
    updateExportButtons();
  }
  if ((data.failed || []).length) {
    alert(`已删除 ${data.deleted.length} 个，${data.failed.length} 个删除失败。`);
  }
  $("#selectAllFiles").checked = false;
  await loadOutputs();
}

function formPayload(form) {
  const fd = new FormData(form);
  const platform = fd.get("platform");
  return {
    platform,
    keyword: fd.get("keyword"),
    outputFile: fd.get("outputFile"),
    videoCount: Number(fd.get("videoCount") || 10),
    biliOrder: fd.get("biliOrder") || "totalrank",
    biliDuration: Number(fd.get("biliDuration") || 0),
    xhsContentType: fd.get("xhsContentType") || "all",
    xhsSort: fd.get("xhsSort") || "general",
    commentCount: Number(fd.get("commentCount") || 20),
    maxRepliesPerComment: Number(fd.get("maxRepliesPerComment") || 200),
    limit: Number(fd.get("limit") || 10),
    headless: Boolean(fd.get("headless")),
    scriptPath: platform === "bilibili" ? fd.get("biliScriptPath") : fd.get("xhsScriptPath"),
    profileDir: fd.get("profileDir"),
  };
}

function setPlatform(platform) {
  $$(".bili-only").forEach((el) => el.classList.toggle("hidden", platform !== "bilibili"));
  $$(".xhs-only").forEach((el) => el.classList.toggle("hidden", platform !== "xhs"));
  $("#commentCountLabel").textContent = platform === "bilibili" ? "每视频热门评论数" : "每帖热门评论数";
  if (platform === "xhs") {
    $("[name=headless]").checked = false;
  }
  const profile = $("[name=profileDir]");
  if (state.defaults.bilibiliProfileDir && state.defaults.xhsProfileDir) {
    profile.value = platform === "bilibili"
      ? state.defaults.bilibiliProfileDir
      : state.defaults.xhsProfileDir;
  }
}

async function startCrawl(event) {
  event.preventDefault();
  const payload = formPayload(event.currentTarget);
  const res = await fetch("/api/crawl", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const job = await res.json();
  if (!res.ok) {
    $("#jobLogs").textContent = job.error || "启动失败";
    return;
  }
  state.currentJobId = job.id;
  $("#jobLogs").textContent = "任务已启动...";
  setStopEnabled(true);
  pollJob(job.id, true);
}

async function pollJob(jobId, loadWhenDone = false) {
  clearInterval(state.pollTimer);
  const tick = async () => {
    const res = await fetch(`/api/jobs/${jobId}`);
    const job = await res.json();
    $("#jobStatus").textContent = job.status || "未知";
    $("#jobLogs").textContent = (job.logs || []).join("\n") || "等待日志...";
    $("#jobLogs").scrollTop = $("#jobLogs").scrollHeight;
    setStopEnabled(["queued", "running", "stopping"].includes(job.status));
    if (["finished", "failed", "stopped"].includes(job.status)) {
      clearInterval(state.pollTimer);
      await loadOutputs();
      if (loadWhenDone && job.output_file && job.status === "finished") {
        await loadFile(job.output_file);
      }
    }
  };
  await tick();
  state.pollTimer = setInterval(tick, 1600);
}

async function startLogin(platform) {
  $("#platformSelect").value = platform;
  setPlatform(platform);
  const payload = formPayload($("#crawlForm"));
  const endpoint = platform === "bilibili" ? "/api/bilibili-login" : "/api/xhs-login";
  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const job = await res.json();
  if (!res.ok) {
    $("#jobLogs").textContent = job.error || "登录启动失败";
    return;
  }
  state.loginJobId = job.id;
  state.currentJobId = job.id;
  $("#finishLogin").disabled = false;
  setStopEnabled(true);
  pollJob(job.id, false);
}

async function finishLogin() {
  if (!state.loginJobId) return;
  await fetch(`/api/jobs/${state.loginJobId}/finish-login`, { method: "POST" });
  $("#finishLogin").disabled = true;
}

async function stopCurrentJob() {
  const jobId = state.currentJobId || state.loginJobId;
  if (!jobId) return;
  $("#jobStatus").textContent = "stopping";
  $("#stopJob").disabled = true;
  const res = await fetch(`/api/jobs/${jobId}/stop`, { method: "POST" });
  const job = await res.json();
  if (!res.ok) {
    $("#jobLogs").textContent += `\n停止失败：${job.error || "未知错误"}`;
    return;
  }
  $("#jobStatus").textContent = job.status || "stopped";
  $("#jobLogs").textContent = (job.logs || []).join("\n") || "任务已停止。";
  $("#finishLogin").disabled = true;
  await loadOutputs();
}

async function init() {
  $$(".nav-button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("#platformSelect").addEventListener("change", (event) => setPlatform(event.target.value));
  $("#crawlForm").addEventListener("submit", startCrawl);
  $("#startBiliLogin").addEventListener("click", () => startLogin("bilibili"));
  $("#startXhsLogin").addEventListener("click", () => startLogin("xhs"));
  $("#finishLogin").addEventListener("click", finishLogin);
  $("#stopJob").addEventListener("click", stopCurrentJob);
  $("#refreshOutputs").addEventListener("click", loadOutputs);
  $("#refreshImportFiles").addEventListener("click", loadOutputs);
  $("#selectAllFiles").addEventListener("change", (event) => {
    $$("#outputFiles .file-check:not(:disabled)").forEach((checkbox) => {
      checkbox.checked = event.target.checked;
    });
    setDisabled("#deleteCheckedFiles", !getCheckedOutputPaths().length);
  });
  $("#loadSelectedFile").addEventListener("click", async () => loadFile(state.selectedOutputPath));
  $("#deleteSelectedFile").addEventListener("click", deleteSelectedFile);
  $("#deleteCheckedFiles").addEventListener("click", deleteCheckedFiles);
  $("#fileInput").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const text = await file.text();
    setData(JSON.parse(text), file.name);
  });
  $("#downloadNormalized").addEventListener("click", () => {
    downloadJson(state.normalized, `normalized_${safeFileName(state.sourceName, "comments.json")}`);
  });
  $("#downloadRaw").addEventListener("click", () => {
    downloadJson(state.raw, `raw_${safeFileName(state.sourceName, "comments.json")}`);
  });
  $("#downloadNormalizedImport").addEventListener("click", () => {
    downloadJson(state.normalized, `normalized_${safeFileName(state.sourceName, "comments.json")}`);
  });
  $("#copyReport").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("#insightReport").innerText);
  });

  try {
    const defaultsRes = await fetch("/api/defaults");
    if (!defaultsRes.ok) throw new Error("no backend");
    const defaults = await defaultsRes.json();
    state.defaults = defaults;
    $("[name=biliScriptPath]").value = defaults.bilibiliBrowserScript;
    $("[name=xhsScriptPath]").value = defaults.xhsScript;
    $("[name=profileDir]").value = defaults.bilibiliProfileDir;
  } catch (_error) {
    enterStaticMode();
  }
  updateExportButtons();
  await loadOutputs();
}

init().catch((error) => {
  const target = $("#jobLogs") || $("#selectedFileInfo");
  if (target) target.textContent = `初始化失败：${error.message}`;
});
