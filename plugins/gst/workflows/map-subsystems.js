export const meta = {
  name: 'map-subsystems',
  description: 'Study the systems of the current game in parallel (one code-navigator per system), then consolidate ARCHITECTURE.md',
  whenToUse: 'In a study workspace after gst decompile: a note per system and section 4 of ARCHITECTURE.md. Optional args: a list of system names, or {systems: [...], max: 8}',
  phases: [
    { title: 'Map', detail: 'gst map and the choice of systems' },
    { title: 'Study', detail: 'one code-navigator per system, in parallel' },
    { title: 'Consolidate', detail: 'the synthesizer updates ARCHITECTURE.md and runs gst report' },
  ],
}

const PICKED = {
  type: 'object',
  required: ['systems'],
  properties: {
    systems: {
      type: 'array',
      items: {
        type: 'object',
        required: ['label', 'slug', 'question', 'types'],
        properties: {
          label: { type: 'string' },
          slug: { type: 'string' },
          question: { type: 'string' },
          types: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  },
}

const wanted = Array.isArray(args) ? args : (args && args.systems) || null
const max = (args && args.max) || 8

phase('Map')
const picked = await agent(
  'In the current study workspace, run `gst map` (or read notes/MAP.md when it is newer than index/code.db). ' +
    (wanted
      ? `Take these systems: ${JSON.stringify(wanted)}.`
      : `Choose the ${max} systems that matter most to understand how this game works (gameplay and simulation first; ` +
        'UI widgets and data plumbing only when they are central).') +
    ' For each return: label (pt-BR), slug (lowercase, hyphens), the central question to answer about it, and its 3-6 ' +
    'key types as qualified names that `gst q def` finds. Check each type with `gst q def` before returning it.',
  { label: 'map', phase: 'Map', schema: PICKED, agentType: 'gst:code-navigator' },
)
const systems = picked.systems.slice(0, max)
log(`${systems.length} systems: ${systems.map(s => s.label).join(', ')}`)

phase('Study')
const studied = await parallel(
  systems.map(s => () =>
    agent(
      `Study the system "${s.label}" of this game. Question: ${s.question} Start from: ${s.types.join(', ')}.\n` +
        `DELIVERABLE: notes/logic/${s.slug}.md\n` +
        `First create it pre-filled: gst notes new logic/${s.slug} --template sistema (extend it if it exists). ` +
        'Navigate with gst q (def, outline, show, callers, callees, refs, strings) and read the data files it uses. ' +
        `Fill notes/logic/${s.slug}.md in pt-BR: purpose, entry points, key types with path:line, the flow from ` +
        'trigger to data, formulas with their constants, a Mermaid stateDiagram-v2 if there is a state machine, and a ' +
        '"Não verificado" list. Add verified facts to FACTS.md. Finish with `gst notes check` and fix broken citations. ' +
        'Reply with at most 8 lines: the answer and the note path.',
      { label: s.slug, phase: 'Study', agentType: 'gst:code-navigator' },
    ),
  ),
)
const done = systems.filter((s, i) => studied[i])
const missing = systems.filter((s, i) => !studied[i]).map(s => s.label)
if (missing.length) log(`not studied: ${missing.join(', ')}`)

phase('Consolidate')
await agent(
  'Update ARCHITECTURE.md in the current study workspace from these notes: ' +
    done.map(s => `notes/logic/${s.slug}.md (${s.label})`).join('; ') +
    '.\nDELIVERABLE: ARCHITECTURE.md\nSection 4 gets one ### subsection per system (purpose, key types with path:line, data, formulas, state ' +
    'machines, link to the note); section 8 a Mermaid graph of how the systems connect; section 10 the union of ' +
    'what the notes could not verify. Run `gst report` first (it fills sections 1-2), then `gst report --check`, ' +
    'and fix what it reports.',
  { label: 'synthesize', phase: 'Consolidate', agentType: 'gst:synthesizer' },
)
return { systems: done.map(s => s.label), notes: done.map(s => `notes/logic/${s.slug}.md`), missing }
