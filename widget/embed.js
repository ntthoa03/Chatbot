/**
 * H3-06: widget chat thuần JavaScript chỉ dùng để demo.
 *
 * TODO(Hieu/Production): production cần auth thật, CSP, rate limit, telemetry,
 * consent/retention và cơ chế retry. Không phát triển các phần đó trong file demo.
 */
(function () {
  "use strict";

  // Lấy config ngay khi async script được thực thi; không đọc key từ URL/log.
  var script = document.currentScript;
  if (!script) return;
  if (document.body) mountWidget();
  else document.addEventListener("DOMContentLoaded", mountWidget, { once: true });

  function mountWidget() {
  if (document.querySelector("[data-mima-widget-host]")) return;

  var configuredApiUrl = script.dataset.apiUrl || "http://127.0.0.1:8000";
  // data-api-url="auto" giúp cùng demo chạy trên PC và điện thoại trong LAN:
  // hostname lấy từ trang đang mở, API H3-05 vẫn dùng cổng 8000.
  var apiUrl = configuredApiUrl === "auto"
    ? window.location.protocol + "//" + window.location.hostname + ":8000"
    : configuredApiUrl.replace(/\/$/, "");
  var publicKey = script.dataset.publicKey || "";
  var tenantId = script.dataset.tenantId || "";
  var title = script.dataset.title || "Trợ lý tư vấn";
  var welcome = script.dataset.welcome || "Xin chào! Anh/chị cần em hỗ trợ nội dung gì ạ?";
  var configVersion = Number(script.dataset.configVersion || "1");
  var conversationId = createConversationId();
  var history = [];
  var busy = false;

  var host = document.createElement("div");
  host.setAttribute("data-mima-widget-host", "");
  var shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = [
    "<style>",
    ":host{all:initial;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:#172033}",
    "*{box-sizing:border-box}",
    ".launcher{position:fixed;right:20px;bottom:20px;z-index:2147483000;width:58px;height:58px;border:0;border-radius:50%;background:#6d4aff;color:#fff;box-shadow:0 12px 30px #24165a42;cursor:pointer;font-size:25px}",
    ".panel{position:fixed;right:20px;bottom:90px;z-index:2147483000;width:min(380px,calc(100vw - 24px));height:min(590px,calc(100vh - 112px));display:none;grid-template-rows:auto 1fr auto;background:#fff;border:1px solid #dfe3ec;border-radius:18px;overflow:hidden;box-shadow:0 22px 55px #17203333}",
    ".panel.open{display:grid}",
    ".head{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;background:#6d4aff;color:#fff}",
    ".head strong{font-size:16px}.head small{display:block;margin-top:2px;opacity:.85;font-size:11px}",
    ".close{border:0;background:transparent;color:#fff;font-size:24px;line-height:1;cursor:pointer;padding:3px 6px}",
    ".messages{padding:15px;overflow:auto;background:#f6f7fb;scroll-behavior:smooth}",
    ".message{max-width:86%;margin:0 0 10px;padding:10px 12px;border-radius:14px;white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.45 Inter,system-ui,sans-serif}",
    ".assistant{background:#fff;border:1px solid #e5e8ef;border-bottom-left-radius:4px}",
    ".user{margin-left:auto;background:#6d4aff;color:#fff;border-bottom-right-radius:4px}",
    ".error{background:#fff0f0;color:#9f1c1c;border:1px solid #ffc9c9}",
    ".typing::after{content:'';display:inline-block;width:5px;height:5px;margin-left:5px;border-radius:50%;background:#6d4aff;animation:pulse .8s infinite alternate}",
    "@keyframes pulse{to{opacity:.2}}",
    ".form{display:grid;grid-template-columns:1fr auto;gap:8px;padding:12px;background:#fff;border-top:1px solid #e5e8ef}",
    ".input{min-width:0;border:1px solid #cfd5e1;border-radius:12px;padding:10px 12px;font:14px Inter,system-ui,sans-serif;outline:none}.input:focus{border-color:#6d4aff;box-shadow:0 0 0 3px #6d4aff20}",
    ".send{border:0;border-radius:12px;padding:0 14px;background:#6d4aff;color:#fff;font-weight:700;cursor:pointer}.send:disabled,.input:disabled{opacity:.55;cursor:not-allowed}",
    "@media(max-width:600px){.launcher{right:14px;bottom:14px}.panel{right:8px;bottom:82px;width:calc(100vw - 16px);height:min(72vh,620px);border-radius:15px}}",
    "</style>",
    '<button class="launcher" type="button" aria-label="Mở cửa sổ chat" aria-expanded="false">💬</button>',
    '<section class="panel" role="dialog" aria-label="Trợ lý tư vấn" aria-hidden="true">',
    '<header class="head"><div><strong class="title"></strong><small>Demo nội bộ</small></div><button class="close" type="button" aria-label="Đóng cửa sổ chat">×</button></header>',
    '<main class="messages" aria-live="polite"></main>',
    '<form class="form"><input class="input" maxlength="1000" autocomplete="off" placeholder="Nhập câu hỏi…" aria-label="Nội dung chat"><button class="send" type="submit">Gửi</button></form>',
    "</section>"
  ].join("");
  document.body.appendChild(host);

  var launcher = shadow.querySelector(".launcher");
  var panel = shadow.querySelector(".panel");
  var closeButton = shadow.querySelector(".close");
  var form = shadow.querySelector(".form");
  var input = shadow.querySelector(".input");
  var sendButton = shadow.querySelector(".send");
  var messages = shadow.querySelector(".messages");
  shadow.querySelector(".title").textContent = title;
  addMessage("assistant", welcome);

  launcher.addEventListener("click", function () { setOpen(!panel.classList.contains("open")); });
  closeButton.addEventListener("click", function () { setOpen(false); });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var message = input.value.trim();
    if (!message || busy) return;
    input.value = "";
    submitMessage(message);
  });

  function setOpen(open) {
    panel.classList.toggle("open", open);
    panel.setAttribute("aria-hidden", String(!open));
    launcher.setAttribute("aria-expanded", String(open));
    launcher.textContent = open ? "×" : "💬";
    if (open) input.focus();
  }

  function addMessage(role, text, extraClass) {
    var item = document.createElement("div");
    item.className = "message " + role + (extraClass ? " " + extraClass : "");
    item.textContent = text;
    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
    return item;
  }

  function setBusy(value) {
    busy = value;
    input.disabled = value;
    sendButton.disabled = value;
  }

  async function submitMessage(message) {
    addMessage("user", message);
    var replyNode = addMessage("assistant", "", "typing");
    setBusy(true);
    try {
      if (!publicKey || !tenantId) throw new Error("Widget chưa có public key hoặc tenant ID.");
      var response = await fetch(apiUrl + "/chat?stream=true", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Public-Key": publicKey },
        body: JSON.stringify({
          tenant_id: tenantId,
          conversation_id: conversationId,
          message: message,
          history: history.slice(-30),
          config_version: configVersion
        })
      });
      if (!response.ok) throw new Error(await readError(response));
      var finalResponse = await consumeSse(response, replyNode);
      var finalReply = finalResponse && finalResponse.reply ? finalResponse.reply : replyNode.textContent;
      replyNode.textContent = finalReply;
      history.push({ role: "user", content: message }, { role: "assistant", content: finalReply });
    } catch (error) {
      replyNode.classList.add("error");
      replyNode.textContent = error instanceof Error ? error.message : "Không kết nối được máy chủ.";
    } finally {
      replyNode.classList.remove("typing");
      setBusy(false);
      input.focus();
    }
  }

  async function consumeSse(response, replyNode) {
    if (!response.body) throw new Error("Trình duyệt không hỗ trợ streaming response.");
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    var fullReply = "";
    var finalResponse = null;
    while (true) {
      var part = await reader.read();
      buffer += decoder.decode(part.value || new Uint8Array(), { stream: !part.done });
      var blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      blocks.forEach(function (block) {
        block.split("\n").forEach(function (line) {
          if (line.indexOf("data: ") !== 0) return;
          var event = JSON.parse(line.slice(6));
          if (event.type === "delta") {
            fullReply += event.delta || "";
            replyNode.textContent = fullReply;
            messages.scrollTop = messages.scrollHeight;
          } else if (event.type === "done") {
            finalResponse = event.response;
          }
        });
      });
      if (part.done) break;
    }
    return finalResponse;
  }

  async function readError(response) {
    try {
      var payload = await response.json();
      return payload.detail ? "API: " + payload.detail : "API trả lỗi " + response.status;
    } catch (_) {
      return "API trả lỗi " + response.status;
    }
  }

  function createConversationId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (char) {
      var random = Math.random() * 16 | 0;
      return (char === "x" ? random : random & 3 | 8).toString(16);
    });
  }
  }
})();
