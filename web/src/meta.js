// Decode the cryptic model/prompt/outcome identifiers the API returns into
// human labels + brand colors. Family is inferred from the model_name string,
// so new models from a known family light up automatically.

export const FAMILIES = {
  claude:   { key: 'claude',   label: 'Claude',   vendor: 'Anthropic',   color: 'var(--fam-claude)' },
  qwen:     { key: 'qwen',     label: 'Qwen',     vendor: 'Alibaba',     color: 'var(--fam-qwen)' },
  deepseek: { key: 'deepseek', label: 'DeepSeek', vendor: 'DeepSeek',    color: 'var(--fam-deepseek)' },
  gptoss:   { key: 'gptoss',   label: 'gpt-oss',  vendor: 'OpenAI',      color: 'var(--fam-gptoss)' },
  kimi:     { key: 'kimi',     label: 'Kimi',     vendor: 'Moonshot AI', color: 'var(--fam-kimi)' },
  other:    { key: 'other',    label: 'Other',    vendor: '',            color: 'var(--ink-3)' },
}

// Ordered list of fighters for the countdown stage.
export const FIGHTER_FAMILIES = ['claude', 'qwen', 'deepseek', 'gptoss', 'kimi']

export function famKeyOf(name = '') {
  const s = String(name).toLowerCase()
  if (s.includes('claude')) return 'claude'
  if (s.includes('qwen')) return 'qwen'
  if (s.includes('deepseek')) return 'deepseek'
  if (s.includes('gpt-oss') || s.includes('gpt_oss') || s.includes('gptoss')) return 'gptoss'
  if (s.includes('kimi')) return 'kimi'
  return 'other'
}
export const famOf = (name) => FAMILIES[famKeyOf(name)]

// config_id = "<model>__<prompt>"
export function splitConfig(cid = '') {
  const i = cid.indexOf('__')
  return i < 0 ? { model: cid, prompt: '' } : { model: cid.slice(0, i), prompt: cid.slice(i + 2) }
}

// human names for the standard prompt strategies; falls back to the suffix.
const PROMPT_NAMES = {
  P1_zeroshot: 'Zero-shot', P2_fewshot: 'Few-shot', P3_dialect: 'Dialect-aware',
  P4_cot: 'Chain-of-thought', P5_selfcorrect: 'Self-correcting',
}
export function promptMeta(promptId = '', descMap = {}) {
  const short = (promptId.split('_')[0] || promptId).toUpperCase()
  const name = PROMPT_NAMES[promptId] || promptId.split('_').slice(1).join(' ') || promptId
  return { id: promptId, short, name, desc: descMap[promptId] || '' }
}

export const OUTCOME_META = {
  correct:             { label: 'correct',            color: 'var(--oc-correct)' },
  wrong_result:        { label: 'wrong result',       color: 'var(--oc-wrong)' },
  sql_exec_error:      { label: 'sql exec error',     color: 'var(--oc-exec)' },
  sql_policy_rejected: { label: 'policy rejected',    color: 'var(--oc-policy)' },
  empty_but_expected:  { label: 'empty but expected', color: 'var(--oc-empty)' },
  timeout:             { label: 'timeout',            color: 'var(--oc-timeout)' },
  model_error:         { label: 'model error',        color: 'var(--oc-model)' },
}
export const outcomeMeta = (k) => OUTCOME_META[k] || { label: k, color: 'var(--ink-3)' }
