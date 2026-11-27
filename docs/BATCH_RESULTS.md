# Batch results (dry run, nothing was uploaded)

Run: https://github.com/fboy-engine/clip-engine/actions/runs/45977429410

**PASS 0 / FAIL 2 / SKIP 1 / CRASH 0 / TIMEOUT 0** out of 3

Regression window (must pass): **MISSING**

Passing windows from overlapping stream times can be the same moment: watch them before using two.

## Not passed, by gate

- captions: 2
- find: 1

## Every window

| job | source | window in file (s) | result | gate line | grade | artifact |
|---|---|---|---|---|---|---|
| c9-dense | clip #9 | 0-45 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 76% of the speech seconds (need 90%); captions cover only 33% of the whole clip window with no exclusions (need 75%) |  | clip-45977429410-c9-dense |
| s1800-w2 | stream 1938-1999.9 s | 138-199.9 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover only 35% of the whole clip window with no exclusions (need 75%) |  | clip-45977429410-s1800-w2 |
| c8 | clip #8 | - | SKIP | find gave no candidate window |  |  |
