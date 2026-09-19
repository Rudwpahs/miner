const nf = new Intl.NumberFormat("ko-KR");
const df = new Intl.DateTimeFormat("ko-KR", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : df.format(date);
}

function renderHistory(points) {
  const root = document.querySelector("#history-chart");
  root.replaceChildren();
  const values = Array.isArray(points) ? points : [];
  const ceiling = Math.max(1, ...values.map((point) => point.collected));

  for (const point of values) {
    const item = document.createElement("div");
    item.className = "history-item";

    const value = document.createElement("strong");
    value.textContent = nf.format(point.collected);

    const rail = document.createElement("div");
    rail.className = "history-rail";
    const bar = document.createElement("div");
    bar.className = "history-bar";
    bar.style.height = `${Math.max(4, Math.round((point.collected / ceiling) * 100))}%`;
    rail.appendChild(bar);

    const date = document.createElement("span");
    date.textContent = point.date.slice(5).replace("-", "/");

    item.append(value, rail, date);
    root.appendChild(item);
  }

  if (!values.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "아직 표시할 일별 데이터가 없습니다.";
    root.appendChild(empty);
  }
}

function renderTimes(status) {
  document.querySelector("#last-miner-run").textContent = formatTime(status.last_miner_run_at);
  document.querySelector("#last-distillation-success").textContent =
    formatTime(status.last_distillation_success_at);
  document.querySelector("#generated-at").textContent = formatTime(status.generated_at);
}

function renderStatus(value) {
  const root = document.querySelector("#system-status");
  root.textContent = value;
  root.closest(".status-wrap").dataset.state = value;
}

function renderError(error) {
  console.error("Miner Live refresh failed", error);
  renderStatus("STALE");
  document.querySelector("#generated-at").textContent = "갱신 실패";
}

async function refresh() {
  const response = await fetch(`status.json?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`status ${response.status}`);
  const status = await response.json();

  document.querySelector("#collected-total").textContent = nf.format(status.collected_total);
  document.querySelector("#distillation-pending").textContent =
    nf.format(status.distillation_pending);
  document.querySelector("#distillation-success").textContent =
    nf.format(status.distillation_success);
  document.querySelector("#today-progress-label").textContent =
    `${nf.format(status.today_collected)} / ${nf.format(status.daily_target)}`;

  const progress = document.querySelector("#today-progress");
  progress.max = status.daily_target;
  progress.value = Math.min(status.today_collected, status.daily_target);

  renderHistory(status.history_7d);
  renderTimes(status);
  renderStatus(status.system_status);
}

refresh().catch(renderError);
setInterval(() => refresh().catch(renderError), 60_000);
