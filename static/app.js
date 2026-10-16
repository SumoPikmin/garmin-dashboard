/* Garmin dashboard front end — reads /api/dashboard and renders everything. */
const state = { days: 180, race: "Marathon", volUnit: "h", sport: "", data: null };
const charts = {};
const DAY = 86400000;
const SPORT_ORDER = ["run", "bike", "strength", "swim", "walk", "other"];
const SPORT_LABEL = { run: "Run", bike: "Bike", strength: "Strength", swim: "Swim", walk: "Walk/Hike", other: "Other" };

// ---------------------------------------------------------------- helpers
const $ = (s) => document.querySelector(s);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const ts = (iso) => new Date(iso + "T12:00:00").getTime();
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }));
const dash = (v) => (v === null || v === undefined || v === "" ? "—" : v);
const fmtDate = (t, long) => new Date(t).toLocaleDateString(undefined, long ? { day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "short" });
const fmtTime = (sec) => {
  if (sec == null) return "—";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
};
const fmtPace = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}/km` : "—");
const rolling = (arr, n = 7) => arr.map((p, i) => {
  const w = arr.slice(Math.max(0, i - n + 1), i + 1);
  return { x: p.x, y: w.reduce((a, b) => a + b.y, 0) / w.length };
});
const cutoff = () => Date.now() - state.days * DAY;
const inRange = (series, key = "date") => (series || []).filter((p) => ts(p[key]) >= cutoff());
const pts = (series, f = (p) => p.value) => inRange(series).map((p) => ({ x: ts(p.date), y: f(p) })).filter((p) => p.y != null);
const alpha = (hex, a) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
};
const STATUS = { good: "st-good", optimal: "st-good", warn: "st-warn", caution: "st-warn", low: "st-info", high: "st-bad", bad: "st-bad", info: "st-info" };
const statusChip = (level, text) => `<span class="status ${STATUS[level] || "st-info"}"><i></i>${esc(text)}</span>`;

// ---------------------------------------------------------------- chart scaffolding
function baseOptions({ yFmt = (v) => v, yMin, yMax, reverse = false, legend = false, mode = "nearest" } = {}) {
  const ink2 = css("--ink-2"), muted = css("--muted"), grid = css("--grid"), surface = css("--surface"), ink = css("--ink");
  return {
    responsive: true, maintainAspectRatio: false, animation: false, parsing: true, normalized: true,
    interaction: { mode, axis: "x", intersect: false },
    layout: { padding: { top: 4, right: 6 } },
    plugins: {
      legend: {
        display: legend, position: "top", align: "end",
        labels: { color: ink2, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "rectRounded", font: { size: 12 },
          filter: (item, data) => !data.datasets[item.datasetIndex].hideLegend },
      },
      tooltip: {
        backgroundColor: surface, titleColor: ink, bodyColor: ink2, borderColor: css("--border"), borderWidth: 1,
        padding: 9, boxPadding: 4, usePointStyle: true,
        filter: (item) => !item.dataset.hideLegend,
        callbacks: {
          title: (items) => (items.length ? fmtDate(items[0].parsed.x, true) : ""),
          label: (ctx) => ` ${ctx.dataset.label}: ${ctx.dataset.fmt ? ctx.dataset.fmt(ctx.raw) : yFmt(ctx.parsed.y)}`,
        },
      },
    },
    scales: {
      x: {
        type: "linear", grid: { display: false }, border: { color: css("--axis") },
        ticks: { color: muted, maxTicksLimit: 7, maxRotation: 0, callback: (v) => fmtDate(v) },
        min: cutoff(), max: Date.now() + DAY / 2,
      },
      y: {
        reverse, min: yMin, max: yMax, grid: { color: grid }, border: { display: false },
        ticks: { color: muted, maxTicksLimit: 6, callback: yFmt },
      },
    },
  };
}

function line(label, data, color, extra = {}) {
  return { type: "line", label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0,
    pointHoverRadius: 5, pointHoverBorderWidth: 2, pointHoverBorderColor: css("--surface"), tension: 0.25, borderCapStyle: "round",
    borderJoinStyle: "round", spanGaps: true, ...extra };
}

function bars(label, data, color, extra = {}) {
  return { type: "bar", label, data, backgroundColor: color, hoverBackgroundColor: color, borderRadius: 4,
    borderSkipped: "start", maxBarThickness: 24, barPercentage: 0.9, categoryPercentage: 0.9, ...extra };
}

function band(lowPts, highPts, color) {
  const shared = { type: "line", borderWidth: 0, pointRadius: 0, pointHoverRadius: 0, hideLegend: true, tension: 0.25, spanGaps: true };
  return [
    { ...shared, label: "band-low", data: lowPts, fill: false },
    { ...shared, label: "band-high", data: highPts, fill: "-1", backgroundColor: alpha(color, 0.12) },
  ];
}

function draw(id, datasets, opts = {}) {
  charts[id]?.destroy();
  const el = document.getElementById(id);
  if (!el) return;
  const card = el.closest(".chart-card");
  const hasData = datasets.some((d) => !d.hideLegend && d.data.length);
  if (card) card.classList.toggle("no-data", !hasData);
  const options = baseOptions({ legend: datasets.filter((d) => !d.hideLegend).length > 1, ...opts });
  if (opts.xBar) { // bar charts on a linear axis need explicit bar widths
    options.scales.x.offset = false;
    options.scales.x.min = cutoff() - 4 * DAY;
  }
  if (opts.stacked) { options.scales.x.stacked = true; options.scales.y.stacked = true; }
  if (opts.after) opts.after(options);
  charts[id] = new Chart(el, { data: { datasets }, options });
  if (!hasData) el.parentElement.dataset.empty = "No data in this range";
}

// ---------------------------------------------------------------- sections
function renderHero(d) {
  const v = d.vo2max;
  const ch = v.garmin_change_90d;
  $("#hero").innerHTML = `
    <div class="label">VO2max · Garmin</div>
    <div class="value">${v.garmin_current != null ? num(v.garmin_current, 1) : "—"}<small>ml/kg/min</small></div>
    <div class="detail">${v.category ? `${esc(v.category)} for your age` : ""}
      ${ch != null ? ` · <span class="${ch >= 0 ? "delta-up" : "delta-down"}">${ch >= 0 ? "▲" : "▼"} ${num(Math.abs(ch), 1)}</span> vs 90 days ago` : ""}</div>
    <div class="spark"><canvas id="c-spark"></canvas></div>
    <div class="row">
      <div><b>${num(v.hr_estimate_current, 1)}</b>HR-based estimate</div>
      <div><b>${num(v.vdot_current, 1)}</b>VDOT (race fitness)</div>
      <div><b>${num(v.uth_estimate, 1)}</b>Uth (HRmax/HRrest)</div>
      ${v.fitness_age ? `<div><b>${num(v.fitness_age, 1)}</b>Fitness age${v.chronological_age ? ` (actual ${num(v.chronological_age)})` : ""}</div>` : ""}
    </div>`;
  charts["c-spark"]?.destroy();
  const series = (v.garmin_series || []).slice(-120).map((p) => ({ x: ts(p.date), y: p.value }));
  charts["c-spark"] = new Chart($("#c-spark"), {
    type: "line",
    data: { datasets: [line("VO2max", series, css("--s1"), { fill: true, backgroundColor: alpha(css("--s1"), 0.1) })] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { type: "linear", display: false }, y: { display: false } } },
  });
}

function tile(label, value, unit, detail) {
  return `<article class="card tile"><div class="label">${label}</div><div class="value">${value}${unit ? `<small>${unit}</small>` : ""}</div><div class="detail">${detail || ""}</div></article>`;
}

function renderTiles(d) {
  const l = d.load, r = d.recovery, g = d.garmin_status;
  const ready = g.readiness_current;
  const rhrDelta = r.rhr_delta;
  $("#tiles").innerHTML = [
    tile("Fitness (CTL)", num(l.ctl, 0), "", `${l.ramp_7d >= 0 ? "+" : ""}${num(l.ramp_7d, 1)} this week`),
    tile("Form (TSB)", `${l.tsb > 0 ? "+" : ""}${num(l.tsb, 0)}`, "", esc(l.form_label || "")),
    tile("Acute : chronic", num(l.acwr, 2), "", l.acwr_zone ? statusChip(l.acwr_zone, { low: "Below norm", optimal: "Optimal", caution: "Ramping fast", high: "High risk" }[l.acwr_zone]) : ""),
    tile("Recovery score", num(r.score), "/100", r.label ? statusChip(r.label === "Primed" ? "good" : r.label === "Normal" ? "info" : "warn", r.label) : "Needs 2+ weeks of data"),
    ready
      ? tile("Training readiness", num(ready.value), "/100", esc((ready.level || "").toLowerCase()) + " · Garmin")
      : (() => { const bb = (r.body_battery || []).slice(-1)[0];
          return tile("Body Battery today", bb ? num(bb.value[1]) : "—", bb ? "max" : "", bb ? `low ${num(bb.value[0])}` : ""); })(),
    tile("HRV last night", num(r.hrv_last), "ms", r.hrv_status ? esc(r.hrv_status.toLowerCase()) : ""),
    tile("Resting HR · 7d", num(r.rhr_7d, 0), "bpm", rhrDelta != null ? `<span class="${rhrDelta > 2 ? "delta-down" : ""}">${rhrDelta > 0 ? "+" : ""}${num(rhrDelta, 1)}</span> vs 30-day baseline` : ""),
    tile("Sleep · 7d avg", num(r.sleep_7d_h, 1), "h", r.sleep_7d_score ? `score ${num(r.sleep_7d_score)}` : ""),
  ].join("");
}

function renderGoal(d) {
  const g = d.goal, el = $("#goal-card");
  if (!g) { el.hidden = true; return; }
  el.hidden = false;
  const gap = g.gap_s;
  el.innerHTML = `
    <div><div class="label">Goal race · ${esc(fmtDate(ts(g.date), true))}</div><div class="big">${esc(g.name)}</div>
      <div class="label">${g.days_left >= 0 ? `${g.days_left} days · ${g.weeks_left} weeks to go` : "Race day has passed"}</div></div>
    <div><div class="label">Target</div><div class="v">${dash(g.target_fmt)}</div><div class="label">${dash(g.target_pace_fmt)}</div></div>
    <div><div class="label">Predicted now</div><div class="v">${dash(g.predicted_fmt)}</div><div class="label">${dash(g.predicted_pace_fmt)}</div></div>
    <div><div class="label">Gap</div><div class="v ${gap != null ? (gap <= 0 ? "delta-up" : "delta-down") : ""}">${gap != null ? (gap <= 0 ? "−" : "+") + fmtTime(Math.abs(gap)) : "—"}</div>
      <div class="label">${gap != null ? (gap <= 0 ? "ahead of target" : "behind target") : ""}</div></div>
    <div><div class="label">VDOT needed / current</div><div class="v">${num(g.required_vdot, 1)} / ${num(g.current_vdot, 1)}</div></div>`;
}

function renderInsights(d) {
  const icon = { good: "✓", warn: "!", bad: "!", info: "i" };
  $("#insights").innerHTML = d.insights.length
    ? d.insights.map((i) => `<li class="lv-${i.level}"><span class="ico" aria-label="${i.level}">${icon[i.level]}</span><span>${esc(i.text)}</span></li>`).join("")
    : `<li class="lv-info"><span class="ico">i</span><span>Not enough data for insights yet.</span></li>`;
}

function renderPredictions(d) {
  const p = d.predictions;
  const b = p.basis;
  $("#pred-basis").innerHTML = b
    ? `VDOT &amp; Riegel based on your best recent effort: <b>${esc(b.label)} in ${esc(b.time_fmt)}</b> (${esc(fmtDate(ts(b.date), true))}, VDOT ${num(b.vdot, 1)}).
       Tanda uses your last 8 weeks: ${num(p.volume.weekly_km, 1)} km/week at ${esc(p.volume.mean_pace_fmt || "—")}.`
    : "Run a few harder efforts (≥ 3 km) to unlock VDOT and Riegel predictions.";
  const cols = [["actual_fmt", "Your best (4 mo)"], ["garmin_fmt", "Garmin"], ["vdot_fmt", "VDOT"], ["riegel_fmt", "Riegel"], ["tanda_fmt", "Tanda"], ["consensus_fmt", "Consensus"], ["pace_fmt", "Pace"]];
  $("#pred-table").innerHTML = `<thead><tr><th>Race</th>${cols.map(([, h]) => `<th class="num">${h}</th>`).join("")}</tr></thead><tbody>
    ${p.rows.map((r) => `<tr><td class="strong">${r.race}</td>${cols.map(([k]) => `<td class="num ${k === "consensus_fmt" ? "strong" : ""}">${dash(r[k])}</td>`).join("")}</tr>`).join("")}</tbody>`;
  $("#pred-notes").innerHTML = p.notes.map(esc).join("<br>");
}

function renderGarmin(d) {
  const g = d.garmin_status;
  const lt = g.lactate_threshold || {};
  const v = d.vo2max || {};
  const kv = [
    ["Training status", g.status],
    ["Acute load", g.acute_load != null ? num(g.acute_load) : null],
    ["Chronic load", g.chronic_load != null ? num(g.chronic_load) : null],
    ["ACWR (Garmin)", g.acwr != null ? num(g.acwr, 2) : null],
    ["Lactate threshold", lt.hr ? `${num(lt.hr)} bpm` : null],
    ["Threshold pace", lt.pace_fmt],
    ["Running power threshold", g.running_ftp ? `${num(g.running_ftp)} W` : null],
    ["Power / weight", g.power_to_weight ? `${num(g.power_to_weight, 2)} W/kg` : null],
    ["Endurance score", g.endurance_score ? num(g.endurance_score) : null],
    ["Endurance level", g.endurance_class],
    ["Hill score", g.hill_score ? num(g.hill_score) : null],
    ["Fitness age", v.fitness_age ? `${num(v.fitness_age, 1)} y` : null],
  ].filter(([, val]) => val != null && val !== "");
  const f = g.load_focus || {};
  const rows = [["aerobic_low", "Low aerobic"], ["aerobic_high", "High aerobic"], ["anaerobic", "Anaerobic"]];
  const maxV = Math.max(1, ...rows.flatMap(([k]) => [f[k] || 0, ...((f.targets || {})[k] || []).map((x) => x || 0)])) * 1.1;
  const focus = rows.some(([k]) => f[k] != null)
    ? `<h2 style="margin-top:18px">4-week load focus</h2><p class="note">Bar = your load · outline = Garmin's target range${f.feedback ? ` · ${esc(f.feedback.replaceAll("_", " ").toLowerCase())}` : ""}</p>
       <div class="focus">${rows.map(([k, label]) => {
         const t = (f.targets || {})[k] || [];
         return `<div class="row"><span>${label}</span><div class="track">
           ${t[0] != null ? `<div class="target" style="left:${(100 * t[0]) / maxV}%;width:${(100 * (t[1] - t[0])) / maxV}%"></div>` : ""}
           <div class="fill" style="width:${(100 * (f[k] || 0)) / maxV}%"></div></div><span class="num">${num(f[k])}</span></div>`;
       }).join("")}</div>`
    : "";
  const missing = !g.status && !focus
    ? `<p class="note" style="margin-top:14px">Your watch doesn't report Garmin Training Status, Load Focus or Lactate Threshold. The fitness, fatigue and load-ratio charts on this page cover the same ground from your heart-rate data.</p>`
    : "";
  $("#garmin-status").innerHTML = `<div class="kv">${kv.map(([k, val]) => `<div><b>${val}</b>${k}</div>`).join("")}</div>${focus}${missing}`;
}

