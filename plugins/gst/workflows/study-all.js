export const meta = {
  name: 'study-all',
  description: 'Turn a finished gst study into notes: one agent per system, mechanic or asset family in waves (the focus list first), then ARCHITECTURE.md, verify-claims and gst report --check',
  whenToUse: 'In a study workspace after gst study finished. Optional args: {focus: [...], max: 12, wave: 4, verify: 20} (focus defaults to gst config show -> focus; verify: 0 skips verify-claims)',
  phases: [
    { title: 'Check', detail: 'gst study finished? (gst status, gst jobs list, gst coverage)' },
    { title: 'Plan', detail: 'systems.json, asset families and the focus list -> one task per agent' },
    { title: 'Study', detail: 'waves of agents, focus first, each writing its deliverable' },
    { title: 'Consolidate', detail: 'the synthesizer updates ARCHITECTURE.md' },
    { title: 'Verify', detail: 'verify-claims on ARCHITECTURE.md' },
    { title: 'Report', detail: 'gst report --check and gst notes check' },
  ],
}

const READY = {
  type: 'object',
  required: ['ready', 'summary'],
  properties: {
    ready: { type: 'boolean' },
    summary: { type: 'string' },
    running: { type: 'integer' },
    pending: { type: 'integer' },
    failed: { type: 'array', items: { type: 'string' } },
  },
}

const PLAN = {
  type: 'object',
  required: ['focus', 'tasks'],
  properties: {
    focus: { type: 'array', items: { type: 'string' } },
    tasks: {
      type: 'array',
      items: {
        type: 'object',
        required: ['slug', 'label', 'kind', 'template', 'question', 'priority'],
        properties: {
          slug: { type: 'string' },
          label: { type: 'string' },
          kind: { type: 'string', enum: ['system', 'mechanic', 'ragdoll', 'network', 'maps', 'audio', 'models', 'character', 'code'] },
          template: { type: 'string' },
          question: { type: 'string' },
          system: { type: 'string' },
          match: { type: 'string' },
          focus: { type: 'string' },
          priority: { type: 'integer' },
        },
      },
    },
  },
}

const CHECKED = {
  type: 'object',
  required: ['ok', 'problems'],
  properties: {
    ok: { type: 'boolean' },
    problems: { type: 'array', items: { type: 'string' } },
    unfilled: { type: 'array', items: { type: 'string' } },
  },
}

// kind -> (agent, template, notes folder)
const KINDS = {
  system: ['gst:code-navigator', 'sistema', 'logic'],
  mechanic: ['gst:mechanics-analyst', 'mecanica', 'logic'],
  ragdoll: ['gst:mechanics-analyst', 'ragdoll-fisica', 'logic'],
  network: ['gst:code-navigator', 'rede-voz', 'logic'],
  maps: ['gst:asset-analyst', 'mapa-niveis', 'logic'],
  audio: ['gst:asset-analyst', 'audio-catalogo', 'assets'],
  models: ['gst:asset-analyst', 'modelos-3d-dossie', 'assets'],
  character: ['gst:asset-analyst', 'personagem-modelos-animacoes', 'assets'],
  code: ['gst:code-navigator', 'arquitetura-codigo', 'code'],
}

const opts = args && typeof args === 'object' && !Array.isArray(args) ? args : {}
const max = opts.max || 12
const wave = opts.wave || 4
const verifyMax = opts.verify === undefined ? 20 : opts.verify
const askedFocus = Array.isArray(args) ? args : opts.focus || null

phase('Check')
const ready = await agent(
  'In the current study workspace check that `gst study` finished: run `gst status`, `gst jobs list` and `gst coverage`. ' +
    'ready = no job is running or pending (failed or skipped jobs are fine: list the failed ones by name). ' +
    'Do not start, retry or reset any job. Summarize in 2 lines what was extracted and analyzed.',
  { label: 'check', phase: 'Check', schema: READY, agentType: 'gst:code-navigator', effort: 'low' },
)
if (!ready || !ready.ready) {
  log(`gst study is not finished: ${ready ? ready.summary : 'no answer'}`)
  return { ready: false, summary: ready ? ready.summary : null, next: 'run gst study <game> (rerun resumes), then /gst:study-all' }
}
if (ready.failed && ready.failed.length) log(`failed jobs (their outputs are missing): ${ready.failed.join(', ')}`)

