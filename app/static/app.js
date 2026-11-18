"use strict";

const element = id => document.getElementById(id);
const controls = {
  machine: element("machine"), start: element("outage-start"), end: element("outage-end"),
  replan: element("replan"), form: element("outage-form"),
  jobId: element("job-id"), jobArrival: element("job-arrival"), jobDue: element("job-due"),
  jobMachine: element("job-machine"), jobDuration: element("job-duration"),
  addJob: element("add-job"), urgentForm: element("urgent-form"),
  history: element("history"), loadHead: element("load-head"), newWorkspace: element("new-workspace"),
  optimized: element("optimized-view"), fifo: element("fifo-view"),
};
const state = { example: null, head: null, snapshot: null, versions: [], view: "optimized", busy: false };
const storageKey = "production-scheduler.workspace";
const eventLabels = { initial: "İlk plan", outage: "Makine arızası", urgent_job: "Acil sipariş" };
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
    const error = new Error(detail ?? (response.status === 422
      ? "Girdi değerlerini kontrol et."
      : `İstek tamamlanamadı (HTTP ${response.status}).`));
    error.status = response.status;
    throw error;
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
  const readOnly = state.snapshot?.version !== state.head?.version;
  const editingDisabled = state.busy || !state.head || readOnly;
  const outageDisabled = editingDisabled || Boolean(state.head?.outage);
  for (const input of [controls.machine, controls.start, controls.end, controls.replan]) {
    input.disabled = outageDisabled;
  }
  for (const input of [controls.jobId, controls.jobArrival, controls.jobDue, controls.jobMachine, controls.jobDuration, controls.addJob]) {
    input.disabled = editingDisabled;
  }
  controls.history.disabled = state.busy || !state.snapshot;
  controls.loadHead.disabled = state.busy || !state.head;
  controls.newWorkspace.disabled = state.busy;
  controls.optimized.disabled = state.busy || !state.snapshot;
  controls.fifo.disabled = state.busy || !state.snapshot?.result.baseline;
  controls.optimized.setAttribute("aria-pressed", String(state.view === "optimized"));
  controls.fifo.setAttribute("aria-pressed", String(state.view === "fifo"));
  element("chart").setAttribute("aria-busy", String(state.busy));
  controls.replan.textContent = state.head?.outage ? "Arıza kaydedildi" : "Yeniden planla";
}

