// The ChatBI Arena architecture, as React Flow nodes + animated edges.
// Zones (translucent backgrounds) group nodes by where they run.

export const EDGE_COLORS = {
  data: '#3fb950',       // OLTP writes + CDC replication
  ai: '#a371f7',         // Bedrock inference
  read: '#f5d90a',       // read-only analytic queries
  control: '#58a6ff',    // orchestration / requests
  trace: '#ec6cb9',      // LangFuse traces + scores
  telemetry: '#39c5cf',  // OTel telemetry
}

// ---- Zones (rendered behind the cards) -------------------------------------
const zones = [
  { id: 'z-local', type: 'zone', position: { x: 40, y: 40 },
    data: { label: 'Local / Docker  (your machine)', accent: '#58a6ff' },
    style: { width: 450, height: 760 } },
  { id: 'z-aws', type: 'zone', position: { x: 515, y: 130 },
    data: { label: 'AWS · ap-southeast-1', accent: '#ff9900' },
    style: { width: 290, height: 470 } },
  { id: 'z-ch', type: 'zone', position: { x: 850, y: 60 },
    data: { label: 'ClickHouse Cloud · AWS ap-southeast-1', accent: '#faff00' },
    style: { width: 410, height: 660 } },
  { id: 'z-saas', type: 'zone', position: { x: 850, y: 770 },
    data: { label: 'SaaS', accent: '#a371f7' },
    style: { width: 410, height: 120 } },
]

// ---- Component cards --------------------------------------------------------
const card = (id, x, y, data, w = 190) =>
  ({ id, type: 'card', position: { x, y }, data, style: { width: w }, zIndex: 1 })

const cards = [
  // Local / Docker
  card('user', 130, 70, { title: 'Analyst', subtitle: 'asks a question', kind: 'user', accent: '#8b96a8' }, 150),
  card('serving', 70, 175, { title: 'Serving API', subtitle: 'FastAPI · POST /ask', kind: 'api', accent: '#58a6ff' }),
  card('harness', 275, 175, { title: 'Benchmark harness', subtitle: 'grid runner + grader', kind: 'job', accent: '#58a6ff' }),
  card('agent', 70, 330, { title: 'Agent core', subtitle: 'loop · P1–P5 prompts · SQL guard', kind: 'agent', accent: '#58a6ff' }),
  card('golden', 275, 330, { title: 'Golden set', subtitle: '20 Qs × 5 tiers + golden SQL', kind: 'data', accent: '#58a6ff' }),
  card('datagen', 70, 510, { title: 'Data generator', subtitle: 'Faker · seed + mutations', kind: 'job', accent: '#58a6ff' }),
  card('collector', 275, 510, { title: 'OTel collector', subtitle: 'clickstack-otel-collector', kind: 'otel', accent: '#39c5cf' }),
  card('dashboard', 70, 690, { title: 'Leaderboard dashboard', subtitle: 'FastAPI · reads ClickHouse', kind: 'api', accent: '#58a6ff' }),

  // AWS
  card('aurora', 545, 200, { title: 'Aurora PostgreSQL', subtitle: 'Serverless v2 · OLTP source', kind: 'db', accent: '#ff9900' }, 230),
  card('bedrock', 545, 470, { title: 'Amazon Bedrock', subtitle: 'Converse · Nova + Claude', kind: 'ai', accent: '#ff9900' }, 230),

  // ClickHouse Cloud
  card('cdc', 890, 130, { title: 'arena_cdc tables', subtitle: 'ReplacingMergeTree (CDC landing)', kind: 'db', accent: '#faff00' }, 330),
  card('views', 890, 280, { title: 'v_* analytic views', subtitle: 'FINAL · dedup current state', kind: 'view', accent: '#faff00' }, 330),
  card('evalruns', 890, 430, { title: 'eval_runs + v_leaderboard', subtitle: 'results · cost-per-correct', kind: 'db', accent: '#faff00' }, 330),
  card('otel', 890, 600, { title: 'otel_* / ClickStack', subtitle: 'traces · metrics · logs', kind: 'otel', accent: '#faff00' }, 330),

  // SaaS
  card('langfuse', 890, 815, { title: 'LangFuse Cloud', subtitle: 'traces + correctness/cost/latency scores', kind: 'trace', accent: '#a371f7' }, 330),
]

export const nodes = [...zones, ...cards]

// ---- Animated data-flow edges ----------------------------------------------
const e = (id, source, target, label, color, sh, th) => ({
  id, source, target, label, type: 'flow',
  sourceHandle: sh, targetHandle: th,
  data: { color: EDGE_COLORS[color] },
})

export const edges = [
  // OLTP + CDC data plane (green)
  e('datagen-aurora', 'datagen', 'aurora', 'INSERT + status UPDATEs', 'data', 's-right', 't-left'),
  e('aurora-cdc', 'aurora', 'cdc', 'ClickPipes CDC · logical replication', 'data', 's-right', 't-left'),
  e('cdc-views', 'cdc', 'views', 'FINAL dedup', 'data', 's-bottom', 't-top'),

  // AI inference (purple)
  e('agent-bedrock', 'agent', 'bedrock', 'Converse (NL→SQL) + tokens', 'ai', 's-right', 't-left'),

  // Read-only analytic queries (yellow)
  e('agent-views', 'agent', 'views', 'read-only SELECT (sandboxed)', 'read', 's-right', 't-left'),
  e('evalruns-dashboard', 'evalruns', 'dashboard', 'leaderboard (read)', 'read', 's-left', 't-right'),

  // Orchestration / requests (blue)
  e('golden-harness', 'golden', 'harness', 'questions + golden SQL', 'control', 's-top', 't-bottom'),
  e('harness-agent', 'harness', 'agent', 'run grid: model × prompt', 'control', 's-left', 't-right'),
  e('user-serving', 'user', 'serving', 'POST /ask', 'control', 's-bottom', 't-top'),
  e('serving-agent', 'serving', 'agent', 'reuses agent core', 'control', 's-bottom', 't-top'),

  // Grading results + traces (pink/green)
  e('harness-evalruns', 'harness', 'evalruns', 'grade (exec accuracy) → write', 'data', 's-right', 't-left'),
  e('harness-langfuse', 'harness', 'langfuse', 'traces + scores', 'trace', 's-bottom', 't-left'),

  // Telemetry (cyan)
  e('datagen-collector', 'datagen', 'collector', 'OTLP', 'telemetry', 's-right', 't-left'),
  e('serving-collector', 'serving', 'collector', 'OTLP', 'telemetry', 's-bottom', 't-top'),
  e('collector-otel', 'collector', 'otel', 'write telemetry', 'telemetry', 's-right', 't-left'),
]
