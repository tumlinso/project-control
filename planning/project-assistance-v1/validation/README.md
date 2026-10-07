# What validation means here

The package checker validates this bootstrap's integrity and internal structure.
The four synthetic baseline unit tests validate only their listed fixture cases.
They intentionally do not cover odd-length pair input; a separate generated
scratch test is the proposed product evaluation, not a test already run against
Project Control.

Optional native plan validation imports the Todo validator bundled with the
Project Control checkout and validates inert plan payloads only. It does not
open a registered workspace or ledger and does not apply plans. Registered-
workspace validation is a separate read-only Project Control CLI operation
using the bundled Todo engine. Project Control source tests, live model trials, GPU
resource eviction, sandbox qualification and production cutover are not performed
by producing this package. Every product scenario remains `planned_not_executed`.

Use `scripts/pc-dev python planning/project-assistance-v1/scripts/check_package.py --native`
from the repository root. That mode validates
the package's inert plan payloads only. It does not require a registered
workspace. There is no plan application, deployment or source mutation script
in this bundle.
