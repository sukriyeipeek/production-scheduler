"use strict";

const element = id => document.getElementById(id);
const controls = {
  machine: element("machine"), start: element("outage-start"), end: element("outage-end"),
  replan: element("replan"), reset: element("reset-plan"), form: element("outage-form"),
  optimized: element("optimized-view"), fifo: element("fifo-view"),
};
const state = { scenario: null, initial: null, result: null, outage: null, view: "optimized", busy: false };
const statusLabels = {
  optimal: "En iyi çözüm", feasible: "Uygulanabilir çözüm", infeasible: "Uygulanabilir plan yok",
  unknown: "Sonuç alınamadı", loading: "Hesaplanıyor", error: "İşlem başarısız",
};
const colors = [
  ["#cfeee2", "#175647"], ["#cce8f2", "#174c67"], ["#e8def6", "#523671"],
  ["#f3e7c6", "#604a19"], ["#dceadb", "#315831"], ["#f4dfe4", "#78374d"],
];
const number = new Intl.NumberFormat("tr-TR");
const minutes = value => Number.isFinite(value) ? `${number.format(value)} dk` : "—";

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

async function request(path, body) {
  const options = body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : {};
  const response = await fetch(path, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = typeof data.detail === "string" ? data.detail : null;
    if (detail) throw new Error(detail);
    if (response.status === 422) throw new Error("Arıza aralığını ve makineyi kontrol et.");
    throw new Error(`İstek tamamlanamadı (HTTP ${response.status}).`);
  }
  return response.json();
}

function setStatus(status) {
  element("status").textContent = statusLabels[status] ?? statusLabels.unknown;
  element("status").dataset.state = status;
}

function setError(message = "") {
  const error = element("error");
  error.textContent = message;
  error.hidden = !message;
}

function syncButtons() {
  controls.machine.disabled = state.busy || !state.scenario;
  controls.start.disabled = state.busy;
  controls.end.disabled = state.busy;
  controls.replan.disabled = state.busy || !state.scenario;
  controls.reset.disabled = state.busy || !state.initial || (!state.outage && state.result === state.initial);
  controls.optimized.disabled = state.busy || !state.result;
  controls.fifo.disabled = state.busy || !state.result?.baseline;
  controls.optimized.setAttribute("aria-pressed", String(state.view === "optimized"));
  controls.fifo.setAttribute("aria-pressed", String(state.view === "fifo"));
  element("chart").setAttribute("aria-busy", String(state.busy));
  controls.replan.textContent = state.busy ? "Hesaplanıyor…" : "Yeniden planla";
}

function showMetrics() {
  const optimized = state.result?.metrics;
  const baseline = state.result?.baseline?.metrics;
  for (const metric of ["makespan", "total_tardiness"]) {
    element(`optimized-${metric}`).textContent = minutes(optimized?.[metric]);
    element(`fifo-${metric}`).textContent = minutes(baseline?.[metric]);
    const delta = element(`delta-${metric}`);
    const difference = optimized?.[metric] - baseline?.[metric];
    delta.textContent = Number.isFinite(difference)
      ? `${difference > 0 ? "+" : difference < 0 ? "−" : ""}${minutes(Math.abs(difference))}` : "—";
    delta.dataset.delta = difference < 0 ? "better" : difference > 0 ? "worse" : "";
  }
}

function chartScale(end) {
  const targetStep = Math.max(1, end / 8);
  const base = 10 ** Math.floor(Math.log10(targetStep));
  const step = [1, 2, 5, 10].map(size => size * base).find(size => size >= targetStep);
  const horizon = Math.max(step, Math.ceil(end / step) * step);
  return { horizon, ticks: Array.from({ length: Math.round(horizon / step) + 1 }, (_, i) => i * step) };
}

function position(item, start, end, horizon) {
  item.style.left = `${100 * start / horizon}%`;
  if (end !== undefined) item.style.width = `${100 * (end - start) / horizon}%`;
}

function chartRow(label, ticks, horizon, isAxis = false) {
  const row = node("div", isAxis ? "chart-row axis" : "chart-row");
  const track = node("div", "track");
  row.append(node("div", "machine-label", label), track);
  for (const value of ticks) {
    const tick = node("span", "tick");
    tick.setAttribute("aria-hidden", "true");
    position(tick, value, undefined, horizon);
    if (isAxis) tick.append(node("b", "", number.format(value)));
    track.append(tick);
  }
  return { row, track };
}

function operationLabel(operation) {
  const { job_id, operation_id, machine_id, start, end } = operation;
  return `${job_id} / ${operation_id} · ${machine_id} · ${number.format(start)}–${minutes(end)}`;
}

function addOperation(track, operation, horizon, jobColors) {
  const details = operationLabel(operation);
  const button = node("button", "operation", operation.job_id);
  const [background, foreground] = jobColors.get(operation.job_id) ?? colors[0];
  button.type = "button";
  button.title = details;
  button.setAttribute("aria-label", details);
  button.style.setProperty("--job-bg", background);
  button.style.setProperty("--job-ink", foreground);
  const showDetail = () => { element("operation-detail").textContent = details; };
  button.addEventListener("focus", showDetail);
  button.addEventListener("click", showDetail);
  position(button, operation.start, operation.end, horizon);
  track.append(button);
}

