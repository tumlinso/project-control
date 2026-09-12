# WF2 A acceptance registry

Run this task's registration check before any WF2 milestone relies on the A02
environment baseline:

```sh
python -B -m unittest discover -s tests/wf2_acceptance/a -p 'test_a02*.py' -v
```

This dedicated standard-library command intentionally avoids changing the
historical repository-wide test-package topology.  Continue to run the
repository's canonical `python -B -m unittest discover -s tests -v` suite as
the broad regression check.
