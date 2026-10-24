/* kchain analyst console — vanilla JS, no build step. */

const NS = 'http://www.w3.org/2000/svg';
const PHASE_COLORS = {
  find: '#6ea8fe',
  fix: '#5ec8b8',
  track: '#a99bf5',
  target: '#e0b05c',
  engage: '#e07a7a',
  assess: '#7c8698',
};
const PHASE_ORDER = ['find', 'fix', 'track', 'target', 'engage', 'assess'];
const OUTCOME_COLORS = { approve: '#5ec8b8', reject: '#e0b05c', escalate: '#a99bf5', deny: '#e07a7a' };

const state = {
  scenarios: [],
  currentId: null,
  report: null,
  runs: 4000,
  seed: 7,
  sensitivity: true,
  policy: {},
  token: 0,
};

const $ = (sel) => document.querySelector(sel);

/* ------------------------------------------------------------------ utils */

function fmtTime(sec) {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return '—';
  if (sec < 90) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${(sec / 60).toFixed(1)}m`;
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return m ? `${h}h ${m}m` : `${h}h`;
}

function pct(x) {
  return x === null || x === undefined ? '—' : `${(x * 100).toFixed(1)}%`;
}

function truncate(text, n) {
  if (!text) return '';
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

function showError(message) {
  const node = $('#error');
  node.textContent = `ERROR\n${message}`;
  node.classList.remove('hidden');
}

function clearError() {
  $('#error').classList.add('hidden');
}

function svgEl(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    node.setAttribute(key, String(value));
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

function h(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function chartWidth(svg, fallback) {
  const parent = svg.parentElement;
  const measured = parent ? parent.clientWidth - 4 : 0;
  return Math.min(1000, Math.max(320, Math.round(measured || fallback)));
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

async function api(path, body) {
  const options = body
    ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const response = await fetch(path, options);
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`${path} returned non-JSON (${response.status}): ${text.slice(0, 200)}`);
  }
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

/* ------------------------------------------------------------------- boot */

async function boot() {
  try {
    const health = await api('/api/health');
    $('#version').textContent = `v${health.version}`;
    state.scenarios = await api('/api/scenarios');
    renderTabs();
    const requested = decodeURIComponent(window.location.hash.replace(/^#/, ''));
    const initial = state.scenarios.find((scenario) => scenario.id === requested) || state.scenarios[0];
    await selectScenario(initial.id);
    bindControls();
  } catch (error) {
    showError(error.message);
  }
}

function bindControls() {
  $('#run').addEventListener('click', () => {
    state.runs = Number($('#runs').value);
    state.seed = Number($('#seed').value) || 0;
    state.sensitivity = $('#sensitivity').checked;
    loadReport();
  });
  $('#runs').addEventListener('change', () => {
    state.runs = Number($('#runs').value);
    loadReport();
  });
  $('#sensitivity').addEventListener('change', () => {
    state.sensitivity = $('#sensitivity').checked;
    loadReport();
  });

  const pid = $('#pid-slider');
  const esc = $('#esc-slider');
  const applyPolicy = debounce(() => {
    state.policy = {
      pid_threshold: Number(pid.value),
      escalation_threshold: Number(esc.value),
    };
    loadReport();
  }, 420);

  pid.addEventListener('input', () => {
    let value = Number(pid.value);
    if (Number(esc.value) < value) {
      esc.value = value.toFixed(2);
      paintSlider(esc);
    }
    paintSlider(pid);
    $('#pid-value').textContent = value.toFixed(2);
    applyPolicy();
  });
  esc.addEventListener('input', () => {
    let value = Number(esc.value);
    if (value < Number(pid.value)) {
      value = Number(pid.value);
      esc.value = value.toFixed(2);
    }
    paintSlider(esc);
    $('#esc-value').textContent = value.toFixed(2);
    applyPolicy();
  });
}

function paintSlider(input) {
  const min = Number(input.min);
  const max = Number(input.max);
  const value = Number(input.value);
  const fill = ((value - min) / (max - min)) * 100;
  input.style.setProperty('--fill', `${fill}%`);
}

function renderTabs() {
  const nav = $('#tabs');
  nav.innerHTML = '';
  for (const scenario of state.scenarios) {
    const button = h('button', 'tab', scenario.title);
    button.dataset.id = scenario.id;
    if (scenario.id === state.currentId) button.classList.add('active');
    button.addEventListener('click', () => selectScenario(scenario.id));
    nav.appendChild(button);
  }
}

async function selectScenario(id) {
  state.currentId = id;
  state.report = null;
  history.replaceState(null, '', `#${id}`);
  const scenario = state.scenarios.find((item) => item.id === id);
  state.policy = { ...scenario.policy };
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.id === id);
  });
  $('#node-detail').classList.add('hidden');
  paintPolicyControls(scenario.policy);
  await loadReport();
}

