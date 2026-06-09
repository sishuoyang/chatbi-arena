// The ChatBI Arena architecture as React Flow nodes + animated edges.
// Positions are computed by dagre (layout.js); nodes only declare a size + env.

export const EDGE_COLORS = {
  data: '#3fb950',       // OLTP writes + CDC replication
  ai: '#a371f7',         // Bedrock inference
  read: '#f5d90a',       // read-only analytic queries
  control: '#58a6ff',    // orchestration / requests
  trace: '#ec6cb9',      // LangFuse traces + scores
}

// Where each component runs -> card accent color (legend, not a bounding box).
export const ENV = {
  local: { label: 'Local / Docker', color: '#58a6ff' },
  aws: { label: 'AWS (Aurora ap-southeast-1 · Bedrock us-east-1)', color: '#ff9900' },
  ch: { label: 'ClickHouse Cloud (AWS)', color: '#f5d90a' },
  saas: { label: 'LangFuse Cloud (SaaS)', color: '#a371f7' },
}

const card = (id, env, title, subtitle, kind, w = 215) => ({
  id, type: 'card', position: { x: 0, y: 0 }, width: w, height: 66,
  data: { title, subtitle, kind, env, accent: ENV[env].color },
})

export const componentNodes = [
  card('user', 'local', 'Analyst', 'asks a question', 'user', 170),
  card('datagen', 'local', 'Data generator', 'Faker · seed + mutations', 'job'),
  card('golden', 'local', 'Golden set', '18 Qs × 5 tiers + golden SQL', 'data'),
  card('serving', 'local', 'Serving API', 'FastAPI · POST /ask', 'api'),
  card('harness', 'local', 'Benchmark harness', 'grid runner + grader', 'job'),
  card('agent', 'local', 'Agent core', 'loop · P1–P5 · SQL guard', 'agent', 230),
  card('dashboard', 'local', 'Leaderboard dashboard', 'FastAPI · reads ClickHouse + LangFuse', 'api'),

  card('aurora', 'aws', 'Aurora PostgreSQL', 'Serverless v2 · OLTP source', 'db', 235),
  card('bedrock', 'aws', 'Amazon Bedrock', 'Converse · Claude/Qwen/DeepSeek/gpt-oss/Kimi', 'ai', 300),

  card('cdc', 'ch', 'arena_cdc tables', 'ReplacingMergeTree (CDC landing)', 'db', 300),
  card('views', 'ch', 'v_* analytic views', 'FINAL · dedup current state', 'view', 300),
  card('evalruns', 'ch', 'eval_runs + v_leaderboard', 'results · cost-per-correct', 'db', 300),

  card('langfuse', 'saas', 'LangFuse Cloud', 'traces + correctness/cost/latency', 'trace', 300),
]

// All edges flow forward in the dagre LR ranking, so source=right, target=left.
// label = short (always shown); detail = full text (shown on hover).
const e = (id, source, target, label, detail, color) => ({
  id, source, target, label, type: 'flow',
  sourceHandle: 's-right', targetHandle: 't-left',
  data: { color: EDGE_COLORS[color], detail },
})

export const edges = [
  e('datagen-aurora', 'datagen', 'aurora', 'write', 'INSERT + status UPDATEs', 'data'),
  e('aurora-cdc', 'aurora', 'cdc', 'CDC', 'ClickPipes CDC · logical replication', 'data'),
  e('cdc-views', 'cdc', 'views', 'FINAL', 'FINAL dedup → current state', 'data'),

  e('agent-bedrock', 'agent', 'bedrock', 'Converse', 'Bedrock Converse: NL→SQL + token usage', 'ai'),
  e('agent-views', 'agent', 'views', 'SELECT', 'read-only SELECT (sandboxed)', 'read'),
  e('evalruns-dashboard', 'evalruns', 'dashboard', 'read', 'leaderboard read', 'read'),

  e('golden-harness', 'golden', 'harness', 'Qs', 'questions + golden SQL', 'control'),
  e('harness-agent', 'harness', 'agent', 'run', 'run grid: model × prompt', 'control'),
  e('user-serving', 'user', 'serving', '/ask', 'POST /ask', 'control'),
  e('serving-agent', 'serving', 'agent', 'reuse', 'reuses the agent core', 'control'),

  e('harness-evalruns', 'harness', 'evalruns', 'grade', 'grade by execution accuracy → write', 'data'),
  e('harness-langfuse', 'harness', 'langfuse', 'trace', 'traces + correctness/cost/latency scores', 'trace'),
  e('langfuse-dashboard', 'langfuse', 'dashboard', 'read', 'sessions + conversation traces, read live via the LangFuse API', 'trace'),
]
