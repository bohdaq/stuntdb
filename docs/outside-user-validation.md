# Outside-user validation

**Status: waived for v0.3.0 by the owner on 2026-10-05.** Automated tests and the demo are not a substitute for an independent person using stuntdb. No external tester has been contacted or result recorded by this work. Issue #2 is excluded from the release gate at the owner’s request; the checklist below remains available for future validation.

Ask a volunteer unfamiliar with the repository to follow the quick start from a clean environment. Use synthetic or approved disposable fixtures only. Do not send real database dumps, credentials or personal data in feedback.

## Session checklist

1. Record OS, Python version, package version and installation method.
2. Follow [quick start](quickstart.md) without undocumented help; record time, blocked steps and confusing messages.
3. Confirm the synthetic demo produces four masked rows and retains cyclic references.
4. Try a small disposable MySQL or MariaDB fixture with their own reviewed config and empty matching target.
5. Try a `review` rule, a populated target, schema drift and an altered SQL checksum; confirm refusal and understandable recovery instructions.
6. Review the masking limits and ask the tester what they believe is protected and what remains disclosed.
7. Record whether they can repeat the workflow unaided, and turn concrete failures into issues without including data or secrets.

## Result template

```text
Tester: [consented display name or anonymous identifier]
Date:
Package version / commit:
OS / Python / database version:
Installation method:
Synthetic demo: passed / failed / not run
Independent database fixture: passed / failed / not run
Time to first successful verification:
Help required:
Misunderstood limits:
Actionable problems and issue links:
Repeat without help: yes / no / not attempted
Overall result: passed / failed / incomplete
```

Keep this status pending until a real tester completes the checklist and the result is recorded with their consent. Stable release decisions should account for unresolved failures, not just a completed session.
