# What validation means here

The package checker validates this bootstrap's integrity and internal structure.
The four synthetic baseline unit tests validate only their listed fixture cases.
They intentionally do not cover odd-length pair input; a separate generated
scratch test is the proposed product evaluation, not a test already run against
Project Control.

Native Todo plan validation must run against the trusted canonical installed
kernel during adoption. Project Control source tests, live model trials, GPU
resource eviction, sandbox qualification and production cutover are not performed
by producing this package. Every product scenario remains `planned_not_executed`.

Use `python scripts/check_package.py --native` only inside the correctly bound
local development environment. That mode is validation only. There is no plan
application, deployment or source mutation script in this bundle.