function renderFitnessCharts(d) {
  const v = d.vo2max;
  draw("c-vo2", [
    line("Garmin", pts(v.garmin_series), css("--s1")),
    line("HR-based estimate", pts(v.hr_estimate_series), css("--s2")),
    line("VDOT (6-wk best)", pts(v.vdot_series), css("--s3"), { stepped: true, tension: 0 }),
  ], { yFmt: (x) => num(x, 0) });

  const pred = d.predictions.garmin_series[state.race] || [];
  draw("c-pred", [line(`Garmin ${state.race}`, pts(pred), css("--s1"))],
    { yFmt: fmtTime, reverse: true });

  const s = d.load.series || [];
  draw("c-pmc", [
    line("Fitness (CTL)", pts(s, (p) => p.ctl), css("--s1")),
    line("Fatigue (ATL)", pts(s, (p) => p.atl), css("--s2")),
  ], { yFmt: (x) => num(x), mode: "index" });

  const tsb = pts(s, (p) => p.tsb);
  draw("c-tsb", [bars("Form", tsb, tsb.map((p) => (p.y >= 0 ? css("--s1") : css("--s8"))), { borderRadius: 2, barPercentage: 1, categoryPercentage: 1 })],
    { yFmt: (x) => num(x), xBar: true, mode: "index" });

  const acwr = pts(s, (p) => p.acwr);
  draw("c-acwr", [
    ...band(acwr.map((p) => ({ x: p.x, y: 0.8 })), acwr.map((p) => ({ x: p.x, y: 1.3 })), css("--good")),
    line("ACWR", acwr, css("--s1")),
  ], { yFmt: (x) => num(x, 1), yMin: 0, yMax: Math.max(2, ...acwr.map((p) => p.y)) });
}