function paintPolicyControls(policy) {
  const pid = $('#pid-slider');
  const esc = $('#esc-slider');
  pid.value = Number(policy.pid_threshold).toFixed(2);
  esc.value = Number(policy.escalation_threshold).toFixed(2);
  $('#pid-value').textContent = Number(policy.pid_threshold).toFixed(2);
  $('#esc-value').textContent = Number(policy.escalation_threshold).toFixed(2);
  paintSlider(pid);
  paintSlider(esc);
}

async function loadReport() {
  const token = ++state.token;
  const button = $('#run');
  button.disabled = true;
  button.textContent = 'running…';
  clearError();
  try {
    const report = await api('/api/run', {
      scenario: state.currentId,
      runs: state.runs,
      seed: state.seed,
      sensitivity: state.sensitivity,
      policy: state.policy,
    });
    if (token !== state.token) return;
    state.report = report;
    render(report);
  } catch (error) {
    if (token === state.token) showError(error.message);
  } finally {
    if (token === state.token) {
      button.disabled = false;
      button.textContent = 'Run';
    }
  }
}

/* ----------------------------------------------------------------- render */

function render(report) {
  const sections = [
    ['KPIs', () => renderKpis(report)],
    ['topology', () => renderGraph(report)],
    ['findings', () => renderInsights(report)],
    ['policy', () => renderPolicy(report)],
    ['histogram', () => renderHistogram(report)],
    ['phases', () => renderPhases(report)],
    ['timeline', () => renderTimeline(report)],
    ['sensitivity', () => renderTornado(report)],
    ['tradeoff', () => renderTradeoff(report)],
    ['audit', () => renderAudit(report)],
  ];
  const failures = [];
  for (const [name, fn] of sections) {
    try {
      fn();
    } catch (error) {
      failures.push(`${name}: ${error.message}`);
    }
  }
  if (failures.length) showError(failures.join('\n'));
}

function renderKpis(report) {
  const m = report.metrics;
  const scenario = report.scenario;
  $('#scenario-title').textContent = scenario.title;
  $('#scenario-summary').textContent = scenario.summary;
  $('#scenario-meta').textContent = `${scenario.target.label} · ${scenario.target.kind} · prior hostile ${pct(
    scenario.evidence ? scenario.evidence.prior_hostile : null,
  )} · fp ${scenario.fingerprint}`;

  const total = m.total_s || 1;
  const linkShare = m.link_s / total;
  const blocked = (m.outcomes.denied || 0) + (m.outcomes.abandoned || 0);
  const tiles = [
    { label: 'Median', value: fmtTime(m.p50_s), sub: 'engaged runs', cls: 'accent' },
    { label: 'p95', value: fmtTime(m.p95_s), sub: 'same runs', cls: 'warn' },
    {
      label: 'Chain success',
      value: pct(m.success_rate),
      sub: `${m.engaged.toLocaleString()} of ${m.runs.toLocaleString()}`,
      cls: m.success_rate > 0.9 ? 'good' : m.success_rate > 0.5 ? '' : 'bad',
    },
    { label: 'Re-cycles', value: m.recycles_mean.toFixed(2), sub: 'per attempt', cls: '' },
    { label: 'Handoff', value: pct(linkShare), sub: 'on the network', cls: '' },
    {
      label: 'Blocked',
      value: pct(blocked / m.runs),
      sub: 'denied or abandoned',
      cls: blocked / m.runs > 0.25 ? 'bad' : '',
    },
  ];

  const host = $('#kpis');
  host.innerHTML = '';
  for (const tile of tiles) {
    const box = h('div', `stat ${tile.cls}`.trim());
    box.appendChild(h('div', 'stat-label', tile.label));
    box.appendChild(h('div', 'stat-value', tile.value));
    box.appendChild(h('div', 'stat-sub', tile.sub));
    host.appendChild(box);
  }
}

/* ---------------------------------------------------------------- topology */

const GEO = { NW: 156, NH: 52, GX: 184, GY: 72, PADX: 22, PADY: 18 };