phase('Plan')
const plan = await agent(
  'Plan the study notes of the current study workspace. Read: analysis/systems.json (systems with key types), the ' +
    'asset families (analysis/gltf_summary.json and dcc.json `data.families`, audio_catalog.json summary, ' +
    'godot_scenes.json / prefabs.json / unreal_assets.json summaries, joints.json or godot_animation.json ragdolls), ' +
    'recon/recon.json `net_voice`, analysis/code_architecture.json (modules, libraries, patterns), and the notes ' +
    'that already exist under notes/ (do not plan a note that exists). ' +
    (askedFocus
      ? `The focus list is ${JSON.stringify(askedFocus)}.`
      : 'The focus list is `focus` in `gst config show --json`.') +
    ` Return the focus list you used and at most ${max} tasks, one system, mechanic or asset family each: ` +
    'kind (system | mechanic | ragdoll | network | maps | audio | models | character | code), slug (lowercase, hyphens, ' +
    'pt-BR), label (pt-BR), the central question, `system` (a regex on the systems.json label it belongs to, when ' +
    'any), `match` (a regex on script/table names whose tunables matter, when any), template (sistema, mecanica, ' +
    'ragdoll-fisica, rede-voz, mapa-niveis, audio-catalogo, modelos-3d-dossie, personagem-modelos-animacoes, ' +
    'arquitetura-codigo), ' +
    '`focus` (the focus item it serves, or empty) and priority (1 = first). Every focus item that exists in this ' +
    'game gets a task, ordered as the focus list; then the gameplay systems that matter most; always one audio task, ' +
    'one maps task and, when the game has decompiled code, one code task (kind code, slug arquitetura-codigo, template ' +
    'arquitetura-codigo: structure, libraries, entry points, design patterns, performance techniques). Only plan what ' +
    'the data supports (a focus item absent from this game: leave it out).',
  { label: 'plan', phase: 'Plan', schema: PLAN, agentType: 'gst:code-navigator' },
)
const tasks = plan.tasks
  .filter(t => KINDS[t.kind])
  .sort((a, b) => a.priority - b.priority)
  .slice(0, max)
if (plan.tasks.length > tasks.length) log(`planned ${plan.tasks.length} tasks, kept ${tasks.length} (max ${max})`)
log(`focus: ${plan.focus.join(', ')} | ${tasks.map(t => t.label).join(', ')}`)

function deliverable(t) {
  return `notes/${KINDS[t.kind][2]}/${t.slug}.md`
}

function prompt(t) {
  const [, fallbackTemplate, layer] = KINDS[t.kind]
  const template = t.template || fallbackTemplate
  const flags = (t.system ? ` --system "${t.system}"` : '') + (t.match ? ` --match "${t.match}"` : '')
  return (
    `Study "${t.label}" of this game. Question: ${t.question}\n` +
    `DELIVERABLE: ${deliverable(t)}\n` +
    `First create it pre-filled: gst notes new ${layer}/${t.slug} --template ${template}${flags} ` +
    '(if it exists, extend it). Fill it from analysis/*.json and the code (gst q), with path:line or the analysis ' +
    'key for every claim, a "Relevância para o meu jogo" section and a closed "Não verificado" list. Save every ' +
    'few steps. Add verified facts to FACTS.md. Finish with gst notes check. Reply in at most 8 lines.'
  )
}

phase('Study')
const done = []
const missing = []
for (let i = 0; i < tasks.length; i += wave) {
  const batch = tasks.slice(i, i + wave)
  log(`wave ${i / wave + 1}: ${batch.map(t => t.slug).join(', ')}`)
  const results = await parallel(
    batch.map(t => () => agent(prompt(t), { label: t.slug, phase: 'Study', agentType: KINDS[t.kind][0] })),
  )
  batch.forEach((t, k) => (results[k] ? done : missing).push(t))
}
if (missing.length) log(`no answer from: ${missing.map(t => t.slug).join(', ')} (their notes may still exist)`)

phase('Consolidate')
await agent(
  'Update ARCHITECTURE.md in the current study workspace from these notes: ' +
    tasks.map(t => `${deliverable(t)} (${t.label})`).join('; ') +
    '.\nDELIVERABLE: ARCHITECTURE.md\n' +
    'Run `gst report` first (sections 1-2 from recon). Section 3 summarizes notes/code/arquitetura-codigo.md when it ' +
    'exists (structure, libraries, patterns and performance techniques, with its path:line). Section 4 gets one ### per system with key types and ' +
    'path:line, always ### Áudio and ### Mapas e níveis; section 5 the asset dossiers; section 8 a Mermaid graph of ' +
    'how the systems connect; section 10 the union of the notes\' "Não verificado". Then `gst report --check`.',
  { label: 'synthesize', phase: 'Consolidate', agentType: 'gst:synthesizer' },
)

let verified = null
if (verifyMax > 0) {
  phase('Verify')
  for (const name of ['gst:verify-claims', 'verify-claims']) {
    try {
      verified = await workflow(name, { max: verifyMax })
      break
    } catch (e) {
      log(`workflow ${name}: ${e && e.message ? e.message : e}`)
    }
  }
  if (!verified) log('verify-claims did not run: run /gst:verify-claims by hand')
}

phase('Report')
const checked = await agent(
  'In the current study workspace run `gst report --check` and `gst notes check`. Fix what is cheap to fix in ' +
    'ARCHITECTURE.md or the notes (broken citations, empty sections), run them again, and return ok (report --check ' +
    'passes), the remaining problems, and the notes that still have <!-- preencher --> sections.',
  { label: 'report', phase: 'Report', schema: CHECKED, agentType: 'gst:synthesizer' },
)
return {
  focus: plan.focus,
  notes: done.map(deliverable),
  missing: missing.map(deliverable),
  verify: verified,
  report_ok: checked ? checked.ok : false,
  problems: checked ? checked.problems : ['no answer from the report agent'],
}