function renderVolume(d) {
  const vol = d.volume;
  const weeks = vol.weekly.filter((w) => ts(w.week) >= cutoff() - 7 * DAY);
  const x = (w) => ts(w.week) + 3.5 * DAY;
  const gap = { borderColor: css("--surface"), borderWidth: { top: 2 }, borderSkipped: "start", borderRadius: 0 };
  if (state.volUnit === "h") {
    const sports = SPORT_ORDER.filter((s) => vol.sports.includes(s));
    draw("c-weekly", sports.map((s, i) => bars(SPORT_LABEL[s], weeks.map((w) => ({ x: x(w), y: w[`${s}_h`] || 0 })), css(`--s${i + 1}`),
      { ...gap, fmt: (r) => `${num(r.y, 1)} h` })), { yFmt: (v) => `${v} h`, stacked: true, xBar: true, mode: "index" });
  } else {
    draw("c-weekly", [
      bars("Weekly run km", weeks.map((w) => ({ x: x(w), y: w.run_km || 0 })), css("--s1"), { fmt: (r) => `${num(r.y, 1)} km` }),
      line("Longest run", weeks.map((w) => ({ x: x(w), y: w.long_run_km || null })), css("--s2"), { pointRadius: 3, pointBackgroundColor: css("--s2"), fmt: (r) => `${num(r.y, 1)} km` }),
    ], { yFmt: (v) => `${v} km`, xBar: true, mode: "index" });
  }

  // heatmap — always the last 12 months
  const heat = vol.heatmap;
  const first = new Date(heat[0].date + "T12:00:00");
  const pad = (first.getDay() + 6) % 7;
  const level = (m) => (m <= 0 ? 0 : m < 30 ? 1 : m < 60 ? 2 : m < 90 ? 3 : m < 120 ? 4 : 5);
  $("#heatmap").innerHTML = '<div class="cell pad"></div>'.repeat(pad) + heat.map((h) =>
    `<div class="cell" data-l="${level(h.min)}" data-tip="${esc(fmtDate(ts(h.date), true))}: ${h.min ? `${h.min} min${h.km ? ` · ${h.km} km` : ""}` : "rest"}"></div>`).join("");
  $("#heatmap").style.gridTemplateColumns = `repeat(${Math.ceil((heat.length + pad) / 7)}, minmax(9px, 1fr))`;
  $("#heat-legend").innerHTML = "Rest " + [0, 1, 2, 3, 4, 5].map((l) => `<span class="c" style="background:var(--seq-${l})"></span>`).join("") + " 2 h+";

  const y = vol.ytd, ly = vol.last_year_ytd;
  const cmp = (a, b, unit, dgt = 0) => `${num(a || 0, dgt)} ${unit}${b ? ` <span class="${(a || 0) >= b ? "delta-up" : "delta-down"}">${(a || 0) >= b ? "▲" : "▼"}${num(Math.abs(100 * ((a || 0) - b) / b))}%</span>` : ""}`;
  $("#totals").innerHTML = [
    [`${cmp(y.run_km, ly.run_km, "km")}`, "Running this year"],
    [`${cmp(y.bike_km, ly.bike_km, "km")}`, "Cycling this year"],
    [`${cmp(y.hours, ly.hours, "h")}`, "Training time this year"],
    [`${num(y.elev || 0)} m`, "Elevation this year"],
    [`${num(vol.run_km_4w_avg, 1)} km`, "Run km/week · last 4 wks"],
    [`${num(vol.last_7d.run_km || 0, 1)} km`, "Run km · last 7 days"],
    [`${vol.streak_days} d`, "Current activity streak"],
  ].map(([v, k]) => `<div><b>${v}</b>${k}</div>`).join("") + (ly.count ? "" : "");
}

