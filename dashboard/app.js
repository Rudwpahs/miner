const nf = new Intl.NumberFormat("ko-KR");
const df = new Intl.DateTimeFormat("ko-KR", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const COMPONENT_LABELS = {
  OPERATIONAL: "Operational",
  COLLECTING: "Collecting",
  DELAYED: "Delayed",
  DEGRADED: "Degraded",
  UNAVAILABLE: "Unavailable",
  UNKNOWN: "Unknown",
};

const SUMMARY_COPY = {
  OPERATIONAL: {
    title: "정상 운영 중",
    description: "Miner 및 확인 가능한 처리 시스템이 정상입니다.",
  },
  DEGRADED: {
    title: "일부 처리 지연",
    description: "수집 상태는 계속 표시하며 일부 처리 상태를 확인하고 있습니다.",
  },
  PARTIAL_OUTAGE: {
    title: "일부 시스템 장애",
    description: "일부 핵심 시스템의 상태를 현재 확인할 수 없습니다.",
  },
  STALE: {
    title: "상태 갱신 지연",
    description: "마지막으로 확인된 상태를 표시하고 있습니다.",
  },
};

const REASON_COPY = {
  NONE: "",
  STATE_UNAVAILABLE: "현재 Distillation 상태 데이터를 확인할 수 없습니다.",
  STATE_MALFORMED: "Distillation 상태 데이터 검증에 문제가 있습니다.",
  NO_RECENT_SUCCESS: "최근 증류 성공 기록이 확인되지 않았습니다.",
  PAGE_STALE: "상태 페이지 갱신이 지연되고 있습니다.",
};

function formatTime(value) {
  if (!value) return "확인 불가";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "확인 불가" : df.format(date);
}

function formatNullable(value) {
  if (value === null || value === undefined) return "확인 불가";
  return nf.format(value);
}

function renderComponent(name, component) {
  const state = component?.status ?? "UNKNOWN";
  document.querySelector(`#${name}-state`).textContent = COMPONENT_LABELS[state] ?? "Unknown";
  const row = document.querySelector(`[data-component="${name}"]`);
  row.dataset.state = state;
}

function renderSummary(state) {
  const safeState = SUMMARY_COPY[state] ? state : "STALE";
  const copy = SUMMARY_COPY[safeState];
  const panel = document.querySelector("#summary-status");
  panel.dataset.state = safeState;
  document.querySelector("#summary-title").textContent = copy.title;
  document.querySelector("#summary-description").textContent = copy.description;
}

function renderIncident(status) {
  const panel = document.querySelector("#incident-panel");
  if (status.summary_status === "OPERATIONAL") {
    panel.hidden = true;
    return;
  }

  panel.hidden = false;
  const reason = status.distillation?.reason ?? "NONE";
  const reasonCopy = REASON_COPY[reason] || "일부 시스템 상태를 확인하고 있습니다.";
  document.querySelector("#incident-title").textContent = SUMMARY_COPY[status.summary_status]?.title
    ?? "상태 확인 필요";
  document.querySelector("#incident-copy").textContent = reasonCopy;
}

function renderHistory(points) {
  const root = document.querySelector("#history-strip");
  root.replaceChildren();
  const values = Array.isArray(points) ? points : [];
  const ceiling = Math.max(1, ...values.map((point) => point.collected));

  if (!values.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "아직 표시할 최근 수집 기록이 없습니다.";
    root.appendChild(empty);
    return;
  }

  for (const point of values) {
    const item = document.createElement("div");
    item.className = "history-day";

    const value = document.createElement("span");
    value.className = "history-value";
    value.textContent = nf.format(point.collected);

    const track = document.createElement("div");
    track.className = "history-track";
    const bar = document.createElement("div");
    bar.className = "history-fill";
    bar.style.width = `${Math.max(6, Math.round((point.collected / ceiling) * 100))}%`;
    track.appendChild(bar);

    const date = document.createElement("span");
    date.className = "history-date";
    date.textContent = point.date.slice(5).replace("-", "/");

    item.append(value, track, date);
    root.appendChild(item);
  }
}

function renderStatus(status) {
  renderSummary(status.summary_status);
  renderComponent("miner", status.miner);
  renderComponent("distillation", status.distillation);
  renderComponent("corpus", status.corpus);

  document.querySelector("#miner-today").textContent =
    `오늘 ${nf.format(status.miner.today_collected)} / ${nf.format(status.miner.daily_target)}`;
  document.querySelector("#miner-total").textContent = nf.format(status.miner.collected_total);
  document.querySelector("#miner-last").textContent = formatTime(status.miner.last_success_at);
  document.querySelector("#distillation-pending").textContent =
    formatNullable(status.distillation.pending);
  document.querySelector("#distillation-last").textContent =
    formatTime(status.distillation.last_success_at);
  document.querySelector("#corpus-total").textContent = formatNullable(status.corpus.accepted_total);
  document.querySelector("#generated-at").textContent = formatTime(status.generated_at);

  renderIncident(status);
  renderHistory(status.history_7d);
}

function renderError(error) {
  console.error("HoopHub Status refresh failed", error);
  renderSummary("STALE");
  const panel = document.querySelector("#incident-panel");
  panel.hidden = false;
  document.querySelector("#incident-title").textContent = "상태 갱신 지연";
  document.querySelector("#incident-copy").textContent = REASON_COPY.PAGE_STALE;
  document.querySelector("#generated-at").textContent = "갱신 실패";
}

async function refresh() {
  const response = await fetch(`status.json?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`status ${response.status}`);
  const status = await response.json();
  renderStatus(status);
}

document.querySelector("#refresh-button").addEventListener("click", () => {
  refresh().catch(renderError);
});

refresh().catch(renderError);
setInterval(() => refresh().catch(renderError), 60_000);
