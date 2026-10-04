# Preparation validation

These files record what was actually checked before delivering the bootstrap, not whether the new product has been implemented.

Both complete native plans passed the current live `plan_preview` validator. Each receipt is an explicitly labeled extraction of the returned validation fields. At preparation its `plan_digest` matched the packaged native plan using the validator’s canonical serialization. The user-requested search correction subsequently changed the Project Control context objective; `search-correction.json` records the corrected digests and marks that historical preview stale. The preview bytes are retained unchanged. The subsequent parallel layout correction changes both plans; `parallel-correction.json` binds both corrected plan digests and file hashes to their unchanged historical previews. The original search correction sidecar remains historical evidence. Both corrected plans need fresh live validation before bootstrap/import. It is not an authorization to apply later.

The original package and helper suite passed **22 tests and 9 subtests**. Current parallel-package checks are recorded separately in `parallel-package-checks.txt`; historical test receipts remain unchanged. Six target record examples passed JSON Schema validation. The package checker covers all 45 requirements, 16 outcomes, 72 acceptance cases, native parent/prerequisite topology, cross-authority dependency topology and role/tool-mode restrictions. A missing product test correctly fails the product gate runner.

An initial draft’s epic-to-child prerequisite edges conflicted with the live validator’s parent edges. The final plans use required task-state closure gates instead. The failed draft was never applied.

`preparation.json` separates package tests, live plan previews and still-required product qualification. No live source/Todo mutation, deployment, model launch or GPU work was performed while preparing this package. No product acceptance pass is fabricated.