function renderIntensity(d) {
  const z = d.intensity;
  const colors = [1, 2, 3, 4, 5].map((i) => `var(--z${i})`);
  $("#zones-note").innerHTML = z.verdict
    ? `<b>${num(z.easy_pct)}%</b> easy (Z1–2) · <b>${num(z.moderate_pct)}%</b> moderate (Z3) · <b>${num(z.hard_pct)}%</b> hard (Z4–5). ${esc(z.verdict)}.`
    : "No heart-rate data in the last 28 days.";
  $("#zonebar").innerHTML = z.pct.map((p, i) => (p > 0 ? `<div style="width:${p}%;background:${colors[i]}" data-tip="Zone ${i + 1}: ${num(p, 1)}% · ${z.minutes[i]} min"></div>` : "")).join("");
  const floors = z.zone_floors || [];
  $("#zones-table").innerHTML = `<table class="data"><thead><tr><th>Zone</th><th class="num">From</th><th class="num">Minutes</th><th class="num">Share</th></tr></thead><tbody>
    ${z.pct.map((p, i) => `<tr><td><span class="sw" style="background:${colors[i]}"></span>Zone ${i + 1}</td><td class="num">${floors[i] ? `${Math.round(floors[i])} bpm` : "—"}</td><td class="num">${num(z.minutes[i])}</td><td class="num">${num(p, 1)}%</td></tr>`).join("")}
    </tbody></table><p class="note" style="margin-top:8px">Max HR ${num(z.max_hr)} · resting ${num(z.resting_hr)} · ${esc(z.source)}</p>`;
}

