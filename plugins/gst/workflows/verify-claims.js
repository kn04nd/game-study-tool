export const meta = {
  name: 'verify-claims',
  description: 'Adversarially verify the claims of ARCHITECTURE.md against the decompiled code and the data, then correct the document',
  whenToUse: 'In a study workspace once ARCHITECTURE.md is written. Optional args: {max: 30} claims to check',
  phases: [
    { title: 'Extract', detail: 'checkable claims with the evidence they cite' },
    { title: 'Verify', detail: 'one skeptic per claim re-reads the code' },
    { title: 'Report', detail: 'notes/VERIFY.md and corrections in ARCHITECTURE.md' },
  ],
}

const CLAIMS = {
  type: 'object',
  required: ['claims'],
  properties: {
    claims: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'section', 'claim', 'evidence'],
        properties: {
          id: { type: 'string' },
          section: { type: 'string' },
          claim: { type: 'string' },
          evidence: { type: 'string' },
        },
      },
    },
  },
}
const VERDICT = {
  type: 'object',
  required: ['id', 'verdict', 'reason', 'evidence'],
  properties: {
    id: { type: 'string' },
    verdict: { type: 'string', enum: ['confirmed', 'refuted', 'unclear'] },
    reason: { type: 'string' },
    evidence: { type: 'string' },
  },
}

const max = (args && args.max) || 30

phase('Extract')
const extracted = await agent(
  `Read ARCHITECTURE.md in the current study workspace and list up to ${max} checkable claims: formulas and constants, ` +
    'call flows (A calls B when C), data layouts, state transitions, addresses. Skip opinions and summaries. ' +
    'Give each an id (c1, c2...), its section, the claim in one sentence and the evidence the document cites.',
  { label: 'extract', phase: 'Extract', schema: CLAIMS, agentType: 'gst:code-navigator' },
)
const claims = extracted.claims.slice(0, max)
log(`${claims.length} claims to verify`)

phase('Verify')
const verdicts = await parallel(
  claims.map(c => () =>
    agent(
      `Try to refute this claim about the game by reading the code and data yourself: "${c.claim}" ` +
        `(section ${c.section}; the document cites: ${c.evidence}). Use gst q show/callers/callees/refs/strings and ` +
        'open the cited files. Answer "confirmed" only if the code says so unambiguously, "refuted" if it says ' +
        'otherwise, and "unclear" when it does not settle it. Return the id, the verdict, a short reason and the ' +
        `path:line you relied on. Do not edit any file. The id is ${c.id}.`,
      { label: `verify:${c.id}`, phase: 'Verify', schema: VERDICT, agentType: 'gst:code-navigator' },
    ),
  ),
)
const rows = verdicts.filter(Boolean)
const count = v => rows.filter(r => r.verdict === v).length

phase('Report')
await agent(
  'In the current study workspace write notes/VERIFY.md (pt-BR): a table with claim, verdict, reason and evidence for ' +
    `these results ${JSON.stringify(rows)} of the claims ${JSON.stringify(claims)}. Then correct ARCHITECTURE.md: ` +
    'rewrite refuted claims from the evidence, move unclear ones to section 10 (não verificado), and run ' +
    '`gst report --check` until it passes.',
  { label: 'report', phase: 'Report', agentType: 'gst:synthesizer' },
)
return { claims: claims.length, confirmed: count('confirmed'), refuted: count('refuted'), unclear: count('unclear'),
         unanswered: claims.length - rows.length }
