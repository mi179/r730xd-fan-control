(() => {
  "use strict";

  const form = document.getElementById("loginForm");
  const username = document.getElementById("loginUsername");
  const password = document.getElementById("loginPassword");
  const loginButton = document.getElementById("loginButton");
  const errorBox = document.getElementById("loginError");
  const togglePassword = document.getElementById("toggleLoginPassword");
  const idleButtonText = "验证并解锁控制";

  function showError(message) {
    errorBox.textContent = message || "登录失败";
    errorBox.hidden = false;
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.hidden = true;
  }

  async function parseResponse(response) {
    const raw = await response.text();
    let payload = null;
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch (_error) {
        payload = { message: raw };
      }
    }
    const apiError = payload?.ok === false ? payload.error : null;
    if (!response.ok || apiError) {
      throw new Error(apiError?.message || payload?.message || `登录失败 (${response.status})`);
    }
    return payload?.data ?? payload ?? {};
  }

  async function checkSession() {
    try {
      const response = await fetch("/api/auth/session", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const data = await parseResponse(response);
      if (data.authenticated) window.location.replace("/");
    } catch (_error) {
      // The login form remains usable if the optional session probe fails.
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    clearError();
    loginButton.disabled = true;
    loginButton.textContent = "正在验证…";
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          username: username.value.trim(),
          password: password.value,
        }),
      });
      const data = await parseResponse(response);
      if (data.authenticated === false) throw new Error("用户名或密码不正确");
      password.value = "";
      window.location.replace("/");
    } catch (error) {
      showError(error.message === "Failed to fetch" ? "无法连接本机控制服务" : error.message);
      password.focus();
      password.select();
    } finally {
      loginButton.disabled = false;
      loginButton.textContent = idleButtonText;
    }
  });

  togglePassword.addEventListener("click", () => {
    const reveal = password.type === "password";
    password.type = reveal ? "text" : "password";
    togglePassword.textContent = reveal ? "隐藏" : "显示";
    togglePassword.setAttribute("aria-label", reveal ? "隐藏密码" : "显示密码");
    password.focus();
  });

  checkSession();
})();