function renderEfficiency(d) {
  const e = d.efficiency;
  const pointsData = inRange(e.points).map((p) => ({ x: ts(p.date), y: p.ef, pace: p.pace, hr: p.hr }));
  draw("c-ef", [
    { type: "scatter", label: "Run", data: pointsData, backgroundColor: alpha(css("--s1"), 0.45), pointRadius: 4, pointHoverRadius: 6,
      pointBorderColor: css("--surface"), pointBorderWidth: 1, fmt: (r) => `${num(r.y, 2)} · ${fmtPace(r.pace)} @ ${num(r.hr)} bpm` },
    line("28-day average", pts(e.trend), css("--s2")),
  ], { yFmt: (x) => num(x, 2) });
  $("#ef-note").innerHTML = `Metres per minute per heartbeat on steady runs ≥ 20 min — higher is fitter.
    ${e.ef_change_pct != null ? `<b>${e.ef_change_pct > 0 ? "+" : ""}${num(e.ef_change_pct, 1)}%</b> vs 3 months ago.` : ""}
    ${e.zone2_pace_fmt ? `Zone 2 pace ≈ <b>${esc(e.zone2_pace_fmt)}</b>.` : ""} ${e.cadence_avg ? `Cadence ≈ ${e.cadence_avg} spm.` : ""}`;
}

