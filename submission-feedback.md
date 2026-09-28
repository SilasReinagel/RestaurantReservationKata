# Submission feedback (v1.1)

This would pass, and the driving of the model is what makes it pass.

If this were submitted as a take-home, the artifact scores about **8/10** and the AI-native process about **8/10**. Requirements, tool design, state integrity, separation, and scope are strong. The open hole is verification: the real model and the browser were never run, and the model's own final review named that as the high finding. The candidate stopped there.

## Design

### Positive

- The product calls are explicit and then built: a table is held for 2 hours, availability is real tables rather than a seat count, parties of 7–8 may combine tables, times are the restaurant's local timezone, modify is in scope, and alternatives are only real slots even when there are fewer than two.
- Create and modify confirmation is server state, not a prompt. The first call is a dry run of the real write. The same proposal is saved only when it is repeated on a later guest turn. A preview from a turn whose reply never reached the guest is discarded.
- Cancel is refused unless `get_reservation` returned that code on an earlier turn. The follow-up fix (TC-4.2.04) drops a lookup when the model fails before the guest sees the details.
- Writes run in `BEGIN IMMEDIATE`. One confirmed booking per phone per date is a partial unique index. A 20-thread race test is in the suite.
- Rules live in `rules.py`. The OpenAI client is a function the tests replace. The README separates stakeholder decisions from assumptions, including the `BV-DEMO` phone conflict with example 10.1.
- Traceability is written down: user story, acceptance criterion, test case. The offline suite is 257 tests. Stretch goals are left undone. A design-review pass cut scope before implementation.

### Negative

- Overlap is checked in application code inside the transaction. There is no occupancy primary key, so a wrong overlap check can still double-book. The unique index only covers one booking per phone per date.
- The confirmation gate checks that the same proposal was previewed earlier. It does not check that the guest's words were "yes". The model can call the tool again and the server will save.
- Nobody ran the app end to end. The 8 live tests are skipped, and the UI was never opened in a browser. The final review marks this high (F1) and quotes the BRD check for opening the browser. Conversation quality is unproven. The submitted README already has the test count and the live-test command, so the stale-README finding from that review is not in the files.
- An unexpected error (for example SQLite "database is locked") skips the unseen-preview cleanup. That is the same class of bug as TC-4.2.04 on a rarer path.
- Chat sessions live in memory. Tool logs print guest names and phone numbers. `/api/reservations` has no auth. Name and phone changes require cancel and rebook.
- The zip includes `__pycache__` and `data/bella_vista.db`. `.gitignore` already excludes both.

## AI-native

### Positive

- The sequence is gated. Analyze the BRD with no design. Then seven written decisions and an updated analysis, still no architecture. Then a technical design, still no code. Then a design review aimed at a take-home, including over-engineering, risk, and code-review questions, with OpenAI instead of Claude. Then implementation. Then a short list of agreed fixes. Then tests. Then one bug. Then a review that must not change code.
- The human made the judgment calls the BRD left open, including the scope inconsistency on modify, and the choice to use the confirmation code only in this version while noting that code plus phone would be stronger.
- The build prompt keeps rules in code, asks for tests per feature, and asks the model to surface design issues instead of hiding them.
- The fix prompt is bounded: server-side confirmation, prefer 4+4 over a 6-top for a party of 8, document the seed phone conflict, keep the architecture, add no features.
- The test prompt requires a map from user story to acceptance criterion to test case, and it forbids code changes until a failure's root cause is explained.
- TC-4.2.04 is a precise change: smallest fix, keep the cancel flow, update the test, run the full suite, report the result.

### Negative

- The final review's top item was left undone. The model said to run the live tests and the demo script in a browser before submitting. There is no later turn that does that.
- The fix list skips item 2, and the wording has typos ("usinga", "Fo each user strory"). The instruction was not re-read.
- The export includes an IDE note that the `.env` file was opened. That is not a task, and it puts the secrets file in the agent's context.

## Redact

No API key string appears in the transcript. `.env.example` only has the placeholder `sk-...`. These still should not go out:

- Drop the `<ide_opened_file>` turn. It contains `e:\srcctrl\nexera_test\.env`, which points at the secrets file.
- The line `Ran 4 shell commands (ctrl+o to expand)` under the implement prompt is client chrome, not something the candidate typed.
- Do not ship `data/bella_vista.db` or `__pycache__` with the zip. The gitignore already names them.
- The BRD's interviewer-only evaluation section is in the repo, and the transcript quotes it. Leave that out of anything shared beyond the interview.