function renderGraph(report) {
  const { layout } = report;
  const maxRows = Math.max(1, layout.rows || 1);
  const terminalX = GEO.PADX + layout.layers * GEO.GX;

  const pos = {};
  for (const node of layout.nodes) {
    const y = GEO.PADY + node.row * GEO.GY + ((maxRows - node.rows_in_layer) * GEO.GY) / 2;
    pos[node.id] = {
      x: GEO.PADX + node.layer * GEO.GX,
      y,
      layer: node.layer,
      cx: GEO.PADX + node.layer * GEO.GX + GEO.NW / 2,
      cy: y + GEO.NH / 2,
    };
  }

  let height = GEO.PADY * 2 + maxRows * GEO.GY + 46;
  for (const edge of layout.edges) {
    if (!edge.recycle) continue;
    const a = pos[edge.from];
    const b = pos[edge.to];
    if (!a || !b) continue;
    const dip = Math.max(a.y, b.y) + GEO.NH + 30 + 10 * Math.abs(a.layer - b.layer);
    height = Math.max(height, dip + 36);
  }
  const width = terminalX + 96;

  const svg = $('#graph');
  svg.innerHTML = '';
  // Size is owned by CSS (width:100%, height:auto, min-width floor) so the
  // graph fits the panel without a measurement pass that can race layout.
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('preserveAspectRatio', 'xMinYMin meet');

  const defs = svgEl('defs');
  const markers = [
    ['arrow', '#2c333e'],
    ['arrow', '#3a4351'],
    ['arrow-recycle', '#8a5555'],
    ['arrow-escalate', '#7a6742'],
  ];
  for (const [id, color] of markers) {
    const marker = svgEl('marker', {
      id,
      viewBox: '0 0 10 10',
      refX: '9',
      refY: '5',
      markerWidth: '6',
      markerHeight: '6',
      orient: 'auto-start-reverse',
    });
    marker.appendChild(svgEl('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: color }));
    defs.appendChild(marker);
  }
  svg.appendChild(defs);

  const edgeLayer = svgEl('g');
  const nodeLayer = svgEl('g');
  svg.appendChild(edgeLayer);
  svg.appendChild(nodeLayer);

  for (const edge of layout.edges) {
    const a = pos[edge.from];
    if (!a) continue;
    const colour = edge.recycle ? '#8a5555' : edge.label === 'escalate' ? '#7a6742' : '#313947';
    const markerId = edge.recycle ? 'arrow-recycle' : edge.label === 'escalate' ? 'arrow-escalate' : 'arrow';
    const dash = edge.recycle ? '5 4' : edge.label === 'escalate' ? '3 3' : null;

    let path;
    let mid;
    if (edge.terminal) {
      const sx = a.x + GEO.NW;
      const sy = a.y + GEO.NH / 2;
      const ex = terminalX + 12;
      path = `M ${sx} ${sy} C ${sx + 24} ${sy}, ${ex - 24} ${sy}, ${ex} ${sy}`;
      mid = { x: (sx + ex) / 2, y: sy };

      const pill = svgEl('g');
      pill.appendChild(
        svgEl('rect', {
          x: terminalX,
          y: sy - 9,
          width: 54,
          height: 18,
          rx: 4,
          fill: edge.to.includes('DENIED') ? '#1d1319' : '#131b24',
          stroke: edge.to.includes('DENIED') ? '#5a2230' : '#2a3547',
        }),
      );
      pill.appendChild(
        svgEl(
          'text',
          {
            x: terminalX + 27,
            y: sy + 3.5,
            'text-anchor': 'middle',
            fill: edge.to.includes('DENIED') ? '#ff9aa8' : '#8c9aae',
            'font-size': 8.5,
            'font-family': 'ui-monospace, monospace',
          },
          edge.to.replace('END_', 'END '),
        ),
      );
      edgeLayer.appendChild(pill);
    } else {
      const b = pos[edge.to];
      if (!b) continue;
      if (edge.recycle) {
        const sx = a.x + GEO.NW * 0.35;
        const sy = a.y + GEO.NH;
        const ex = b.x + GEO.NW * 0.35;
        const ey = b.y + GEO.NH;
        const dip = Math.max(a.y, b.y) + GEO.NH + 30 + 10 * Math.abs(a.layer - b.layer);
        path = `M ${sx} ${sy} C ${sx} ${dip}, ${ex} ${dip}, ${ex} ${ey}`;
        mid = { x: (sx + 3 * sx + 3 * ex + ex) / 8, y: (sy + 3 * dip + 3 * dip + ey) / 8 };
      } else {
        const sx = a.x + GEO.NW;
        const sy = a.y + GEO.NH / 2;
        const ex = b.x;
        const ey = b.y + GEO.NH / 2;
        const mx = (sx + ex) / 2;
        path = `M ${sx} ${sy} C ${mx} ${sy}, ${mx} ${ey}, ${ex} ${ey}`;
        mid = { x: (sx + 3 * mx + 3 * mx + ex) / 8, y: (sy + 3 * sy + 3 * ey + ey) / 8 };
      }
    }

    const pathNode = svgEl('path', {
      d: path,
      fill: 'none',
      stroke: colour,
      'stroke-width': 1.2,
      'marker-end': `url(#${markerId})`,
    });
    if (dash) pathNode.setAttribute('stroke-dasharray', dash);
    pathNode.appendChild(
      svgEl(
        'title',
        {},
        `${edge.from} to ${edge.to} · handoff ${fmtTime(edge.mean_link_s || 0)}${
          edge.p !== null && edge.p !== undefined ? ` · p=${edge.p}` : ''
        }`,
      ),
    );
    edgeLayer.appendChild(pathNode);

    const parts = [];
    if (edge.label && edge.label !== 'flow') parts.push(edge.label.toUpperCase());
    if (edge.p !== null && edge.p !== undefined && edge.p < 1) parts.push(`p=${edge.p}`);
    if (parts.length) {
      edgeLayer.appendChild(
        svgEl(
          'text',
          {
            x: mid.x,
            y: mid.y - (edge.recycle ? 0 : 4),
            'text-anchor': 'middle',
            fill: edge.recycle ? '#8f5f5f' : '#4f5866',
            'font-size': 9,
            'font-family': 'ui-monospace, monospace',
            stroke: '#0a0b0e',
            'stroke-width': 3,
            'paint-order': 'stroke',
          },
          parts.join(' · '),
        ),
      );
    }
  }

  for (const node of layout.nodes) {
    const { x, y } = pos[node.id];
    const phaseColor = PHASE_COLORS[node.phase] || '#7c8698';
    const group = svgEl('g', { cursor: 'pointer' });
    group.appendChild(
      svgEl(
        'title',
        {},
        `${node.label}\n${node.actor || node.kind} · ${node.phase} · ${node.kind}\nmean ${fmtTime(
          node.mean_s,
        )} · ${pct(node.share || 0)} of cycle`,
      ),
    );
    group.appendChild(
      svgEl('rect', {
        x,
        y,
        width: GEO.NW,
        height: GEO.NH,
        rx: 6,
        fill: '#0d1014',
        stroke: node.kind === 'decision' ? '#3a3222' : '#1f242c',
        'stroke-width': 1,
      }),
    );
    group.appendChild(
      svgEl('rect', {
        x: x + 1,
        y: y + 11,
        width: 2.5,
        height: GEO.NH - 22,
        rx: 1.25,
        fill: phaseColor,
        opacity: 0.9,
      }),
    );
    group.appendChild(
      svgEl(
        'text',
        { x: x + 12, y: y + 21, fill: '#dfe3ea', 'font-size': 12, 'font-weight': 500 },
        truncate(node.label, node.kind === 'decision' ? 18 : 21),
      ),
    );
    group.appendChild(
      svgEl(
        'text',
        { x: x + 12, y: y + 35, fill: '#5f6877', 'font-size': 9 },
        truncate(node.actor || node.kind, node.kind === 'decision' ? 19 : 27),
      ),
    );
    group.appendChild(
      svgEl(
        'text',
        {
          x: x + GEO.NW - 10,
          y: y + 21,
          'text-anchor': 'end',
          fill: '#6f7a8c',
          'font-size': 10,
          'font-family': 'ui-monospace, monospace',
        },
        fmtTime(node.mean_s),
      ),
    );
    if (node.kind === 'decision') {
      group.appendChild(
        svgEl(
          'text',
          {
            x: x + GEO.NW - 10,
            y: y + 35,
            'text-anchor': 'end',
            fill: '#8a7440',
            'font-size': 8,
            'letter-spacing': '0.08em',
          },
          'GATE',
        ),
      );
    }
    group.appendChild(
      svgEl('rect', {
        x: x + 13,
        y: y + GEO.NH - 8,
        width: GEO.NW - 26,
        height: 2.5,
        rx: 1.25,
        fill: '#0c1119',
      }),
    );
    group.appendChild(
      svgEl('rect', {
        x: x + 13,
        y: y + GEO.NH - 8,
        width: Math.max(1, (GEO.NW - 26) * Math.min(1, node.share || 0)),
        height: 2.5,
        rx: 1.25,
        fill: phaseColor,
        opacity: 0.85,
      }),
    );
    group.addEventListener('click', () => showNodeDetail(report, node.id));
    nodeLayer.appendChild(group);
  }

  const legend = $('#phase-legend');
  legend.innerHTML = '';
  for (const phase of PHASE_ORDER) {
    const item = h('span');
    const dot = h('i');
    dot.style.background = PHASE_COLORS[phase];
    item.appendChild(dot);
    item.appendChild(document.createTextNode(phase));
    legend.appendChild(item);
  }
}

function showNodeDetail(report, id) {
  const node = report.nodes.find((item) => item.id === id);
  const contribution = report.metrics.contributions.find((item) => item.id === id);
  if (!node) return;
  const detail = $('#node-detail');
  detail.innerHTML = '';
  detail.classList.remove('hidden');

  const title = h('div');
  title.appendChild(h('b', null, node.label));
  title.appendChild(
    h(
      'span',
      'muted',
      `  ${node.actor || 'unattributed'} · ${node.kind} · ${node.phase} phase · ${node.echelon} echelon`,
    ),
  );
  detail.appendChild(title);

  const stats = h(
    'div',
    'mono',
    `duration ${node.duration}   mean ${fmtTime(node.mean_s)}   visits ${(contribution?.visits || 0).toFixed(
      2,
    )}   share ${pct(contribution?.share || 0)}`,
  );
  stats.style.fontSize = '11px';
  stats.style.margin = '4px 0';
  stats.style.color = '#b9c6d9';
  detail.appendChild(stats);

  if (node.note) detail.appendChild(h('div', 'muted', node.note));
}

/* --------------------------------------------------------------- insights */

function renderInsights(report) {
  const list = $('#insights');
  list.innerHTML = '';
  for (const line of report.insights) list.appendChild(h('li', null, line));
}

function renderPolicy(report) {
  const list = $('#policy-rules');
  list.innerHTML = '';
  for (const line of report.scenario.policy_rules) list.appendChild(h('li', null, line));
  if (document.activeElement !== $('#pid-slider') && document.activeElement !== $('#esc-slider')) {
    paintPolicyControls(report.scenario.policy);
  }
}

/* -------------------------------------------------------------- histogram */

function renderHistogram(report) {
  const bins = report.histogram;
  const svg = $('#histogram');
  svg.innerHTML = '';
  const W = chartWidth(svg, 640);
  const H = 190;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', H);
  svg.setAttribute('preserveAspectRatio', 'none');

  $('#hist-meta').textContent = `${report.metrics.engaged.toLocaleString()} engaged runs · p95 ${fmtTime(
    report.metrics.p95_s,
  )}`;

  if (!bins.length) {
    svg.appendChild(
      svgEl('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', fill: '#8c9aae', 'font-size': 12 }, 'No engaged runs.'),
    );
    return;
  }

  const padL = 42;
  const padR = 14;
  const padT = 16;
  const padB = 30;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const maxCount = Math.max(...bins.map((b) => b.count), 1);
  const maxX = bins[bins.length - 1].x1 || 1;

  for (let i = 0; i <= 3; i += 1) {
    const y = padT + (plotH * i) / 3;
    svg.appendChild(svgEl('line', { x1: padL, x2: W - padR, y1: y, y2: y, stroke: '#15181f' }));
    svg.appendChild(
      svgEl(
        'text',
        { x: padL - 7, y: y + 3, 'text-anchor': 'end', class: 'tick' },
        Math.round(maxCount * (1 - i / 3)).toLocaleString(),
      ),
    );
  }

  bins.forEach((bin) => {
    const width = Math.max(1, (bin.x1 - bin.x0) / maxX * plotW - 1);
    const height = (bin.count / maxCount) * plotH;
    const x = padL + (bin.x0 / maxX) * plotW;
    const y = padT + plotH - height;
    svg.appendChild(
      svgEl('rect', {
        x,
        y,
        width,
        height,
        fill: '#3d6fa8',
        opacity: 0.55,
        rx: 1,
      }),
    );
  });

  const markers = [
    ['p50', report.metrics.p50_s, '#6ea8fe'],
    ['p95', report.metrics.p95_s, '#e0b05c'],
    ['p99', report.metrics.p99_s, '#e07a7a'],
  ];
  for (const [label, value, colour] of markers) {
    if (value === null || value === undefined) continue;
    const x = padL + Math.min(1, value / maxX) * plotW;
    svg.appendChild(
      svgEl('line', { x1: x, x2: x, y1: padT - 4, y2: padT + plotH, stroke: colour, 'stroke-width': 1.2, 'stroke-dasharray': '3 3' }),
    );
    svg.appendChild(
      svgEl('text', { x: x + 4, y: padT + 8, fill: colour, 'font-size': 9.5, 'font-family': 'ui-monospace, monospace' }, label),
    );
  }

  for (let i = 0; i <= 4; i += 1) {
    const value = (maxX * i) / 4;
    svg.appendChild(
      svgEl(
        'text',
        { x: padL + (plotW * i) / 4, y: H - 10, 'text-anchor': 'middle', class: 'tick' },
        fmtTime(value),
      ),
    );
  }
}

/* ----------------------------------------------------------------- phases */

function renderPhases(report) {
  const host = $('#phase-bars');
  host.innerHTML = '';
  const maxSeconds = Math.max(...report.phases.map((phase) => phase.seconds), 1);
  for (const phase of report.phases) {
    const row = h('div', 'phase-row');
    row.appendChild(h('div', 'phase-name', phase.phase));
    const track = h('div', 'phase-track');
    const fill = h('div', 'phase-fill');
    fill.style.width = `${Math.max(1.5, (phase.seconds / maxSeconds) * 100)}%`;
    fill.style.background = PHASE_COLORS[phase.phase] || '#8b98a8';
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(h('div', 'phase-value', `${Math.round(phase.seconds)}s`));
    host.appendChild(row);
  }

  const list = $('#contrib-list');
  list.innerHTML = '';
  for (const item of report.metrics.contributions.slice(0, 6)) {
    const li = h('li');
    li.appendChild(h('span', null, item.label));
    li.appendChild(h('span', 't', `${Math.round(item.mean_s)}s`));
    list.appendChild(li);
  }
}

/* --------------------------------------------------------------- timeline */

function renderTimeline(report) {
  const run = report.sample_run;
  const svg = $('#timeline');
  svg.innerHTML = '';
  const W = chartWidth(svg, 900);
  const H = 156;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', H);
  svg.setAttribute('preserveAspectRatio', 'none');

  const decisions = $('#decisions');
  decisions.innerHTML = '';

  if (!run) {
    $('#timeline-meta').textContent = '';
    return;
  }

  $('#timeline-meta').textContent = `${run.outcome} · ${fmtTime(run.total_s)} · ${run.recycles} re-cycle(s) · ${fmtTime(
    run.link_s,
  )} on the network`;

  const padL = 58;
  const padR = 12;
  const plotW = W - padL - padR;
  const total = Math.max(run.total_s, 1);
  const x = (t) => padL + (t / total) * plotW;

  const laneActivity = { y: 26, h: 30 };
  const laneLink = { y: 74, h: 14 };

  svg.appendChild(
    svgEl('text', { x: 4, y: laneActivity.y + 19, class: 'axis-label' }, 'ACTIVITY'),
  );
  svg.appendChild(svgEl('text', { x: 4, y: laneLink.y + 11, class: 'axis-label' }, 'NETWORK'));

  svg.appendChild(
    svgEl('rect', { x: padL, y: laneActivity.y, width: plotW, height: laneActivity.h, fill: '#0c1119', rx: 3 }),
  );
  svg.appendChild(svgEl('rect', { x: padL, y: laneLink.y, width: plotW, height: laneLink.h, fill: '#0c1119', rx: 3 }));

  for (const segment of run.segments) {
    const start = x(segment.start);
    const width = Math.max(1, x(segment.end) - start);
    if (segment.kind === 'activity') {
      const colour = PHASE_COLORS[segment.detail] || '#7c8698';
      svg.appendChild(
        svgEl('rect', { x: start, y: laneActivity.y, width, height: laneActivity.h, fill: colour, opacity: 0.5, rx: 1.5 }),
      );
      if (width > 54) {
        svg.appendChild(
          svgEl(
            'text',
            {
              x: start + 4,
              y: laneActivity.y + 19,
              fill: '#08101c',
              'font-size': 9,
              'font-weight': 650,
            },
            truncate(segment.label, Math.floor(width / 6)),
          ),
        );
      }
    } else if (segment.kind === 'link') {
      svg.appendChild(
        svgEl('rect', { x: start, y: laneLink.y, width, height: laneLink.h, fill: '#20262f', rx: 1 }),
      );
    } else if (segment.kind === 'gate') {
      const outcome = (segment.label || '').split(' ')[0].toLowerCase();
      const colour = OUTCOME_COLORS[outcome] || '#dce6f5';
      const cx = start;
      const cy = laneActivity.y + laneActivity.h / 2;
      svg.appendChild(
        svgEl('path', {
          d: `M ${cx} ${cy - 7} L ${cx + 7} ${cy} L ${cx} ${cy + 7} L ${cx - 7} ${cy} Z`,
          fill: colour,
          stroke: '#0c1119',
          'stroke-width': 1,
        }),
      );
      svg.appendChild(
        svgEl(
          'text',
          {
            x: cx + 10,
            y: laneActivity.y - 6,
            fill: colour,
            'font-size': 8.5,
            'font-weight': 600,
            'letter-spacing': '0.05em',
          },
          outcome.toUpperCase(),
        ),
      );
    }
  }

  for (let i = 0; i <= 4; i += 1) {
    const value = (total * i) / 4;
    svg.appendChild(
      svgEl('text', { x: padL + (plotW * i) / 4, y: H - 12, 'text-anchor': 'middle', class: 'tick' }, fmtTime(value)),
    );
  }

  for (const decision of run.decisions) {
    const row = h('div', `decision ${decision.outcome}`);
    row.appendChild(h('div', 'when', fmtTime(decision.t)));
    const what = h('div', 'what', decision.outcome);
    what.style.color = OUTCOME_COLORS[decision.outcome] || '#dce6f5';
    row.appendChild(what);
    const why = h('div', 'why');
    why.appendChild(h('b', null, `p=${(decision.confidence * 100).toFixed(0)}%`));
    why.appendChild(document.createTextNode(` · ${decision.reason}`));
    row.appendChild(why);
    decisions.appendChild(row);
  }
}

/* ---------------------------------------------------------------- tornado */

function renderTornado(report) {
  const rows = (report.tornado || []).slice(0, 8);
  const svg = $('#tornado');
  svg.innerHTML = '';
  const W = chartWidth(svg, 620);
  const H = Math.max(80, rows.length * 26 + 26);
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', H);
  svg.setAttribute('preserveAspectRatio', 'none');

  if (!rows.length) {
    svg.appendChild(
      svgEl('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', fill: '#8c9aae', 'font-size': 12 }, 'No engaged runs to analyse.'),
    );
    return;
  }

  const labelW = 168;
  const zero = labelW + 36;
  const maxDelta = Math.max(...rows.map((row) => Math.abs(row.delta_p95_s)), 1);
  const scale = (W - zero - 52) / maxDelta;

  svg.appendChild(svgEl('line', { x1: zero, x2: zero, y1: 4, y2: H - 18, stroke: '#1e232c' }));

  rows.forEach((row, index) => {
    const y = 14 + index * 26;
    svg.appendChild(
      svgEl(
        'text',
        { x: 0, y: y + 11, fill: '#c2cddc', 'font-size': 10.5 },
        truncate(row.label, 26),
      ),
    );
    const width = Math.abs(row.delta_p95_s) * scale;
    const positive = row.delta_p95_s >= 0;
    svg.appendChild(
      svgEl('rect', {
        x: positive ? zero : zero - width,
        y: y + 2,
        width: Math.max(1, width),
        height: 13,
        rx: 2,
        fill: positive ? '#5ec8b8' : '#e07a7a',
        opacity: 0.5,
      }),
    );
    svg.appendChild(
      svgEl(
        'text',
        {
          x: positive ? zero + width + 6 : zero - width - 6,
          y: y + 12,
          'text-anchor': positive ? 'start' : 'end',
          fill: '#8c9aae',
          'font-size': 9.5,
          'font-family': 'ui-monospace, monospace',
        },
        `${row.delta_p95_s >= 0 ? '−' : '+'}${Math.abs(row.delta_p95_s).toFixed(0)}s`,
      ),
    );
  });
}

