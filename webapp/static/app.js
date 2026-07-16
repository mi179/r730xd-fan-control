(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const dom = {
    connectionPill: $("connectionPill"),
    connectionPillText: $("connectionPillText"),
    loginLink: $("loginLink"),
    logoutButton: $("logoutButton"),
    cacheStatus: $("cacheStatus"),
    refreshSummaryButton: $("refreshSummaryButton"),
    openSensorsButton: $("openSensorsButton"),
    temperatureValue: $("temperatureValue"),
    temperatureUnit: $("temperatureUnit"),
    temperatureDetail: $("temperatureDetail"),
    temperatureHealth: $("temperatureHealth"),
    fanValue: $("fanValue"),
    fanUnit: $("fanUnit"),
    fanDetail: $("fanDetail"),
    fanHealth: $("fanHealth"),
    powerValue: $("powerValue"),
    powerUnit: $("powerUnit"),
    powerDetail: $("powerDetail"),
    powerHealth: $("powerHealth"),
    temperatureDelta: $("temperatureDelta"),
    fanDelta: $("fanDelta"),
    powerDelta: $("powerDelta"),
    trendCanvas: $("trendCanvas"),
    trendEmpty: $("trendEmpty"),
    trendDescription: $("trendDescription"),
    currentSampleTime: $("currentSampleTime"),
    currentTemperature: $("currentTemperature"),
    currentFan: $("currentFan"),
    currentPower: $("currentPower"),
    previousSampleTime: $("previousSampleTime"),
    previousTemperature: $("previousTemperature"),
    previousFan: $("previousFan"),
    previousPower: $("previousPower"),
    previous2SampleTime: $("previous2SampleTime"),
    previous2Temperature: $("previous2Temperature"),
    previous2Fan: $("previous2Fan"),
    previous2Power: $("previous2Power"),
    controlGate: $("controlGate"),
    authenticatedWorkspace: $("authenticatedWorkspace"),
    operatorName: $("operatorName"),
    operatorSensorsButton: $("operatorSensorsButton"),
    modeBadge: $("modeBadge"),
    interlockToggle: $("interlockToggle"),
    interlockWarning: $("interlockWarning"),
    manualModeButton: $("manualModeButton"),
    autoModeButton: $("autoModeButton"),
    connectionState: $("connectionState"),
    openConnectionButton: $("openConnectionButton"),
    fanControlState: $("fanControlState"),
    gaugeProgress: $("gaugeProgress"),
    gaugeValue: $("gaugeValue"),
    dialHint: $("dialHint"),
    presetGrid: $("presetGrid"),
    fanSlider: $("fanSlider"),
    sliderValue: $("sliderValue"),
    applyFanButton: $("applyFanButton"),
    clearLogButton: $("clearLogButton"),
    eventLog: $("eventLog"),
    connectionDialog: $("connectionDialog"),
    connectionForm: $("connectionForm"),
    hostInput: $("hostInput"),
    usernameInput: $("usernameInput"),
    passwordInput: $("passwordInput"),
    verifyTlsInput: $("verifyTlsInput"),
    toggleConnectionPassword: $("toggleConnectionPassword"),
    saveConnectionButton: $("saveConnectionButton"),
    sensorsDialog: $("sensorsDialog"),
    refreshAllSensorsButton: $("refreshAllSensorsButton"),
    sensorDialogMeta: $("sensorDialogMeta"),
    sensorSearchInput: $("sensorSearchInput"),
    sensorTypeFilter: $("sensorTypeFilter"),
    sensorTableBody: $("sensorTableBody"),
    toastRegion: $("toastRegion"),
  };

  const state = {
    authenticated: false,
    csrfToken: "",
    configured: false,
    online: false,
    manual: false,
    interlock: false,
    speed: 10,
    actionBusy: false,
    summaryBusy: false,
    deepScanBusy: false,
    deepScanStartedAt: 0,
    summaryAge: null,
    summaryReceivedAt: 0,
    summaryRefreshing: false,
    summaryStale: false,
    allSensors: [],
    historySamples: [],
    historyBusy: false,
    summaryPollTimer: null,
    deepScanPollTimer: null,
  };

  class ApiError extends Error {
    constructor(message, status = 0, code = "") {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.code = code;
    }
  }

  function unwrap(payload) {
    if (payload && typeof payload === "object" && Object.prototype.hasOwnProperty.call(payload, "data")) {
      return payload.data;
    }
    return payload;
  }

  async function api(path, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), options.timeout || 15000);
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
    }
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && state.csrfToken) {
      headers.set("X-CSRF-Token", state.csrfToken);
    }

    let response;
    try {
      response = await fetch(path, {
        method,
        headers,
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
    } catch (error) {
      if (error.name === "AbortError") {
        throw new ApiError("请求超时，iDRAC 可能暂时无响应");
      }
      throw new ApiError("无法连接本机控制服务");
    } finally {
      window.clearTimeout(timeout);
    }

    const raw = await response.text();
    let payload = null;
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch (_error) {
        payload = { message: raw };
      }
    }

    const envelopeError = payload && payload.ok === false ? payload.error : null;
    if (!response.ok || envelopeError) {
      const message = envelopeError?.message || payload?.message || `请求失败 (${response.status})`;
      if (response.status === 401 && path !== "/api/auth/login") {
        renderAuthentication({ authenticated: false });
      }
      throw new ApiError(message, response.status, envelopeError?.code || "");
    }

    return { data: unwrap(payload), status: response.status };
  }

  async function apiFallback(primary, fallback) {
    try {
      return await api(primary.path, primary.options);
    } catch (error) {
      if (!(error instanceof ApiError) || ![404, 405].includes(error.status) || !fallback) {
        throw error;
      }
      return api(fallback.path, fallback.options);
    }
  }

  function text(value, fallback = "--") {
    if (value === null || value === undefined || value === "") return fallback;
    return String(value);
  }

  function finite(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") {
      const match = value.replace(",", ".").match(/-?\d+(?:\.\d+)?/);
      if (match) {
        const parsed = Number(match[0]);
        return Number.isFinite(parsed) ? parsed : null;
      }
    }
    return null;
  }

  function firstFinite(source, keys) {
    if (!source || typeof source !== "object") return null;
    for (const key of keys) {
      const number = finite(source[key]);
      if (number !== null) return number;
    }
    return null;
  }

  function bool(source, keys, fallback = false) {
    if (!source || typeof source !== "object") return fallback;
    for (const key of keys) {
      if (typeof source[key] === "boolean") return source[key];
      if (source[key] === 1 || source[key] === "true") return true;
      if (source[key] === 0 || source[key] === "false") return false;
    }
    return fallback;
  }

  function healthClass(status) {
    const value = text(status, "unknown").toLowerCase();
    if (["ok", "good", "normal", "nominal", "enabled", "present", "ns"].some((word) => value.includes(word))) {
      return "is-ok";
    }
    if (["warn", "non-critical", "degraded", "caution"].some((word) => value.includes(word))) {
      return "is-warning";
    }
    if (["crit", "fail", "error", "fatal", "alarm"].some((word) => value.includes(word))) {
      return "is-critical";
    }
    return "is-unknown";
  }

  function worstHealth(items) {
    let worst = "UNKNOWN";
    let rank = 0;
    for (const item of items || []) {
      const candidate = text(item?.status ?? item?.state ?? item?.health, "UNKNOWN");
      const css = healthClass(candidate);
      const candidateRank = css === "is-critical" ? 3 : css === "is-warning" ? 2 : css === "is-ok" ? 1 : 0;
      if (candidateRank > rank) {
        rank = candidateRank;
        worst = candidate;
      }
    }
    return worst;
  }

  function setHealth(element, status) {
    const normalized = text(status, "UNKNOWN").toUpperCase();
    element.textContent = normalized.length > 14 ? normalized.slice(0, 14) : normalized;
    element.className = `health-tag ${healthClass(status)}`;
  }

  function formatNumber(value, digits = 0) {
    const number = finite(value);
    if (number === null) return "--";
    return new Intl.NumberFormat("zh-CN", {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    }).format(number);
  }

  function updateMetric(elements, metric) {
    elements.value.textContent = metric.value === null ? "--" : formatNumber(metric.value, metric.digits || 0);
    elements.unit.textContent = metric.unit;
    elements.detail.textContent = metric.detail;
    setHealth(elements.health, metric.status);
  }

  function normalizeSummary(payload) {
    const container = payload?.telemetry || payload?.summary || payload || {};
    const temperatures = Array.isArray(container.temperatures)
      ? container.temperatures
      : Array.isArray(container.temperature)
        ? container.temperature
        : [];
    const fans = Array.isArray(container.fans)
      ? container.fans
      : Array.isArray(container.fan)
        ? container.fan
        : [];

    const temperatureReadings = temperatures
      .map((item) => ({
        item,
        value: firstFinite(item, ["celsius", "temperature_c", "value", "reading"]),
      }))
      .filter((entry) => entry.value !== null);
    const temperatureValues = temperatureReadings.map((entry) => entry.value);
    const fallbackTemperature = firstFinite(container, ["temperature_c", "temp_c", "temperature"]);
    const maxTemperature = temperatureValues.length ? Math.max(...temperatureValues) : fallbackTemperature;
    const hottest = temperatureReadings.reduce(
      (current, entry) => (!current || entry.value > current.value ? entry : current),
      null,
    )?.item || null;

    const fanValues = fans
      .map((item) => firstFinite(item, ["rpm", "speed_rpm", "value", "reading"]))
      .filter((value) => value !== null);
    const fallbackFan = firstFinite(container, ["fan_rpm", "rpm", "fan_speed"]);
    const fanAverage = fanValues.length
      ? fanValues.reduce((total, value) => total + value, 0) / fanValues.length
      : fallbackFan;

    const powerSource = container.power && typeof container.power === "object" ? container.power : container;
    const power = firstFinite(powerSource, ["consumed_watts", "watts", "power_watts", "power_w", "value", "reading"]);

    return {
      temperature: {
        value: maxTemperature,
        unit: "°C",
        digits: maxTemperature !== null && maxTemperature % 1 ? 1 : 0,
        detail: temperatureValues.length
          ? `最高值 · ${text(hottest?.name, `${temperatureValues.length} 个温度传感器`)}`
          : "暂无温度读数",
        status: hottest?.status || worstHealth(temperatures),
      },
      fan: {
        value: fanAverage,
        unit: "RPM",
        digits: 0,
        detail: fanValues.length
          ? `平均值 · ${formatNumber(Math.min(...fanValues))}–${formatNumber(Math.max(...fanValues))} RPM`
          : "暂无风扇转速读数",
        status: worstHealth(fans),
      },
      power: {
        value: power,
        unit: "W",
        digits: power !== null && power % 1 ? 1 : 0,
        detail: power !== null ? "系统实时输入功耗" : "暂无功耗读数",
        status: powerSource.status || powerSource.health || (power !== null ? "OK" : "UNKNOWN"),
      },
    };
  }

  function renderSummary(payload) {
    const summary = normalizeSummary(payload);
    updateMetric(
      {
        value: dom.temperatureValue,
        unit: dom.temperatureUnit,
        detail: dom.temperatureDetail,
        health: dom.temperatureHealth,
      },
      summary.temperature,
    );
    updateMetric(
      { value: dom.fanValue, unit: dom.fanUnit, detail: dom.fanDetail, health: dom.fanHealth },
      summary.fan,
    );
    updateMetric(
      { value: dom.powerValue, unit: dom.powerUnit, detail: dom.powerDetail, health: dom.powerHealth },
      summary.power,
    );
  }

  function renderNoTelemetry(detail = "等待 iDRAC 传感器") {
    const metrics = [
      [dom.temperatureValue, dom.temperatureDetail, dom.temperatureHealth],
      [dom.fanValue, dom.fanDetail, dom.fanHealth],
      [dom.powerValue, dom.powerDetail, dom.powerHealth],
    ];
    for (const [value, description, health] of metrics) {
      value.textContent = "--";
      description.textContent = detail;
      setHealth(health, "UNKNOWN");
    }
  }

  function setCacheAge(payload) {
    const age = finite(payload?.age_seconds ?? payload?.cache_age_seconds ?? payload?.telemetry?.age_seconds);
    const observedAt = payload?.telemetry?.observed_at || payload?.observed_at || payload?.updated_at;
    if (age !== null) {
      state.summaryAge = Math.max(0, age);
    } else if (observedAt) {
      const timestamp = Date.parse(observedAt);
      state.summaryAge = Number.isNaN(timestamp) ? 0 : Math.max(0, (Date.now() - timestamp) / 1000);
    } else {
      state.summaryAge = 0;
    }
    state.summaryReceivedAt = Date.now();
    state.summaryRefreshing = Boolean(payload?.refreshing);
    state.summaryStale = Boolean(payload?.stale);
    updateCacheLabel();
  }

  function currentCacheAge() {
    if (state.summaryAge === null || !state.summaryReceivedAt) return null;
    return Math.max(0, state.summaryAge + (Date.now() - state.summaryReceivedAt) / 1000);
  }

  function updateCacheLabel() {
    const age = currentCacheAge();
    if (age === null) {
      dom.cacheStatus.textContent = state.summaryRefreshing ? "正在首次读取…" : "等待数据";
      return;
    }
    const ageText = age < 1 ? "刚刚" : `${Math.round(age)} 秒前`;
    const suffix = state.summaryRefreshing ? " · 后台更新中" : state.summaryStale ? " · 数据可能已过期" : " · 缓存秒开";
    dom.cacheStatus.textContent = `${ageText}${suffix}`;
  }

  function sampleNumber(sample, key) {
    return sample && typeof sample === "object" ? finite(sample[key]) : null;
  }

  function sampleTime(sample) {
    if (!sample?.timestamp) return "--:--:--";
    const date = new Date(sample.timestamp);
    if (Number.isNaN(date.getTime())) return "--:--:--";
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(date);
  }

  function snapshotValue(sample, key, unit) {
    const value = sampleNumber(sample, key);
    if (value === null) return "--";
    const digits = key === "max_temp_c" && value % 1 ? 1 : 0;
    return `${formatNumber(value, digits)} ${unit}`;
  }

  function renderSnapshot(sample, elements) {
    elements.time.textContent = sampleTime(sample);
    elements.temperature.textContent = snapshotValue(sample, "max_temp_c", "°C");
    elements.fan.textContent = snapshotValue(sample, "avg_fan_rpm", "RPM");
    elements.power.textContent = snapshotValue(sample, "power_watts", "W");
  }

  function renderDelta(element, current, previous, key, unit) {
    const currentValue = sampleNumber(current, key);
    const previousValue = sampleNumber(previous, key);
    if (currentValue === null || previousValue === null) {
      element.textContent = "—";
      element.className = "metric-delta is-flat";
      return;
    }
    const delta = currentValue - previousValue;
    const threshold = key === "avg_fan_rpm" ? 1 : 0.05;
    if (Math.abs(delta) < threshold) {
      element.textContent = "→ 0";
      element.className = "metric-delta is-flat";
      return;
    }
    const digits = key === "max_temp_c" && Math.abs(delta) < 10 ? 1 : 0;
    element.textContent = `${delta > 0 ? "↑" : "↓"} ${formatNumber(Math.abs(delta), digits)} ${unit}`;
    element.className = `metric-delta ${delta > 0 ? "is-up" : "is-down"}`;
  }

  function recentFiveMinutes(samples) {
    if (!samples.length) return [];
    const parsed = samples.map((sample) => ({ sample, time: Date.parse(sample?.timestamp || "") }));
    const validTimes = parsed.map((entry) => entry.time).filter(Number.isFinite);
    if (!validTimes.length) return samples.slice(-24);
    const latest = Math.max(...validTimes);
    const recent = parsed
      .filter((entry) => Number.isFinite(entry.time) && entry.time >= latest - 5 * 60 * 1000)
      .map((entry) => entry.sample);
    return recent.length >= 2 ? recent : samples.slice(-24);
  }

  function drawTrend(samples = state.historySamples) {
    const chartSamples = recentFiveMinutes(samples);
    const series = [
      { key: "max_temp_c", color: "#ffb23e" },
      { key: "avg_fan_rpm", color: "#35b8ff" },
      { key: "power_watts", color: "#55d98b" },
    ].map((definition) => ({
      ...definition,
      points: chartSamples
        .map((sample, index) => ({ index, value: sampleNumber(sample, definition.key) }))
        .filter((point) => point.value !== null),
    }));
    const drawable = series.filter((item) => item.points.length >= 2);
    const hasTrend = drawable.length > 0 && chartSamples.length >= 2;
    dom.trendEmpty.hidden = hasTrend;
    dom.trendCanvas.hidden = !hasTrend;
    if (!hasTrend) {
      dom.trendDescription.textContent = "正在积累至少两次遥测数据";
      return;
    }

    const rect = dom.trendCanvas.getBoundingClientRect();
    const width = Math.max(320, Math.round(rect.width || 720));
    const height = Math.max(180, Math.round(rect.height || 220));
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    dom.trendCanvas.width = Math.round(width * ratio);
    dom.trendCanvas.height = Math.round(height * ratio);
    const context = dom.trendCanvas.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    const inset = { left: 18, right: 18, top: 16, bottom: 22 };
    const chartWidth = width - inset.left - inset.right;
    const chartHeight = height - inset.top - inset.bottom;
    context.strokeStyle = "rgba(126, 174, 208, 0.12)";
    context.lineWidth = 1;
    for (let row = 0; row <= 4; row += 1) {
      const y = inset.top + (chartHeight * row) / 4;
      context.beginPath();
      context.moveTo(inset.left, y);
      context.lineTo(width - inset.right, y);
      context.stroke();
    }

    for (const item of drawable) {
      const values = item.points.map((point) => point.value);
      let minimum = Math.min(...values);
      let maximum = Math.max(...values);
      if (maximum === minimum) {
        const padding = Math.max(1, Math.abs(maximum) * 0.04);
        minimum -= padding;
        maximum += padding;
      }
      context.strokeStyle = item.color;
      context.lineWidth = 2.4;
      context.lineJoin = "round";
      context.lineCap = "round";
      context.beginPath();
      item.points.forEach((point, pointIndex) => {
        const x = inset.left + (chartWidth * point.index) / Math.max(1, chartSamples.length - 1);
        const y = inset.top + chartHeight * (1 - (point.value - minimum) / (maximum - minimum));
        if (pointIndex === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();
      const last = item.points[item.points.length - 1];
      const lastX = inset.left + (chartWidth * last.index) / Math.max(1, chartSamples.length - 1);
      const lastY = inset.top + chartHeight * (1 - (last.value - minimum) / (maximum - minimum));
      context.fillStyle = item.color;
      context.beginPath();
      context.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
      context.fill();
    }
    dom.trendDescription.textContent = `趋势图包含最近 ${chartSamples.length} 次遥测采样，分别显示温度、风扇转速和功耗变化`;
  }

  function renderHistory(payload = {}) {
    const samples = Array.isArray(payload.samples) ? payload.samples.filter((item) => item && typeof item === "object") : [];
    state.historySamples = samples;
    const current = payload.current || samples.at(-1) || null;
    const previous = payload.previous || samples.at(-2) || null;
    const previous2 = payload.previous2 || samples.at(-3) || null;
    renderSnapshot(current, {
      time: dom.currentSampleTime,
      temperature: dom.currentTemperature,
      fan: dom.currentFan,
      power: dom.currentPower,
    });
    renderSnapshot(previous, {
      time: dom.previousSampleTime,
      temperature: dom.previousTemperature,
      fan: dom.previousFan,
      power: dom.previousPower,
    });
    renderSnapshot(previous2, {
      time: dom.previous2SampleTime,
      temperature: dom.previous2Temperature,
      fan: dom.previous2Fan,
      power: dom.previous2Power,
    });
    renderDelta(dom.temperatureDelta, current, previous, "max_temp_c", "°C");
    renderDelta(dom.fanDelta, current, previous, "avg_fan_rpm", "RPM");
    renderDelta(dom.powerDelta, current, previous, "power_watts", "W");
    drawTrend(samples);
  }

  async function refreshHistory(options = {}) {
    if (state.historyBusy) return;
    state.historyBusy = true;
    try {
      const response = await api("/api/telemetry/history", { timeout: 8000 });
      renderHistory(response.data || {});
    } catch (error) {
      if (!options.quiet) toast(error.message, "error");
    } finally {
      state.historyBusy = false;
    }
  }

  function renderStatus(payload) {
    const connection = payload?.connection || payload || {};
    const control = payload?.control || payload || {};
    const telemetry = payload?.telemetry || {};
    state.configured = bool(connection, ["configured", "ready", "password_set"], state.configured);
    state.online = bool(connection, ["online", "connected"], bool(telemetry, ["available"], false));

    const mode = text(control.mode ?? payload?.mode, "unknown").toLowerCase();
    state.manual = mode === "manual" || bool(control, ["manual", "manual_mode"], false);
    state.interlock = bool(control, ["safety_unlocked", "interlock", "unlocked"], state.interlock);
    const speed = firstFinite(control, ["percent", "speed", "fan_percent"]);
    if (speed !== null) setSpeed(speed, false);

    dom.connectionPill.className = `status-pill ${state.online ? "is-online" : state.configured ? "is-neutral" : "is-offline"}`;
    dom.connectionPillText.textContent = state.online ? "IDRAC ONLINE" : state.configured ? "IDRAC READY" : "SETUP REQUIRED";
    dom.connectionState.textContent = state.online ? "ONLINE" : state.configured ? "READY" : "SETUP REQUIRED";
    dom.connectionState.classList.toggle("is-ready", state.configured);

    dom.modeBadge.textContent = state.manual ? "MANUAL" : mode === "auto" || mode === "automatic" ? "AUTO" : "UNKNOWN";
    dom.modeBadge.className = `mode-badge ${state.manual ? "is-manual" : mode === "auto" || mode === "automatic" ? "is-auto" : "is-unknown"}`;
    dom.interlockToggle.checked = state.interlock;
    dom.interlockWarning.hidden = !state.interlock;

    if (telemetry?.error) {
      addLog("WARN", text(telemetry.error));
    }
    renderControlAvailability();
  }

  function renderControlAvailability() {
    const fanEnabled = state.authenticated && state.configured && state.manual && state.interlock && !state.actionBusy;
    dom.manualModeButton.disabled = !state.authenticated || !state.configured || !state.interlock || state.actionBusy || state.manual;
    dom.autoModeButton.disabled = !state.authenticated || !state.configured || state.actionBusy;
    dom.interlockToggle.disabled = !state.authenticated || !state.configured || state.actionBusy;
    dom.fanSlider.disabled = !fanEnabled;
    dom.applyFanButton.disabled = !fanEnabled;
    dom.presetGrid.querySelectorAll("button[data-speed]").forEach((button) => {
      button.disabled = !fanEnabled;
    });
    dom.openSensorsButton.disabled = !state.authenticated || !state.configured;
    dom.operatorSensorsButton.disabled = !state.authenticated || !state.configured;
    dom.openConnectionButton.disabled = !state.authenticated;
    dom.refreshSummaryButton.disabled = state.summaryBusy;
    dom.fanControlState.textContent = fanEnabled ? `ACTIVE · ${state.speed}%` : "LOCKED";
    dom.fanControlState.className = `output-state ${fanEnabled ? "is-active" : ""}`;
    dom.dialHint.textContent = fanEnabled
      ? "可发送输出"
      : !state.authenticated
        ? "需要 iDRAC 授权"
        : !state.interlock
          ? "请先解除联锁"
          : !state.manual
            ? "请启用手动模式"
            : "暂不可用";
  }

  function setSpeed(rawSpeed, updateSlider = true) {
    const speed = Math.max(5, Math.min(100, Math.round(finite(rawSpeed) ?? 10)));
    state.speed = speed;
    if (updateSlider) dom.fanSlider.value = String(speed);
    dom.sliderValue.textContent = `${speed}%`;
    dom.gaugeValue.textContent = String(speed);
    const progress = Math.max(0, Math.min(100, ((speed - 5) / 95) * 100));
    dom.gaugeProgress.setAttribute("stroke-dasharray", `${progress} 100`);
    dom.gaugeProgress.classList.toggle("is-warm", speed > 30 && speed < 60);
    dom.gaugeProgress.classList.toggle("is-hot", speed >= 60);
    dom.presetGrid.querySelectorAll("button[data-speed]").forEach((button) => {
      button.classList.toggle("is-selected", Number(button.dataset.speed) === speed);
    });
    if (state.manual && state.interlock) {
      dom.fanControlState.textContent = `ACTIVE · ${speed}%`;
    }
  }

  function nowTime() {
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date());
  }

  function addLog(level, message) {
    const row = document.createElement("div");
    const time = document.createElement("time");
    const badge = document.createElement("span");
    const copy = document.createElement("span");
    const normalized = String(level || "INFO").toUpperCase();
    const classMap = { INFO: "log-info", SEND: "log-send", OK: "log-ok", WARN: "log-warn", ERROR: "log-error" };
    time.textContent = nowTime();
    badge.textContent = `[${normalized}]`;
    badge.className = classMap[normalized] || "log-info";
    copy.textContent = text(message, "--");
    row.append(time, badge, copy);
    dom.eventLog.append(row);
    while (dom.eventLog.children.length > 150) dom.eventLog.firstElementChild?.remove();
    dom.eventLog.scrollTop = dom.eventLog.scrollHeight;
  }

  function toast(message, kind = "info") {
    const item = document.createElement("div");
    item.className = `toast ${kind === "error" ? "is-error" : kind === "success" ? "is-success" : ""}`;
    item.textContent = text(message);
    dom.toastRegion.append(item);
    window.setTimeout(() => item.remove(), 4200);
  }

  function renderAuthentication(sessionData = {}) {
    state.authenticated = Boolean(sessionData.authenticated);
    state.csrfToken = state.authenticated ? text(sessionData.csrf_token, "") : "";
    dom.controlGate.hidden = state.authenticated;
    dom.authenticatedWorkspace.hidden = !state.authenticated;
    dom.loginLink.hidden = state.authenticated;
    dom.logoutButton.hidden = !state.authenticated;
    dom.openSensorsButton.hidden = !state.authenticated;
    if (state.authenticated) {
      dom.operatorName.textContent = `${text(sessionData.username, "root")} · iDRAC 操作员`;
    }
    renderControlAvailability();
  }

  async function loadSession() {
    try {
      const response = await api("/api/auth/session", { timeout: 7000 });
      renderAuthentication(response.data || {});
      return state.authenticated;
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        renderAuthentication({ authenticated: false });
        return false;
      }
      throw error;
    }
  }

  async function refreshStatus(options = {}) {
    try {
      const response = await api("/api/status", { timeout: 8000 });
      renderStatus(response.data || {});
    } catch (error) {
      if (!options.quiet) {
        addLog("ERROR", error.message);
        toast(error.message, "error");
      }
    }
  }

  async function refreshSummary(options = {}) {
    if (state.summaryBusy) return;
    if (!state.configured && !options.initial) {
      renderNoTelemetry("请先完成 iDRAC 连接设置");
      return;
    }
    state.summaryBusy = true;
    dom.refreshSummaryButton.disabled = true;
    const previousText = dom.refreshSummaryButton.textContent;
    if (!options.quiet) dom.refreshSummaryButton.textContent = "读取中…";
    try {
      const query = options.force ? "?refresh=1" : "";
      const response = await apiFallback(
        { path: `/api/telemetry/summary${query}`, options: { timeout: 12000 } },
        { path: `/api/sensors/summary${query}`, options: { timeout: 12000 } },
      );
      const payload = response.data || {};
      const telemetry = payload.telemetry || payload.summary || (payload.temperatures || payload.fans || payload.power ? payload : null);
      setCacheAge(payload);
      if (telemetry) {
        renderSummary(payload);
        state.online = true;
      } else {
        renderNoTelemetry(payload.refreshing ? "首次读取中，请稍候…" : "暂无传感器数据");
      }
      if (!options.quiet) {
        addLog("OK", payload.refreshing ? "已显示缓存，后台正在更新传感器" : "温度、转速与功耗已刷新");
      }
    } catch (error) {
      renderNoTelemetry(error.message);
      if (!options.quiet) {
        addLog("ERROR", error.message);
        toast(error.message, "error");
      }
    } finally {
      state.summaryBusy = false;
      dom.refreshSummaryButton.textContent = previousText;
      renderControlAvailability();
    }
  }

  async function setInterlock(enabled) {
    if (state.actionBusy) return;
    state.actionBusy = true;
    renderControlAvailability();
    try {
      await api("/api/control/interlock", { method: "POST", body: { enabled } });
      state.interlock = enabled;
      dom.interlockWarning.hidden = !enabled;
      addLog(enabled ? "WARN" : "OK", enabled ? "安全联锁已解除" : "安全联锁已恢复");
    } catch (error) {
      state.interlock = !enabled;
      dom.interlockToggle.checked = state.interlock;
      dom.interlockWarning.hidden = !state.interlock;
      addLog("ERROR", error.message);
      toast(error.message, "error");
    } finally {
      state.actionBusy = false;
      renderControlAvailability();
    }
  }

  async function setMode(mode) {
    if (state.actionBusy) return;
    if (mode === "manual" && !state.interlock) {
      toast("请先解除安全联锁", "error");
      return;
    }
    state.actionBusy = true;
    renderControlAvailability();
    addLog("SEND", mode === "manual" ? "启用手动风扇控制" : "恢复 iDRAC 自动温控");
    try {
      const endpoint = mode === "manual" ? "/api/control/manual" : "/api/control/auto";
      await api(endpoint, { method: "POST", body: mode === "manual" ? { confirmed: true } : {} });
      state.manual = mode === "manual";
      addLog("OK", state.manual ? "手动控制已启用" : "自动温控已恢复");
      toast(state.manual ? "手动控制已启用" : "自动温控已恢复", "success");
      await refreshStatus({ quiet: true });
    } catch (error) {
      addLog("ERROR", error.message);
      toast(error.message, "error");
    } finally {
      state.actionBusy = false;
      renderControlAvailability();
    }
  }

  async function applySpeed(speed) {
    if (state.actionBusy || !state.manual || !state.interlock) return;
    const value = Math.max(5, Math.min(100, Math.round(finite(speed) ?? state.speed)));
    state.actionBusy = true;
    renderControlAvailability();
    addLog("SEND", `设置风扇输出 ${value}%`);
    try {
      await apiFallback(
        { path: "/api/control/speed", options: { method: "POST", body: { percent: value } } },
        { path: "/api/control/fan", options: { method: "POST", body: { percent: value, speed: value } } },
      );
      setSpeed(value);
      addLog("OK", `风扇输出已设置为 ${value}%`);
      toast(`风扇输出 ${value}%`, "success");
    } catch (error) {
      addLog("ERROR", error.message);
      toast(error.message, "error");
    } finally {
      state.actionBusy = false;
      renderControlAvailability();
    }
  }

  async function openConnectionDialog() {
    dom.passwordInput.value = "";
    dom.passwordInput.type = "password";
    dom.toggleConnectionPassword.textContent = "显示";
    try {
      const response = await apiFallback(
        { path: "/api/config", options: { timeout: 7000 } },
        { path: "/api/connection", options: { timeout: 7000 } },
      );
      const config = response.data?.connection || response.data || {};
      dom.hostInput.value = text(config.host ?? config.ip, "");
      dom.usernameInput.value = text(config.username ?? config.user, "root");
      dom.verifyTlsInput.checked = bool(config, ["redfish_verify_tls", "verify_tls"], false);
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) {
        toast(error.message, "error");
      }
    }
    dom.connectionDialog.showModal();
    window.setTimeout(() => dom.hostInput.focus(), 50);
  }

  async function saveConnection(event) {
    event.preventDefault();
    if (!dom.connectionForm.reportValidity()) return;
    const password = dom.passwordInput.value;
    const configBody = {
      host: dom.hostInput.value.trim(),
      username: dom.usernameInput.value.trim(),
      redfish_verify_tls: dom.verifyTlsInput.checked,
    };
    if (password) configBody.password = password;

    dom.saveConnectionButton.disabled = true;
    dom.saveConnectionButton.textContent = "保存中…";
    addLog("SEND", "更新 iDRAC 连接设置");
    try {
      try {
        await api("/api/config", { method: "PUT", body: configBody, timeout: 12000 });
      } catch (error) {
        if (!(error instanceof ApiError) || ![404, 405].includes(error.status)) throw error;
        await api("/api/connection", {
          method: "POST",
          body: {
            host: configBody.host,
            username: configBody.username,
            password,
          },
          timeout: 12000,
        });
      }

      dom.passwordInput.value = "";
      dom.connectionDialog.close();
      state.configured = true;
      addLog("OK", "连接设置已保存，正在测试 iDRAC");
      toast("设置已保存，正在测试连接", "success");
      try {
        await api("/api/connection/test", { method: "POST", body: {}, timeout: 20000 });
        addLog("OK", "iDRAC 连接测试成功");
        toast("iDRAC 已连接", "success");
      } catch (error) {
        if (!(error instanceof ApiError) || ![404, 405].includes(error.status)) {
          addLog("ERROR", `连接测试：${error.message}`);
          toast(error.message, "error");
        }
      }
      await refreshStatus({ quiet: true });
      await refreshSummary({ force: true });
    } catch (error) {
      addLog("ERROR", error.message);
      toast(error.message, "error");
    } finally {
      dom.saveConnectionButton.disabled = false;
      dom.saveConnectionButton.textContent = "保存并测试";
    }
  }

  function inferSensorType(record) {
    const explicit = text(record?.category ?? record?.type ?? record?.sensor_type, "").toLowerCase();
    const combined = `${explicit} ${text(record?.name, "")} ${text(record?.unit, "")}`.toLowerCase();
    if (/temp|thermal|degree|celsius|°c/.test(combined)) return "temperature";
    if (/fan|rpm|tach/.test(combined)) return "fan";
    if (/power|watt|psu|current/.test(combined)) return "power";
    if (/volt/.test(combined)) return "voltage";
    return "other";
  }

  function normalizeSensor(record, index) {
    const name = text(record?.name ?? record?.sensor ?? record?.id, `Sensor ${index + 1}`);
    const type = inferSensorType(record);
    const rawValue = record?.reading ?? record?.value ?? record?.reading_value ?? record?.current;
    const unit = text(record?.unit ?? record?.units, "");
    let reading = text(rawValue, "--");
    if (rawValue !== null && rawValue !== undefined && unit && !String(rawValue).toLowerCase().includes(unit.toLowerCase())) {
      reading = `${reading} ${unit}`;
    }
    return {
      name,
      type,
      typeLabel: text(record?.type_label ?? record?.category ?? record?.type ?? record?.sensor_type, {
        temperature: "温度",
        fan: "风扇",
        power: "功耗",
        voltage: "电压",
        other: "其他",
      }[type]),
      reading,
      status: text(record?.status ?? record?.state ?? record?.health, "UNKNOWN"),
    };
  }

  function renderSensorRows() {
    const query = dom.sensorSearchInput.value.trim().toLowerCase();
    const type = dom.sensorTypeFilter.value;
    const filtered = state.allSensors.filter((sensor) => {
      const typeMatch = type === "all" || sensor.type === type;
      const queryMatch = !query || `${sensor.name} ${sensor.typeLabel} ${sensor.reading} ${sensor.status}`.toLowerCase().includes(query);
      return typeMatch && queryMatch;
    });

    const fragment = document.createDocumentFragment();
    if (!filtered.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.className = "empty-table";
      cell.textContent = state.allSensors.length ? "没有匹配的传感器" : "暂无传感器记录";
      row.append(cell);
      fragment.append(row);
    } else {
      for (const sensor of filtered) {
        const row = document.createElement("tr");
        const name = document.createElement("td");
        const typeCell = document.createElement("td");
        const reading = document.createElement("td");
        const status = document.createElement("td");
        const statusBadge = document.createElement("span");
        name.className = "sensor-name";
        typeCell.className = "sensor-type";
        reading.className = "sensor-reading";
        statusBadge.className = `sensor-state ${healthClass(sensor.status)}`;
        name.textContent = sensor.name;
        typeCell.textContent = sensor.typeLabel;
        reading.textContent = sensor.reading;
        statusBadge.textContent = sensor.status;
        status.append(statusBadge);
        row.append(name, typeCell, reading, status);
        fragment.append(row);
      }
    }
    dom.sensorTableBody.replaceChildren(fragment);
    if (state.allSensors.length) {
      dom.sensorDialogMeta.textContent = `显示 ${filtered.length} / ${state.allSensors.length} 条记录`;
    }
  }

  function acceptSensorRecords(payload) {
    const result = payload?.result || payload || {};
    const records = result.records || result.sensors || result.items || (Array.isArray(result) ? result : []);
    state.allSensors = Array.isArray(records) ? records.map(normalizeSensor) : [];
    renderSensorRows();
    addLog("OK", `全部传感器读取完成，共 ${state.allSensors.length} 条`);
  }

  function scheduleDeepScanPoll(delay = 1000) {
    window.clearTimeout(state.deepScanPollTimer);
    state.deepScanPollTimer = window.setTimeout(pollDeepScan, delay);
  }

  async function pollDeepScan() {
    try {
      if (state.deepScanStartedAt && Date.now() - state.deepScanStartedAt > 90000) {
        throw new ApiError("完整传感器扫描超时，请稍后重试");
      }
      const response = await api("/api/sensors/deep-scan", { timeout: 12000 });
      const payload = response.data || {};
      const status = text(payload.status ?? payload.result?.status, "running").toLowerCase();
      if (["complete", "completed", "done", "success", "succeeded"].includes(status) || payload.result?.records) {
        acceptSensorRecords(payload);
        state.deepScanBusy = false;
        state.deepScanStartedAt = 0;
        dom.refreshAllSensorsButton.disabled = false;
        dom.refreshAllSensorsButton.textContent = "刷新";
        return;
      }
      if (["failed", "error", "cancelled"].includes(status)) {
        throw new ApiError(text(payload.error?.message ?? payload.error, "全部传感器读取失败"));
      }
      dom.sensorDialogMeta.textContent = "iDRAC 正在扫描全部 SDR 记录…";
      scheduleDeepScanPoll(1100);
    } catch (error) {
      state.deepScanBusy = false;
      state.deepScanStartedAt = 0;
      dom.refreshAllSensorsButton.disabled = false;
      dom.refreshAllSensorsButton.textContent = "刷新";
      dom.sensorDialogMeta.textContent = error.message;
      addLog("ERROR", error.message);
      toast(error.message, "error");
    }
  }

  async function loadAllSensors(force = false) {
    if (state.deepScanBusy) return;
    if (!force && state.allSensors.length) {
      renderSensorRows();
      return;
    }
    state.deepScanBusy = true;
    state.deepScanStartedAt = Date.now();
    dom.refreshAllSensorsButton.disabled = true;
    dom.refreshAllSensorsButton.textContent = "扫描中…";
    dom.sensorDialogMeta.textContent = "正在启动完整传感器扫描…";
    addLog("SEND", "读取全部 iDRAC 传感器");
    try {
      let response;
      try {
        response = await api("/api/sensors/deep-scan", { method: "POST", body: {}, timeout: 12000 });
      } catch (error) {
        if (!(error instanceof ApiError) || ![404, 405].includes(error.status)) throw error;
        response = await api("/api/sensors/all?refresh=1", { timeout: 45000 });
        acceptSensorRecords(response.data || {});
        state.deepScanBusy = false;
        state.deepScanStartedAt = 0;
        dom.refreshAllSensorsButton.disabled = false;
        dom.refreshAllSensorsButton.textContent = "刷新";
        return;
      }
      const payload = response.data || {};
      if (payload.result?.records || payload.records || payload.sensors || payload.items || Array.isArray(payload)) {
        acceptSensorRecords(payload);
        state.deepScanBusy = false;
        state.deepScanStartedAt = 0;
        dom.refreshAllSensorsButton.disabled = false;
        dom.refreshAllSensorsButton.textContent = "刷新";
      } else {
        dom.sensorDialogMeta.textContent = "扫描已在后台启动，主界面不会卡住…";
        scheduleDeepScanPoll(500);
      }
    } catch (error) {
      state.deepScanBusy = false;
      state.deepScanStartedAt = 0;
      dom.refreshAllSensorsButton.disabled = false;
      dom.refreshAllSensorsButton.textContent = "刷新";
      dom.sensorDialogMeta.textContent = error.message;
      addLog("ERROR", error.message);
      toast(error.message, "error");
    }
  }

  async function logout() {
    dom.logoutButton.disabled = true;
    try {
      await api("/api/auth/logout", { method: "POST", body: {} });
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) {
        toast(error.message, "error");
        dom.logoutButton.disabled = false;
        return;
      }
    }
    renderAuthentication({ authenticated: false });
    window.location.replace("/");
  }

  function bindEvents() {
    dom.logoutButton.addEventListener("click", logout);
    dom.refreshSummaryButton.addEventListener("click", () => refreshSummary({ force: true }));
    dom.openSensorsButton.addEventListener("click", () => {
      dom.sensorsDialog.showModal();
      loadAllSensors(false);
    });
    dom.operatorSensorsButton.addEventListener("click", () => {
      dom.sensorsDialog.showModal();
      loadAllSensors(false);
    });
    dom.refreshAllSensorsButton.addEventListener("click", () => loadAllSensors(true));
    dom.sensorSearchInput.addEventListener("input", renderSensorRows);
    dom.sensorTypeFilter.addEventListener("change", renderSensorRows);
    dom.interlockToggle.addEventListener("change", () => setInterlock(dom.interlockToggle.checked));
    dom.manualModeButton.addEventListener("click", () => setMode("manual"));
    dom.autoModeButton.addEventListener("click", () => setMode("auto"));
    dom.openConnectionButton.addEventListener("click", openConnectionDialog);
    dom.connectionForm.addEventListener("submit", saveConnection);
    dom.toggleConnectionPassword.addEventListener("click", () => {
      const reveal = dom.passwordInput.type === "password";
      dom.passwordInput.type = reveal ? "text" : "password";
      dom.toggleConnectionPassword.textContent = reveal ? "隐藏" : "显示";
      dom.toggleConnectionPassword.setAttribute("aria-label", reveal ? "隐藏密码" : "显示密码");
    });
    dom.fanSlider.addEventListener("input", () => setSpeed(dom.fanSlider.value, false));
    dom.applyFanButton.addEventListener("click", () => applySpeed(dom.fanSlider.value));
    dom.presetGrid.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-speed]");
      if (button && !button.disabled) applySpeed(button.dataset.speed);
    });
    dom.clearLogButton.addEventListener("click", () => dom.eventLog.replaceChildren());

    document.querySelectorAll("[data-close-dialog]").forEach((button) => {
      button.addEventListener("click", () => {
        const dialog = button.closest("dialog");
        if (dialog) dialog.close();
      });
    });
    document.querySelectorAll("dialog").forEach((dialog) => {
      dialog.addEventListener("click", (event) => {
        if (event.target !== dialog) return;
        const rect = dialog.getBoundingClientRect();
        const inside = event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
        if (!inside) dialog.close();
      });
    });
  }

  async function start() {
    bindEvents();
    setSpeed(10);
    updateCacheLabel();
    renderAuthentication({ authenticated: false });
    drawTrend([]);
    try {
      await loadSession();
      await Promise.all([
        refreshStatus({ quiet: true }),
        refreshSummary({ initial: true, quiet: true }),
        refreshHistory({ quiet: true }),
      ]);
      if (state.authenticated) addLog("INFO", "iDRAC 控制权限已解锁");
    } catch (error) {
      addLog("ERROR", error.message);
      toast(error.message, "error");
    }

    state.summaryPollTimer = window.setInterval(() => {
      refreshStatus({ quiet: true });
      refreshSummary({ quiet: true });
      refreshHistory({ quiet: true });
    }, 5000);
    window.setInterval(() => updateCacheLabel(), 1000);

    let resizeTimer = 0;
    const redraw = () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => drawTrend(), 100);
    };
    if ("ResizeObserver" in window) {
      const observer = new ResizeObserver(redraw);
      observer.observe(dom.trendCanvas.parentElement);
    } else {
      window.addEventListener("resize", redraw, { passive: true });
    }
  }

  start();
})();