function drawChart() {
  const chart = element("chart");
  const plan = state.view === "fifo" ? state.result?.baseline : state.result;
  const operations = plan?.operations ?? [];
  const interruptions = state.result?.interruptions ?? [];
  const outage = state.outage;
  const caption = outage
    ? `${outage.machine_id} arızası · ${number.format(outage.start)}–${minutes(outage.end)}`
    : "Vardiya başlangıcından itibaren dakika";
  element("plan-caption").textContent = state.view === "fifo" ? `FIFO · ${caption}` : caption;
  element("interruption-legend").hidden = !interruptions.length;
  element("operation-detail").textContent = operations.length ? "Bir operasyon seçerek ayrıntılarını gör." : "";
  chart.replaceChildren();
  chart.removeAttribute("tabindex");
  if (!operations.length) {
    chart.append(node("p", "empty", "Görüntülenecek operasyon bulunamadı."));
    return;
  }

  const end = Math.max(1, state.result.metrics?.makespan ?? 0, state.result.baseline?.metrics?.makespan ?? 0, outage?.end ?? 0);
  const { horizon, ticks } = chartScale(end);
  const grid = node("div", "chart-grid");
  const shortestOperation = Math.min(...operations.map(operation => operation.end - operation.start));
  const labelWidth = 28;
  const machineLabelWidth = 98;
  grid.style.minWidth = `${Math.min(1900, Math.max(590, machineLabelWidth + Math.ceil(horizon * labelWidth / shortestOperation)))}px`;
  grid.append(chartRow("Dakika", ticks, horizon, true).row);
  const jobColors = new Map(state.scenario.jobs.map((job, index) => [job.id, colors[index % colors.length]]));
  for (const machine of state.scenario.machines) {
    const { row, track } = chartRow(machine, ticks, horizon);
    if (outage?.machine_id === machine) {
      const interval = node("span", "outage");
      interval.title = `Arıza · ${number.format(outage.start)}–${minutes(outage.end)}`;
      position(interval, outage.start, outage.end, horizon);
      track.append(interval);
    }
    for (const cut of interruptions.filter(item => item.machine_id === machine)) {
      const marker = node("span", "interruption");
      marker.title = `${cut.job_id} / ${cut.operation_id} · ${minutes(cut.interrupted_at)} anında kesildi`;
      marker.setAttribute("role", "img");
      marker.setAttribute("aria-label", marker.title);
      position(marker, cut.start, cut.interrupted_at, horizon);
      track.append(marker);
    }
    const machineOperations = operations.filter(operation => operation.machine_id === machine);
    machineOperations.sort((first, second) => first.start - second.start);
    for (const operation of machineOperations) addOperation(track, operation, horizon, jobColors);
    grid.append(row);
  }
  chart.append(grid);
  chart.tabIndex = 0;
}

function showPlan() {
  setStatus(state.result?.status ?? "unknown");
  showMetrics();
  drawChart();
  syncButtons();
}

async function schedule(outage = null) {
  state.busy = true;
  setError();
  setStatus("loading");
  syncButtons();
  try {
    const body = outage ? { scenario: state.scenario, outage } : { scenario: state.scenario };
    state.result = await request("/api/schedule", body);
    state.outage = outage;
    state.view = "optimized";
    if (!outage) state.initial = state.result;
    showPlan();
  } catch (error) {
    setStatus("error");
    setError(error.message);
    if (!state.result) element("chart").replaceChildren(node("p", "empty", "İlk çizelge hesaplanamadı."));
  } finally {
    state.busy = false;
    syncButtons();
  }
}

controls.form.addEventListener("submit", event => {
  event.preventDefault();
  if (state.busy || !state.scenario) return;
  const start = controls.start.valueAsNumber;
  const end = controls.end.valueAsNumber;
  if (end <= start) {
    setError("Bitiş dakikası başlangıçtan büyük olmalı.");
    controls.end.focus();
    return;
  }
  schedule({ machine_id: controls.machine.value, start, end });
});

controls.reset.addEventListener("click", () => {
  if (!state.initial) return;
  state.result = state.initial;
  state.outage = null;
  state.view = "optimized";
  controls.form.reset();
  setError();
  showPlan();
});

for (const view of ["optimized", "fifo"]) {
  controls[view].addEventListener("click", () => {
    state.view = view;
    drawChart();
    syncButtons();
  });
}

async function initialize() {
  let scenario;
  try {
    scenario = await request("/api/scenario");
  } catch (error) {
    setStatus("error");
    setError(error.message);
    element("scenario-summary").textContent = "Örnek senaryo yüklenemedi.";
    element("chart").replaceChildren(node("p", "empty", "Senaryoyu yeniden yüklemek için sayfayı yenile."));
    return;
  }
  state.scenario = scenario;
  const operationCount = scenario.jobs.reduce((total, job) => total + job.operations.length, 0);
  element("scenario-summary").textContent = `${scenario.machines.length} makine · ${scenario.jobs.length} sipariş · ${operationCount} operasyon`;
  const defaultMachine = scenario.machines.includes("M2") ? "M2" : scenario.machines[0];
  for (const machine of scenario.machines) {
    const option = node("option", "", machine);
    option.value = machine;
    option.defaultSelected = machine === defaultMachine;
    controls.machine.append(option);
  }
  await schedule();
}

initialize();
