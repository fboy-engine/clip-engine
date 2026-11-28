# Batch results (dry run, nothing was uploaded)

Run: https://github.com/fboy-engine/clip-engine/actions/runs/46161459331

**PASS 1 / FAIL 11 / SKIP 0 / CRASH 0 / TIMEOUT 0** out of 12

Regression window (must pass): **MISSING**

Passing windows from overlapping stream times can be the same moment: watch them before using two.

## Not passed, by gate

- cut planner: 5
- captions: 4
- cam benchmark: 2

## Every window

| job | source | window in file (s) | result | gate line | grade | artifact |
|---|---|---|---|---|---|---|
| s0-r1w3 | stream 190-254.9 s | 190-254.9 dense | PASS |  | GRADE: PASS (0 failed, 1 warnings, 20 passed) | clip-46161459331-s0-r1w3 |
| s0-r1w7 | stream 467-532.5 s | 467-532.5 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 1 valid starts (window ends 65.5s): skip this moment |  | clip-46161459331-s0-r1w7 |
| s1200-r1w1 | stream 1635-1698 s | 435-498 dense | FAIL | FAIL cam-bench: CamNotConfident: no confident cam: best candidate [0, 0, 1920, 1080] (no straight border found around the face (a face alone is not a cam); 4 of 4 sides have no border and no frame edge to stop at; rectangle covers 100.0% of the frame (a cam is 1.2-42%)): pass cam_rect \| stable-overlay fallback: no confident cam: best candidate [1347, 0, 1920, 1080] (3 of 4 sides have no stable edge and no frame edge to stop at): pass cam_rect |  | clip-46161459331-s1200-r1w1 |
| s1200-r1w9 | stream 1316-1380.2 s | 116-180.2 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 78% of the speech seconds (need 90%); captions cover only 13% of the whole clip window with no exclusions (need 75%) |  | clip-46161459331-s1200-r1w9 |
| s1800-r1w12 | stream 2293-2357 s | 493-557 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 63% of the speech seconds (need 90%); captions cover only 15% of the whole clip window with no exclusions (need 75%) |  | clip-46161459331-s1800-r1w12 |
| s1800-r1w4 | stream 1837-1899.7 s | 37-99.7 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover only 50% of the whole clip window with no exclusions (need 75%) |  | clip-46161459331-s1800-r1w4 |
| s2100-r1w6 | stream 2639-2700 s | 539-600 dense | FAIL | FAIL render: ValueError: dense cut: no other valid start left after the tracking retries: skip this moment |  | clip-46161459331-s2100-r1w6 |
| s2400-r1w12 | stream 2935-3000 s | 535-600 dense | FAIL | FAIL render: ValueError: dense cut: no other valid start left after the tracking retries: skip this moment |  | clip-46161459331-s2400-r1w12 |
| s3000-r1w10 | stream 3031-3096.4 s | 31-96.4 dense | FAIL | FAIL cam-bench: CamNotConfident: no confident cam: best candidate [0, 0, 1920, 1080] (no straight border found around the face (a face alone is not a cam); 4 of 4 sides have no border and no frame edge to stop at; rectangle covers 100.0% of the frame (a cam is 1.2-42%)): pass cam_rect \| stable-overlay fallback: no confident cam: best candidate [0, 0, 1826, 1080] (3 of 4 sides have no stable edge and no frame edge to stop at; rectangle covers 95.1% of the frame (a cam is 1.2-42%)): pass cam_rect |  | clip-46161459331-s3000-r1w10 |
| s3000-r1w2 | stream 3342-3405 s | 342-405 dense | FAIL | FAIL render: ValueError: dense cut: no other valid start left after the tracking retries: skip this moment |  | clip-46161459331-s3000-r1w2 |
| s600-r1w2 | stream 1079-1142 s | 479-542 dense | FAIL | FAIL render: ValueError: dense cut: no other valid start left after the tracking retries: skip this moment |  | clip-46161459331-s600-r1w2 |
| s600-r1w8 | stream 750-813 s | 150-213 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 85% of the speech seconds (need 90%); captions cover only 41% of the whole clip window with no exclusions (need 75%) |  | clip-46161459331-s600-r1w8 |