function renderRecovery(d) {
  const r = d.recovery;
  const bandPts = inRange(r.hrv_band);
  draw("c-hrv", [
    ...band(bandPts.map((p) => ({ x: ts(p.date), y: p.value[0] })), bandPts.map((p) => ({ x: ts(p.date), y: p.value[1] })), css("--s1")),
    line("Last night", pts(r.hrv_series), css("--s1"), { borderWidth: 1.5, tension: 0.15 }),
    line("7-day avg", pts(r.hrv_weekly), css("--s2")),
  ], { yFmt: (x) => `${num(x)} ms` });

  const rhr = pts(r.rhr_series);
  draw("c-rhr", [line("Daily", rhr, alpha(css("--s1"), 0.45), { borderWidth: 1.5, tension: 0.1 }), line("7-day avg", rolling(rhr), css("--s1"))],
    { yFmt: (x) => `${num(x)} bpm`, mode: "index" });

  const sleep = inRange(r.sleep_series).map((p) => ({ x: ts(p.date), y: p.hours, score: p.score }));
  draw("c-sleep", [
    line("Night", sleep, alpha(css("--s1"), 0.45), { borderWidth: 1.5, tension: 0.1, fmt: (p) => `${num(p.y, 1)} h${p.score ? ` · score ${p.score}` : ""}` }),
    line("7-day avg", rolling(sleep), css("--s1"), { fmt: (p) => `${num(p.y, 1)} h` }),
  ], { yFmt: (x) => `${num(x, 1)} h`, mode: "index" });
  $("#sleep-note").innerHTML = r.sleep_7d_h != null
    ? `Last 7 nights: <b>${num(r.sleep_7d_h, 1)} h</b> avg${r.sleep_7d_score ? `, score ${r.sleep_7d_score}` : ""}. ${r.sleep_debt_7d_h > 0 ? `${num(r.sleep_debt_7d_h, 1)} h short of your goal this week.` : "On target."}`
    : "";

  const ready = inRange(d.garmin_status.readiness_series).map((p) => ({ x: ts(p.date), y: p.value, level: p.level }));
  draw("c-ready", [
    line("Daily", ready, alpha(css("--s1"), 0.45), { borderWidth: 1.5, tension: 0.1, fmt: (p) => `${p.y}${p.level ? ` · ${p.level.toLowerCase()}` : ""}` }),
    line("7-day avg", rolling(ready), css("--s1"), { fmt: (p) => num(p.y) }),
  ], { yMin: 0, yMax: 100, mode: "index" });

  const bb = inRange(r.body_battery);
  draw("c-bb", [
    line("Daily low", bb.map((p) => ({ x: ts(p.date), y: p.value[0] })), css("--s2"), { borderWidth: 1.5, tension: 0.1 }),
    line("Daily high", bb.map((p) => ({ x: ts(p.date), y: p.value[1] })), css("--s1"), { borderWidth: 1.5, tension: 0.1, fill: "-1", backgroundColor: alpha(css("--s1"), 0.1) }),
  ], { yMin: 0, yMax: 100, mode: "index" });

  const w = pts(r.weight);
  $("#weight-title").textContent = w.length ? `Weight · ${num(w[w.length - 1].y, 1)} kg` : "Weight";
  draw("c-weight", [line("Weight", w, css("--s1"), { pointRadius: 2 })], { yFmt: (x) => `${num(x, 1)} kg` });
}

