---
description: "Debug a failing test in GoldenGibbon. Reads the test file, identifies the root cause, and proposes a fix."
argument-hint: "<test file or test name>"
agent: agent
---

Debug the failing test: $input

## Steps
1. Run the test to capture the current error output:
   ```bash
   .venv-test/bin/python -m pytest $input -v 2>&1
   ```
2. Read the failing test file to understand what it's testing
3. Read the source file being tested
4. Identify the root cause (assertion mismatch, missing mock, wrong fixture, import error, etc.)
5. Propose the minimal fix — prefer fixing the source over changing the test, unless the test itself has a bug
6. Apply the fix and re-run the test to confirm it passes