/* --------------------------------------------------------------- tradeoff */

function renderTradeoff(report) {
  const points = report.tradeoff || [];
  const threshold = report.scenario.policy.pid_threshold;
  drawTailChart($('#trade-a'), points, threshold);
  drawEvidenceChart($('#trade-b'), points, threshold);

  const current = points.reduce((best, point) => {
    if (!best) return point;
    return Math.abs(point.threshold - threshold) < Math.abs(best.threshold - threshold) ? point : best;
  }, null);
  const note = $('#trade-note');
  if (current) {
    note.textContent =
      `At threshold ${threshold.toFixed(2)}: ${pct(current.p_release_hostile)} of hostile tracks are released, ` +
      `${(current.p_release_benign * 1000).toFixed(1)} per 1,000 benign tracks are released, ` +
      `and ${pct(current.posterior_hostile)} of releases are on genuinely hostile tracks. ` +
      'Evidence model is synthetic and deliberately pessimistic; the release rate on benign tracks is the number to argue about.';
  } else {
    note.textContent = '';
  }
}

function chartFrame(svg, W, H) {
  svg.innerHTML = '';
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', H);
  svg.setAttribute('preserveAspectRatio', 'none');
  return { W, H };
}

function drawTailChart(svg, points, threshold) {
  const W = chartWidth(svg, 620);
  const H = 172;
  chartFrame(svg, W, H);
  const usable = points.filter((point) => point.p95_s !== null);
  if (!usable.length) {
    svg.appendChild(
      svgEl('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', fill: '#8c9aae', 'font-size': 12 }, 'No engaged runs at any threshold.'),
    );
    return;
  }

  const padL = 46;
  const padR = 40;
  const padT = 14;
  const padB = 26;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const maxP95 = Math.max(...usable.map((point) => point.p95_s)) * 1.05;
  const minT = points[0].threshold;
  const maxT = points[points.length - 1].threshold;
  const x = (t) => padL + ((t - minT) / (maxT - minT)) * plotW;
  const y = (v) => padT + plotH - (v / maxP95) * plotH;

  for (let i = 0; i <= 3; i += 1) {
    const gy = padT + (plotH * i) / 3;
    svg.appendChild(svgEl('line', { x1: padL, x2: W - padR, y1: gy, y2: gy, stroke: '#15181f' }));
    svg.appendChild(
      svgEl('text', { x: padL - 6, y: gy + 3, 'text-anchor': 'end', class: 'tick' }, fmtTime(maxP95 * (1 - i / 3))),
    );
  }

  const abandonMax = Math.max(...points.map((point) => point.abandoned_rate), 0.01);
  const areaPoints = [
    `M ${x(usable[0].threshold)} ${padT + plotH}`,
    ...usable.map((point) => `L ${x(point.threshold)} ${padT + plotH - (point.abandoned_rate / abandonMax) * plotH * 0.55}`),
    `L ${x(usable[usable.length - 1].threshold)} ${padT + plotH}`,
    'Z',
  ].join(' ');
  svg.appendChild(svgEl('path', { d: areaPoints, fill: '#e07a7a', opacity: 0.07 }));

  const line = usable.map((point, index) => `${index === 0 ? 'M' : 'L'} ${x(point.threshold)} ${y(point.p95_s)}`).join(' ');
  svg.appendChild(svgEl('path', { d: line, fill: 'none', stroke: '#6ea8fe', 'stroke-width': 1.4 }));

  for (const point of usable) {
    svg.appendChild(svgEl('circle', { cx: x(point.threshold), cy: y(point.p95_s), r: 1.6, fill: '#6ea8fe' }));
  }

  const markerX = x(threshold);
  svg.appendChild(
    svgEl('line', { x1: markerX, x2: markerX, y1: padT - 4, y2: padT + plotH, stroke: '#e0b05c', 'stroke-dasharray': '4 3' }),
  );
  svg.appendChild(
    svgEl('text', { x: markerX, y: 9, 'text-anchor': 'middle', fill: '#e0b05c', class: 'tick' }, `pid ${threshold.toFixed(2)}`),
  );

  for (const [index, value] of [minT, (minT + maxT) / 2, maxT].entries()) {
    svg.appendChild(
      svgEl(
        'text',
        {
          x: x(value),
          y: H - 4,
          'text-anchor': index === 0 ? 'start' : index === 2 ? 'end' : 'middle',
          class: 'tick',
        },
        value.toFixed(2),
      ),
    );
  }
}

