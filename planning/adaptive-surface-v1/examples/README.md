# Illustrative records

All records in `contracts.json` are **synthetic fixtures**, not live packet aliases, real hashes, real registrations or current capabilities. They are checked against the target record schemas. Cross-field properties such as range order, actual source equality, permissions, alias uniqueness and dependency freshness require runtime validation, not JSON Schema alone.

The native Todo plans refer to actual workpackage IDs; these example packet/job/edge records do not. Never submit the fixture registration as a real project mutation.

The `search-query` example is the exact typed query form for `search(project="project-control", query={"kind":"task","target":"PC-AS1-CONTEXT"})`; discovery queries retain their existing behavior. Known gates, interfaces, decisions, runs, packets and investigation records use the same `{kind, target}` form.