function showMetrics() {
  const optimized = state.snapshot?.result.metrics;
  const baseline = state.snapshot?.result.baseline?.metrics;
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
  const { result, scenario, outage, event_type: eventType, event_time: eventTime } = state.snapshot ?? {};
  const plan = state.view === "fifo" ? result?.baseline : result;
  const operations = plan?.operations ?? [];
  const interruptions = result?.interruptions ?? [];
  const caption = [];
  if (state.view === "fifo") caption.push("FIFO");
  if (outage) caption.push(`${outage.machine_id} arızası ${number.format(outage.start)}–${minutes(outage.end)}`);
  if (eventType === "urgent_job") caption.push(`Acil sipariş ${minutes(eventTime)}`);
  if (!outage && eventType !== "urgent_job") caption.push("Vardiya başlangıcından itibaren dakika");
  element("plan-caption").textContent = caption.join(" · ");
  element("interruption-legend").hidden = !interruptions.length;
  element("operation-detail").textContent = operations.length ? "Bir operasyon seçerek ayrıntılarını gör." : "";
  chart.replaceChildren();
  chart.removeAttribute("tabindex");
  if (!operations.length) {
    chart.append(node("p", "empty", "Görüntülenecek operasyon bulunamadı."));
    return;
  }

  const end = Math.max(1, result.metrics?.makespan ?? 0, result.baseline?.metrics?.makespan ?? 0, outage?.end ?? 0);
  const { horizon, ticks } = chartScale(end);
  const grid = node("div", "chart-grid");
  const shortestOperation = Math.min(...operations.map(operation => operation.end - operation.start));
  const labelWidth = 28;
  const machineLabelWidth = 98;
  grid.style.minWidth = `${Math.min(1900, Math.max(590, machineLabelWidth + Math.ceil(horizon * labelWidth / shortestOperation)))}px`;
  grid.append(chartRow("Dakika", ticks, horizon, true).row);
  const jobColors = new Map(scenario.jobs.map((job, index) => [job.id, colors[index % colors.length]]));
  for (const machine of scenario.machines) {
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
  const snapshot = state.snapshot;
  setStatus(snapshot?.result.status ?? "unknown");
  if (snapshot) {
    const { scenario } = snapshot;
    const operationCount = scenario.jobs.reduce((total, job) => total + job.operations.length, 0);
    element("scenario-summary").textContent = `${scenario.machines.length} makine · ${scenario.jobs.length} sipariş · ${operationCount} operasyon`;
    element("version-note").textContent = snapshot.version === state.head.version ? "Güncel plan" : "Salt okunur sürüm";
    controls.history.value = String(snapshot.version);
  }
  showMetrics();
  drawChart();
  syncButtons();
}

async function run(action) {
  if (state.busy) return;
  state.busy = true;
  setError();
  setStatus("loading");
  syncButtons();
  try {
    await action();
  } catch (error) {
    setError(error.message);
    if (!state.snapshot) {
      setStatus("error");
      element("scenario-summary").textContent = "Çizelge yüklenemedi.";
      element("chart").replaceChildren(node("p", "empty", "Yeniden denemek için sayfayı yenile veya yeni senaryo oluştur."));
    }
  } finally {
    state.busy = false;
    if (state.snapshot) showPlan();
    else syncButtons();
  }
}

function workspacePath(id = state.head.workspace_id) {
  return `/api/workspaces/${encodeURIComponent(id)}`;
}

function storedWorkspace(id) {
  try {
    if (id) localStorage.setItem(storageKey, id);
    return localStorage.getItem(storageKey);
  } catch {
    return null;
  }
}

function populateMachines(select, machines, preferred) {
  const selected = machines.includes(preferred) ? preferred : machines[0];
  const options = machines.map(machine => {
    const option = node("option", "", machine);
    option.value = machine;
    return option;
  });
  select.replaceChildren(...options);
  select.value = selected;
}

function showVersions() {
  const options = state.versions.map(version => {
    const time = version.event_type === "initial" ? "" : ` · ${minutes(version.event_time)}`;
    const option = node("option", "", `Sürüm ${version.version} · ${eventLabels[version.event_type]}${time}`);
    option.value = String(version.version);
    return option;
  });
  controls.history.replaceChildren(...options);
}

function setSnapshot(snapshot) {
  state.snapshot = snapshot;
  state.view = "optimized";
}

function nextUrgentId() {
  const existing = new Set(state.head.scenario.jobs.map(job => job.id));
  let index = 1;
  while (existing.has(`ACIL-${String(index).padStart(2, "0")}`)) index++;
  return `ACIL-${String(index).padStart(2, "0")}`;
}

async function setHead(snapshot) {
  const newWorkspace = state.head?.workspace_id !== snapshot.workspace_id;
  state.head = snapshot;
  setSnapshot(snapshot);
  storedWorkspace(snapshot.workspace_id);
  const { machines } = snapshot.scenario;
  if (newWorkspace) {
    state.versions = [];
    controls.form.reset();
    controls.urgentForm.reset();
    populateMachines(controls.machine, machines, "M2");
    populateMachines(controls.jobMachine, machines, "M1");
  }
  if (snapshot.outage) {
    controls.machine.value = snapshot.outage.machine_id;
    controls.start.value = snapshot.outage.start;
    controls.end.value = snapshot.outage.end;
  } else {
    controls.start.value = Math.max(snapshot.event_time, Number(controls.start.value));
    controls.end.value = Math.max(Number(controls.start.value) + 1, Number(controls.end.value));
  }
  controls.start.min = snapshot.event_time;
  controls.jobArrival.min = snapshot.event_time;
  controls.jobArrival.value = Math.max(snapshot.event_time, Number(controls.jobArrival.value));
  controls.jobDue.value = Math.max(Number(controls.jobArrival.value), Number(controls.jobDue.value));
  controls.jobId.value = nextUrgentId();
  state.versions = [snapshot, ...state.versions.filter(version => version.version !== snapshot.version)];
  showVersions();
  try {
    state.versions = await request(`${workspacePath()}/versions`);
    showVersions();
  } catch {
    setError("Plan yüklendi ancak sürüm listesi alınamadı. Güncel planı yükleyerek tekrar dene.");
  }
}

async function createWorkspace() {
  state.example ??= await request("/api/scenario");
  await setHead(await request("/api/workspaces", { scenario: state.example }));
}

function edit(event) {
  if (state.snapshot?.version !== state.head?.version || !state.head) return;
  run(async () => {
    try {
      const snapshot = await request(`${workspacePath()}/events`, { expected_version: state.head.version, ...event });
      await setHead(snapshot);
    } catch (error) {
      if (error.status === 409) {
        setError(`${error.message} Güncel planı yükle ve tekrar dene.`);
        return;
      }
      throw error;
    }
  });
}

controls.form.addEventListener("submit", event => {
  event.preventDefault();
  if (controls.replan.disabled) return;
  const start = controls.start.valueAsNumber;
  const end = controls.end.valueAsNumber;
  if (end <= start) {
    setError("Bitiş dakikası başlangıçtan büyük olmalı.");
    controls.end.focus();
    return;
  }
  const outage = { machine_id: controls.machine.value, start, end };
  edit({ event_type: "outage", event_time: start, outage });
});

controls.urgentForm.addEventListener("submit", event => {
  event.preventDefault();
  if (controls.addJob.disabled) return;
  const id = controls.jobId.value.trim();
  if (!id) {
    setError("Sipariş kodu gir.");
    controls.jobId.focus();
    return;
  }
  const job = {
    id,
    due: controls.jobDue.valueAsNumber,
    operations: [{ id: "O1", duration: controls.jobDuration.valueAsNumber, machines: [controls.jobMachine.value] }],
  };
  edit({ event_type: "urgent_job", event_time: controls.jobArrival.valueAsNumber, job });
});

controls.newWorkspace.addEventListener("click", () => run(createWorkspace));
controls.loadHead.addEventListener("click", () => run(async () => setHead(await request(workspacePath()))));
controls.history.addEventListener("change", () => {
  const selected = Number(controls.history.value);
  run(async () => {
    const snapshot = selected === state.head.version
      ? state.head
      : await request(`${workspacePath()}/versions/${selected}`);
    setSnapshot(snapshot);
  });
});

for (const view of ["optimized", "fifo"]) {
  controls[view].addEventListener("click", () => {
    state.view = view;
    drawChart();
    syncButtons();
  });
}

async function initialize() {
  const id = storedWorkspace();
  if (id) {
    try {
      await setHead(await request(workspacePath(id)));
      return;
    } catch (error) {
      if (error.status !== 404) throw error;
    }
  }
  await createWorkspace();
}

run(initialize);