function drawEvidenceChart(svg, points, threshold) {
  const W = chartWidth(svg, 620);
  const H = 172;
  chartFrame(svg, W, H);
  if (!points.length) return;

  const padL = 46;
  const padR = 46;
  const padT = 14;
  const padB = 26;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const minT = points[0].threshold;
  const maxT = points[points.length - 1].threshold;
  const x = (t) => padL + ((t - minT) / (maxT - minT)) * plotW;
  const y = (v) => padT + plotH - v * plotH;
  const maxPer1000 = Math.max(...points.map((point) => point.p_release_benign * 1000), 1);

  for (let i = 0; i <= 3; i += 1) {
    const gy = padT + (plotH * i) / 3;
    svg.appendChild(svgEl('line', { x1: padL, x2: W - padR, y1: gy, y2: gy, stroke: '#15181f' }));
    svg.appendChild(
      svgEl('text', { x: padL - 6, y: gy + 3, 'text-anchor': 'end', class: 'tick' }, `${((1 - i / 3) * 100).toFixed(0)}%`),
    );
  }

  const posterior = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${x(point.threshold)} ${y(point.posterior_hostile)}`)
    .join(' ');
  svg.appendChild(svgEl('path', { d: posterior, fill: 'none', stroke: '#e0b05c', 'stroke-width': 1.4 }));

  const benign = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${x(point.threshold)} ${padT + plotH - (point.p_release_benign * 1000 / maxPer1000) * plotH}`)
    .join(' ');
  svg.appendChild(
    svgEl('path', { d: benign, fill: 'none', stroke: '#7c8698', 'stroke-width': 1, 'stroke-dasharray': '3 3' }),
  );

  const markerX = x(threshold);
  svg.appendChild(
    svgEl('line', { x1: markerX, x2: markerX, y1: padT - 4, y2: padT + plotH, stroke: '#e0b05c', 'stroke-dasharray': '4 3', opacity: 0.7 }),
  );

  for (const [index, value] of [minT, (minT + maxT) / 2, maxT].entries()) {
    svg.appendChild(
      svgEl(
        'text',
        {
          x: x(value),
          y: H - 4,
          'text-anchor': index === 0 ? 'start' : index === 2 ? 'end' : 'middle',
          class: 'tick',
        },
        value.toFixed(2),
      ),
    );
  }

  svg.appendChild(
    svgEl(
      'text',
      { x: W - 4, y: padT - 3, 'text-anchor': 'end', fill: '#8b98a8', class: 'tick' },
      `${maxPer1000.toFixed(0)}/1k`,
    ),
  );
}

