export function initAdmin({ $, $$, json, getEpoch, getUser, openDialog, openSettings, onChange, toast }) {
  const dialog = $("#admin-dialog"), form = $("#admin-invite-form"), input = $("#admin-invite-code");
  let generation = 0, busy = false, current = null;

  function authorized() { return getUser()?.is_admin === true; }
  function controls() {
    $$("#admin-invite-form input, #admin-invite-form button").forEach((node) => { node.disabled = busy || !current; });
    $("#admin-invite-copy").disabled = busy || !current?.enabled || !current.code || input.value !== current.code;
    $("#admin-invite-disable").disabled = busy || !current?.enabled;
    form.setAttribute("aria-busy", String(busy));
  }
  function clear() {
    generation++; busy = false; current = null;
    input.value = "";
    $("#admin-invite-status").textContent = "尚未读取";
    $("#admin-invite-updated").textContent = "";
    $("#admin-invite-error").textContent = "";
    controls();
  }
  function reset() {
    clear();
    $$("[data-admin-entry]").forEach((button) => button.classList.add("hidden"));
    if (dialog.open) dialog.close();
  }
  function syncUser() {
    if (!authorized()) { reset(); return; }
    $$("[data-admin-entry]").forEach((button) => button.classList.remove("hidden"));
  }
  function render(data) {
    current = data;
    input.value = data.code || "";
    $("#admin-invite-status").textContent = data.enabled
      ? data.code ? "注册已开放 · 需要邀请码" : "注册已开放 · 尚未设置邀请码"
      : "注册已停用 · 已有账户仍可登录";
    const updated = data.updated_at ? new Date(data.updated_at * 1000) : null;
    $("#admin-invite-updated").textContent = updated && Number.isFinite(updated.getTime())
      ? "最近更新：" + updated.toLocaleString("zh-CN") : "";
    controls();
  }
  async function request(url, method, body, success) {
    if (!authorized()) { reset(); return; }
    const epoch = getEpoch(), version = generation;
    const valid = () => epoch === getEpoch() && version === generation && authorized() && dialog.open;
    busy = true; controls(); $("#admin-invite-error").textContent = "";
    try {
      const data = await json(url, method, body);
      if (!valid()) return;
      render(data);
      if (success) { onChange(data); toast(success); }
    } catch (error) {
      if (!valid()) return;
      if (error.status === 403) { reset(); toast("当前账户没有管理员权限。", true); }
      else $("#admin-invite-error").textContent = error.message;
    } finally { if (valid()) { busy = false; controls(); } }
  }
  async function open() {
    if (!authorized()) return;
    clear(); openDialog("#admin-dialog");
    $("#admin-invite-status").textContent = "正在读取邀请码…";
    await request("/api/admin/invite", "GET");
  }

  $$("[data-admin-entry]").forEach((button) => { button.onclick = open; });
  dialog.addEventListener("close", clear);
  input.addEventListener("input", controls);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !current) return;
    await request("/api/admin/invite", "PUT", { enabled: true, code: input.value.trim() },
      "邀请码已保存并启用。请将当前邀请码发给需要注册的人。");
  });
  $("#admin-invite-rotate").onclick = async () => {
    if (busy || !current) return;
    await request("/api/admin/invite/rotate", "POST", {}, "新邀请码已生成并启用，之前的邀请码已失效。");
  };
  $("#admin-invite-disable").onclick = async () => {
    if (busy || !current?.enabled) return;
    await request("/api/admin/invite", "PUT", { enabled: false, code: current.code || "" },
      "新用户注册已停用，已有账户仍可正常登录。");
  };
  $("#admin-invite-copy").onclick = async () => {
    if (!authorized() || busy || !current?.enabled || !current.code || input.value !== current.code) return;
    const epoch = getEpoch(), version = generation;
    try {
      await navigator.clipboard.writeText(current.code);
      if (epoch === getEpoch() && version === generation && authorized()) toast("已复制邀请码，可粘贴发送给需要注册的人。");
    } catch {
      if (epoch !== getEpoch() || version !== generation || !authorized()) return;
      input.focus(); input.select();
      $("#admin-invite-error").textContent = "浏览器未允许复制，已选中邀请码，请手动复制。";
    }
  };
  $("#admin-password-settings").onclick = async () => {
    if (!authorized()) return;
    const epoch = getEpoch();
    dialog.close();
    try {
      await openSettings();
      if (epoch !== getEpoch() || !authorized()) return;
      $("#password-details").open = true;
      $("#old-password").focus();
    } catch (error) { if (epoch === getEpoch()) toast(error.message, true); }
  };
  reset();
  return { reset, syncUser, open };
}