function renderRecords(d) {
  const rec = d.records;
  $("#records").innerHTML = rec.garmin.length
    ? `<div class="table-wrap"><table class="data"><thead><tr><th>Record</th><th class="num">Value</th><th>Date</th></tr></thead><tbody>
       ${rec.garmin.map((r) => `<tr><td class="strong">${esc(r.label)}</td><td class="num">${esc(r.value_fmt)}</td><td>${r.date ? esc(fmtDate(ts(r.date), true)) : "—"}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="note">No Garmin personal records synced yet.</p>`;
  $("#bests").innerHTML = rec.bests.length
    ? `<thead><tr><th>Distance</th><th class="num">All time</th><th>When</th><th class="num">${new Date().getFullYear()}</th><th class="num">VDOT</th></tr></thead><tbody>
       ${rec.bests.map((b) => `<tr><td class="strong">${b.label}</td><td class="num">${b.all_time}</td><td>${esc(fmtDate(ts(b.all_time_date), true))}</td><td class="num">${dash(b.this_year)}</td><td class="num">${num(b.vdot, 1)}</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td>No best-effort data yet.</td></tr></tbody>`;
  $("#records-extra").innerHTML = [
    rec.longest_run_km ? `Longest run: <b>${num(rec.longest_run_km, 1)} km</b> (${esc(fmtDate(ts(rec.longest_run_date), true))})` : "",
    rec.biggest_run_week_km ? `Biggest week: <b>${num(rec.biggest_run_week_km, 1)} km</b> (week of ${esc(fmtDate(ts(rec.biggest_run_week), true))})` : "",
  ].filter(Boolean).join(" · ");
}

function renderActivities(d) {
  const acts = d.activities.filter((a) => !state.sport || a.sport === state.sport);
  const idx = SPORT_ORDER.filter((s) => d.volume.sports.includes(s));
  $("#activities").innerHTML = `<thead><tr><th>Date</th><th>Activity</th><th class="num">Distance</th><th class="num">Time</th><th class="num">Pace/Speed</th>
    <th class="num">Avg HR</th><th class="num">Elev</th><th class="num">TE (aer/ana)</th><th class="num">Load</th><th class="num">TRIMP</th></tr></thead><tbody>
    ${acts.map((a) => `<tr><td>${esc(fmtDate(ts(a.date)))} <span style="color:var(--muted)">${esc(a.start)}</span></td>
      <td class="name"><span class="sw" style="background:var(--s${Math.max(0, idx.indexOf(a.sport)) + 1})"></span>${esc(a.name)}</td>
      <td class="num">${a.km ? `${num(a.km, 2)} km` : "—"}</td><td class="num">${dash(a.duration_fmt)}</td>
      <td class="num">${a.pace_fmt || (a.speed_kmh ? `${a.speed_kmh} km/h` : "—")}</td><td class="num">${num(a.avg_hr)}</td>
      <td class="num">${a.elev ? `${num(a.elev)} m` : "—"}</td><td class="num">${a.te != null ? `${num(a.te, 1)} / ${num(a.ate, 1)}` : "—"}</td>
      <td class="num">${num(a.load)}</td><td class="num">${num(a.trimp)}</td></tr>`).join("") || `<tr><td colspan="10">No activities.</td></tr>`}</tbody>`;
}

// ---------------------------------------------------------------- table view for every chart
function addTableToggles() {
  document.querySelectorAll(".chart-card").forEach((card) => {
    if (card.querySelector(".table-toggle")) return;
    const btn = document.createElement("button");
    btn.className = "table-toggle";
    btn.textContent = "Table";
    const head = card.querySelector(".card-head");
    if (head) { btn.classList.add("inline"); head.querySelector("h2").after(btn); } else card.querySelector("h2").prepend(btn);
    btn.addEventListener("click", () => {
      const wrap = card.querySelector(".chart");
      const existing = card.querySelector(".chart-table");
      if (existing) { existing.remove(); wrap.hidden = false; btn.textContent = "Table"; return; }
      const chart = charts[wrap.querySelector("canvas").id];
      const sets = chart.data.datasets.filter((s) => !s.hideLegend);
      const rows = new Map();
      sets.forEach((s, i) => s.data.forEach((p) => {
        if (!rows.has(p.x)) rows.set(p.x, []);
        rows.get(p.x)[i] = s.fmt ? s.fmt(p) : Array.isArray(p.y) ? p.y.join("–") : typeof p.y === "number" ? num(p.y, Math.abs(p.y) < 10 ? 2 : 0) : p.y;
      }));
      const sorted = [...rows.entries()].sort((a, b) => b[0] - a[0]).slice(0, 60);
      const div = document.createElement("div");
      div.className = "table-wrap chart-table";
      div.style.maxHeight = wrap.offsetHeight + "px";
      div.innerHTML = `<table class="data"><thead><tr><th>Date</th>${sets.map((s) => `<th class="num">${esc(s.label)}</th>`).join("")}</tr></thead><tbody>
        ${sorted.map(([x, vals]) => `<tr><td>${fmtDate(x, true)}</td>${sets.map((_, i) => `<td class="num">${dash(vals[i])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      wrap.hidden = true;
      wrap.after(div);
      btn.textContent = "Chart";
    });
  });
}

// ---------------------------------------------------------------- orchestration
function guard(name, fn, failed = []) {
  try { fn(); } catch (e) { console.error(name, e); if (!failed.includes(name)) failed.push(name); }
}

function renderCharts(failed = []) {
  const d = state.data;
  document.querySelectorAll(".chart-table").forEach((t) => t.remove());
  document.querySelectorAll(".chart").forEach((c) => (c.hidden = false));
  document.querySelectorAll(".table-toggle").forEach((b) => (b.textContent = "Table"));
  [renderFitnessCharts, renderVolume, renderEfficiency, renderRecovery].forEach((fn) => guard(fn.name, () => fn(d), failed));
}

function renderAll() {
  const d = state.data;
  const m = d.meta;
  const synced = m.last_sync?.finished_at ? new Date(m.last_sync.finished_at).toLocaleString() : "never";
  $("#meta").textContent = `${m.name ? m.name + " · " : ""}${num(m.activity_count)} activities since ${m.first_activity ? fmtDate(ts(m.first_activity), true) : "—"} · last sync ${synced}`;
  $("#demo-badge").hidden = !m.demo;
  $("#sync").hidden = m.demo;
  document.querySelector('[data-chart="ready"]').hidden = !(d.garmin_status.readiness_series || []).length;
  document.querySelector('[data-chart="weight"]').hidden = (d.recovery.weight || []).length < 2;
  document.querySelector('[data-chart="bb"]').hidden = !(d.recovery.body_battery || []).length;
  const failed = Object.keys(m.section_errors || {});
  [renderHero, renderTiles, renderGoal, renderInsights, renderPredictions, renderGarmin, renderIntensity, renderRecords, renderActivities]
    .forEach((fn) => guard(fn.name, () => fn(d), failed));
  renderCharts(failed);
  addTableToggles();
  $("#foot").textContent = `Max HR ${m.max_hr} · resting HR ${num(m.resting_hr)} (used for TRIMP, HR-based VO2max and zones). Override in settings.json. Generated ${new Date(m.generated_at).toLocaleString()}.`
    + (failed.length ? ` Sections with unexpected Garmin data: ${failed.join(", ")} (details in /api/dashboard → meta.section_errors).` : "");
}

// ---------------------------------------------------------------- hosted mode: encrypted data.enc
const STATIC = !!window.GARMIN_STATIC;
const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const keySlot = (blob) => `garmin-key:${blob.salt}`;

async function deriveKey(password, blob) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt: b64(blob.salt), iterations: blob.iter },
    base, { name: "AES-GCM", length: 256 }, true, ["decrypt"]);
}

async function decryptBlob(blob, key) {
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(blob.iv) }, key, b64(blob.ct));
  const text = await new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
  return JSON.parse(text);
}

async function rememberedKey(blob) {
  try {
    const raw = localStorage.getItem(keySlot(blob));
    return raw ? await crypto.subtle.importKey("raw", b64(raw), "AES-GCM", true, ["decrypt"]) : null;
  } catch (_) { return null; }
}

function forgetKeys() {
  try { Object.keys(localStorage).filter((k) => k.startsWith("garmin-key:")).forEach((k) => localStorage.removeItem(k)); } catch (_) {}
}

function showDashboard(d) {
  $("#lock").hidden = true;
  document.querySelector(".viz-root").hidden = false;
  state.data = d;
  renderAll();
}

async function loadStatic() {
  const root = document.querySelector(".viz-root");
  root.hidden = true;
  const blob = await (await fetch("data.enc", { cache: "no-store" })).json();
  const saved = await rememberedKey(blob);
  if (saved) {
    try { return showDashboard(await decryptBlob(blob, saved)); } catch (_) { forgetKeys(); }
  }
  $("#lock").hidden = false;
  $("#lock-pw").focus();
  $("#lock-form").onsubmit = async (e) => {
    e.preventDefault();
    $("#lock-btn").disabled = true;
    $("#lock-msg").textContent = "Unlocking…";
    try {
      const key = await deriveKey($("#lock-pw").value, blob);
      const data = await decryptBlob(blob, key);
      if ($("#lock-remember").checked) {
        const raw = new Uint8Array(await crypto.subtle.exportKey("raw", key));
        try { localStorage.setItem(keySlot(blob), btoa(String.fromCharCode(...raw))); } catch (_) {}
      }
      $("#lock-pw").value = "";
      showDashboard(data);
    } catch (_) {
      $("#lock-msg").textContent = "Wrong password.";
      $("#lock-btn").disabled = false;
    }
  };
}

async function load() {
  if (STATIC) return loadStatic();
  const res = await fetch("/api/dashboard");
  const d = await res.json();
  if (d.empty || d.error || !d.meta?.activity_count) {
    $("#app").innerHTML = `<div class="card empty"><h2>No data yet</h2><p>${esc(d.error || d.message || "Run login.py, then sync.py (or press Sync Garmin).")}</p></div>`;
    $("#meta").textContent = "";
    return;
  }
  state.data = d;
  renderAll();
}

function segment(sel, key, attr, cb) {
  $(sel).addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $(sel).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    state[key] = attr === "days" ? Number(b.dataset[attr]) : b.dataset[attr];
    cb();
  });
}

segment("#range", "days", "days", () => state.data && renderCharts());
segment("#pred-race", "race", "race", () => state.data && renderCharts());
segment("#vol-unit", "volUnit", "unit", () => state.data && renderCharts());
segment("#act-filter", "sport", "sport", () => state.data && renderActivities(state.data));

$("#theme").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("garmin-theme", root.dataset.theme); } catch (_) {}
  if (state.data) renderAll();
});
try { const t = localStorage.getItem("garmin-theme"); if (t) document.documentElement.dataset.theme = t; } catch (_) {}

if (STATIC) {
  $("#sync").textContent = "Lock";
  $("#sync").title = "Forget the saved key on this device and lock the dashboard";
}

$("#sync").addEventListener("click", async () => {
  if (STATIC) { forgetKeys(); location.reload(); return; }
  const btn = $("#sync"), log = $("#sync-log");
  btn.disabled = true; btn.textContent = "Syncing…"; log.hidden = false; log.textContent = "Starting sync…";
  await fetch("/api/sync", { method: "POST" });
  const poll = setInterval(async () => {
    const s = await (await fetch("/api/sync")).json();
    log.textContent = s.log.join("\n") || "Working…";
    if (!s.running) {
      clearInterval(poll);
      btn.disabled = false; btn.textContent = "Sync Garmin";
      const errs = s.result?.errors || [];
      log.textContent += `\n\n${s.result?.message ? "Sync failed: " + s.result.message : errs.length ? `Done — ${errs.length} step(s) failed: ${errs.join(", ")}` : "Done."}`;
      setTimeout(() => (log.hidden = true), errs.length || s.result?.message ? 20000 : 4000);
      load();
    }
  }, 1500);
});

// shared hover tooltip for heatmap cells & zone bar
const tip = $("#tip");
document.addEventListener("mousemove", (e) => {
  const t = e.target.closest("[data-tip]");
  if (!t) { tip.hidden = true; return; }
  tip.textContent = t.dataset.tip;
  tip.hidden = false;
  tip.style.left = Math.min(e.clientX + 12, innerWidth - tip.offsetWidth - 8) + "px";
  tip.style.top = e.clientY + 14 + "px";
});

load();