/* ------------------------------------------------------------------ audit */

function renderAudit(report) {
  const table = $('#audit-table');
  table.innerHTML = '';
  const verification = report.audit.verification;
  const badge = $('#audit-badge');
  badge.textContent = verification.ok ? `chain intact · ${verification.records} records` : `BROKEN at #${verification.first_bad_seq}`;
  badge.className = `badge ${verification.ok ? 'ok' : 'bad'}`;

  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['seq', 'sim t', 'gate', 'confidence', 'outcome', 'rule', 'authority', 'hash']) {
    headRow.appendChild(h('th', null, label));
  }
  head.appendChild(headRow);
  table.appendChild(head);

  const body = document.createElement('tbody');
  const decisions = report.audit.records.filter((record) => record.type === 'engagement_decision');
  for (const record of decisions) {
    const row = document.createElement('tr');
    const cells = [
      String(record.seq),
      fmtTime(record.sim_t),
      record.gate || '—',
      (record.confidence * 100).toFixed(0) + '%',
      record.outcome,
      record.rule || '—',
      record.authority || '—',
      record.hash.slice(0, 10),
    ];
    cells.forEach((cell, index) => {
      const td = h('td', index === 0 || index >= 3 ? 'num' : null, cell);
      if (index === 4) td.style.color = OUTCOME_COLORS[record.outcome] || '#e7ecf5';
      row.appendChild(td);
    });
    body.appendChild(row);
  }
  table.appendChild(body);
}

/* -------------------------------------------------------------- kick off */

window.addEventListener('resize', debounce(() => {
  if (state.report) {
    try {
      render(state.report);
    } catch (error) {
      showError(error.message);
    }
  }
}, 350));

window.addEventListener('error', (event) => showError(event.message));
window.addEventListener('unhandledrejection', (event) => {
  showError(event.reason && event.reason.message ? event.reason.message : String(event.reason));
});

boot();


