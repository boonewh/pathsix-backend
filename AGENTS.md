# PathSix CRM agent instructions

## Active MVP scope lock

Read `docs/mcp-readiness-status.md` before planning or changing this project. Its
locked capabilities, implementation criteria and completion checklist define the
only planned build until the MVP is finished. Historical roadmaps do not expand it.

- Implement the MVP and necessary supporting changes; do not add speculative
  improvements, scaling work, extra tools or broad refactors along the way.
- You may investigate and fix discovered bugs or issues without separate scope
  approval. Keep repairs bounded to intended behavior, security, data integrity
  or reliability, and document relevant verification. Do not label an enhancement
  a bug fix to bypass the scope lock.
- If you believe something outside the MVP should be done, explicitly identify it
  as outside scope, explain the reason and impact, and ask the user before doing
  it. Wait for their decision on that work while continuing independent MVP work.
- Record other issues or ideas in the plan's final `Future consideration` section.
  That list is not authorized work. Only the user can approve scope expansion.
- Once the MVP is complete, report its evidence and external release status; do
  not automatically start deferred work. Preserve existing operational permission
  requirements and distinguish source, test and deployment status.
