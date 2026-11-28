# Batch results (dry run, nothing was uploaded)

Run: https://github.com/fboy-engine/clip-engine/actions/runs/46113640647

**PASS 1 / FAIL 52 / SKIP 1 / CRASH 0 / TIMEOUT 2** out of 56

Regression window (must pass): **PASS**

Passing windows from overlapping stream times can be the same moment: watch them before using two.

## Not passed, by gate

- cut planner: 30
- captions: 13
- panel check: 4
- cam benchmark: 3
- timeout: 2
- cam enhance: 1
- find: 1
- tracking: 1

## Every window

| job | source | window in file (s) | result | gate line | grade | artifact |
|---|---|---|---|---|---|---|
| regression | stream 2465-2519 s (known passing window) | 365-419 dense | PASS |  | GRADE: PASS (0 failed, 0 warnings, 21 passed) | clip-46113640647-regression |
| c1-dense | clip #1 | 0-37.6 dense | FAIL | FAIL render: ValueError: dense cut: no other valid start left after the tracking retries: skip this moment |  | clip-46113640647-c1-dense |
| c1-plain | clip #1 | 0-37.6 | FAIL | FAIL render: ValueError: panel check failed (the grade's own measurement, so this clip would fail the grade): 79.2% inside on 93.0% detected frames of 57 (needs >= 95%); cut at 2s, 2.5s, 5s, 6.5s, 13s, 14.5s, 15s, 20s, 20.5s, 23s, 23.5s (out/tracking.json has the numbers; the render-time tracker said 94.7%) |  | clip-46113640647-c1-plain |
| c3-dense | clip #3 | 0-60 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 87% of the speech seconds (need 90%); captions cover only 60% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-c3-dense |
| c3-plain | clip #3 | 0-60 | FAIL | FAIL render: ValueError: panel check failed (the grade's own measurement, so this clip would fail the grade): 94.8% inside on 93.5% detected frames of 62 (needs >= 95%); cut at 7s, 24.5s, 27s (out/tracking.json has the numbers; the render-time tracker said 88.7%) |  | clip-46113640647-c3-plain |
| c4-dense | clip #4 | 0-35 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 72% of the speech seconds (need 90%); captions cover only 64% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-c4-dense |
| c5-dense | clip #5 | 0-55.6 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 1 valid starts (window ends 55.6s): skip this moment |  | clip-46113640647-c5-dense |
| c5-plain | clip #5 | 0-55.6 | FAIL | FAIL render: ValueError: panel check failed (the grade's own measurement, so this clip would fail the grade): 75.7% inside on 98.6% detected frames of 71 (needs >= 95%); cut at 1.5s, 2s, 2.5s, 3s, 5.5s, 6s, 6.5s, 7s, 7.5s, 8s, 8.5s, 9s (out/tracking.json has the numbers; the render-time tracker said 90.2%) |  | clip-46113640647-c5-plain |
| c6-dense | clip #6 | 0-30 dense | FAIL | FAIL render: ValueError: dense cut: no start in 0-29.95s where the shown strip moves and speech follows with 25 s left: skip this moment ({'candidates': 0, 'mid_word': 0, 'shown_strip_frozen': 0, 'silent': 0, 'no_speech': 0, 'valid': 0}) |  | clip-46113640647-c6-dense |
| c6-plain | clip #6 | 0-30 | FAIL | FAIL render: ValueError: no start in 0-29.95s where the shown strip moves and speech or a loud peak follows with 25 s left: skip this moment ({'start_s': None, 'candidates': 50, 'mid_word': 0, 'shown_strip_frozen': 0, 'silent': 50, 'no_speech_or_peak': 0}) |  | clip-46113640647-c6-plain |
| c7-dense | clip #7 | 0-60 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 8 valid starts (window ends 60.0s): skip this moment |  | clip-46113640647-c7-dense |
| c7-plain | clip #7 | 0-60 | FAIL | FAIL render: ValueError: tracking gate failed: subject inside the panel in 69.4% of sampled frames (needs >= 85%); sources {'person': 60.0, 'vehicle': 9.2, 'motion': 27.7, 'none': 3.1}; cut at [0.5, 1.0, 3.0, 4.0, 6.0, 6.5, 7.0, 8.5, 10.5, 11.5, 12.0, 12.5] (out/tracking.json has every sample) |  | clip-46113640647-c7-plain |
| c9-dense | clip #9 | 0-45 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 76% of the speech seconds (need 90%); captions cover only 33% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-c9-dense |
| c9-plain | clip #9 | 0-45 | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 87% of the speech seconds (need 90%); captions cover only 31% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-c9-plain |
| s0-w1 | stream 371-433 s | 371-433 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 2 valid starts (window ends 62.0s): skip this moment |  | clip-46113640647-s0-w1 |
| s0-w2 | stream 398-461 s | 398-461 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 2 valid starts (window ends 63.0s): skip this moment |  | clip-46113640647-s0-w2 |
| s0-w3 | stream 286-350.8 s | 286-350.8 dense | FAIL | FAIL render: ValueError: panel check failed (the grade's own measurement, so this clip would fail the grade): 93.3% inside on 100.0% detected frames of 60 (needs >= 95%); cut at 1s, 10s, 10.5s, 24s (out/tracking.json has the numbers; the render-time tracker said 96.7%) |  | clip-46113640647-s0-w3 |
| s0-w4 | stream 264-327 s | 264-327 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 1 valid starts (window ends 63.0s): skip this moment |  | clip-46113640647-s0-w4 |
| s0-w5 | stream 535-600 s | 535-600 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 4 valid starts (window ends 65.0s): skip this moment |  | clip-46113640647-s0-w5 |
| s1200-w1 | stream 1402-1467.6 s | 202-267.6 dense | FAIL | FAIL cam-bench: CamNotConfident: no confident cam: best candidate [1347, 0, 1920, 1080] (3 of 4 sides have no border and no frame edge to stop at): pass cam_rect \| stable-overlay fallback: no confident cam: best candidate [1347, 0, 1920, 1080] (3 of 4 sides have no stable edge and no frame edge to stop at): pass cam_rect |  | clip-46113640647-s1200-w1 |
| s1200-w2 | stream 1200-1261.1 s | 0-61.1 dense | FAIL | FAIL render: ValueError: dense cut: no other valid start left after the tracking retries: skip this moment |  | clip-46113640647-s1200-w2 |
| s1200-w3 | stream 1472-1537 s | 272-337 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 86% of the speech seconds (need 90%); captions cover only 43% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-s1200-w3 |
| s1200-w4 | stream 1427-1493.3 s | 227-293.3 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 84% of the speech seconds (need 90%); captions cover only 38% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-s1200-w4 |
| s1200-w5 | stream 1527-1590.5 s | 327-390.5 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 2 valid starts (window ends 63.5s): skip this moment |  | clip-46113640647-s1200-w5 |
| s1800-w1 | stream 2121-2182.5 s | 321-382.5 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 1 valid starts (window ends 61.5s): skip this moment |  | clip-46113640647-s1800-w1 |
| s1800-w2 | stream 1938-1999.9 s | 138-199.9 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover only 35% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-s1800-w2 |
| s1800-w3 | stream 2074-2139.5 s | 274-339.5 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover only 38% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-s1800-w3 |
| s1800-w4 | stream 2054-2114.9 s | 254-314.9 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover 89% of the speech seconds (need 90%); captions cover only 27% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-s1800-w4 |
| s1800-w5 | stream 2158-2221 s | 358-421 dense | FAIL | FAIL cam-bench: CamNotConfident: no confident cam: best candidate [0, 0, 1920, 1080] (no straight border found around the face (a face alone is not a cam); 4 of 4 sides have no border and no frame edge to stop at; rectangle covers 100.0% of the frame (a cam is 1.2-42%)): pass cam_rect \| stable-overlay fallback: no confident cam: best candidate [1346, 0, 1920, 1080] (3 of 4 sides have no stable edge and no frame edge to stop at): pass cam_rect |  | clip-46113640647-s1800-w5 |
| s2100-w1 | stream 2121-2183 s | 21-83 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 1 valid starts (window ends 62.0s): skip this moment |  | clip-46113640647-s2100-w1 |
| s2100-w2 | stream 2553-2614.5 s | 453-514.5 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): 1 unresolved disagreement(s): ocr has ['a'] but the speech gap is 0.06 s; captions cover only 67% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-s2100-w2 |
| s2100-w3 | stream 2443-2505.5 s | 343-405.5 dense | FAIL | FAIL cam-bench: CamNotConfident: no confident cam: best candidate [0, 0, 1920, 1080] (no straight border found around the face (a face alone is not a cam); 4 of 4 sides have no border and no frame edge to stop at; rectangle covers 100.0% of the frame (a cam is 1.2-42%)): pass cam_rect \| stable-overlay fallback: no confident cam: best candidate [1347, 0, 1920, 1080] (3 of 4 sides have no stable edge and no frame edge to stop at): pass cam_rect |  | clip-46113640647-s2100-w3 |
| s2100-w4 | stream 2404-2471.5 s | 304-371.5 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 2 valid starts (window ends 67.5s): skip this moment |  | clip-46113640647-s2100-w4 |
| s2100-w5 | stream 2372-2432.3 s | 272-332.3 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 3 valid starts (window ends 60.3s): skip this moment |  | clip-46113640647-s2100-w5 |
| s2400-w1 | stream 2404-2470 s | 4-70 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 3 valid starts (window ends 66.0s): skip this moment |  | clip-46113640647-s2400-w1 |
| s2400-w2 | stream 2734-2797 s | 334-397 dense | FAIL | FAIL render: ValueError: dense cut: no start in 334-397s where the shown strip moves and speech follows with 25 s left: skip this moment ({'candidates': 16, 'mid_word': 13, 'shown_strip_frozen': 0, 'silent': 3, 'no_speech': 0, 'valid': 0}) |  | clip-46113640647-s2400-w2 |
| s2400-w3 | stream 2856-2920.5 s | 456-520.5 dense | FAIL | FAIL render: ValueError: dense cut: no other valid start left after the tracking retries: skip this moment |  | clip-46113640647-s2400-w3 |
| s2400-w4 | stream 2550-2614.5 s | 150-214.5 dense | FAIL | FAIL render: ValueError: cam enhance: in-between frames show double edges with both the optical-flow and the nearest-frame mode (flow: keyframe fidelity 0.9212, in-between mean 0.8398 (min 0.6417); allowed drop 0.03 mean, 0.08 worst; nearest: keyframe fidelity 0.9212, in-between mean 0.9028 (min 0.7553); allowed drop 0.03 mean, 0.08 worst): refusing the clip |  | clip-46113640647-s2400-w4 |
| s2400-w5 | stream 2445-2506 s | 45-106 dense | FAIL | FAIL render: ValueError: dense cut: no other valid start left after the tracking retries: skip this moment |  | clip-46113640647-s2400-w5 |
| s3000-w1 | stream 3463-3525.5 s | 463-525.5 dense | FAIL | FAIL render: ValueError: dense cut: no other valid start left after the tracking retries: skip this moment |  | clip-46113640647-s3000-w1 |
| s3000-w2 | stream 3204-3271.5 s | 204-271.5 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 2 valid starts (window ends 67.5s): skip this moment |  | clip-46113640647-s3000-w2 |
| s3000-w3 | stream 3497-3557.5 s | 497-557.5 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 11 valid starts (window ends 60.5s): skip this moment |  | clip-46113640647-s3000-w3 |
| s3000-w4 | stream 3109-3171.7 s | 109-171.7 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 1 valid starts (window ends 62.7s): skip this moment |  | clip-46113640647-s3000-w4 |
| s3000-w5 | stream 3537-3598 s | 537-598 dense | FAIL | FAIL render: ValueError: dense cut: no start in 537-598s where the shown strip moves and speech follows with 25 s left: skip this moment ({'candidates': 17, 'mid_word': 9, 'shown_strip_frozen': 8, 'silent': 0, 'no_speech': 0, 'valid': 0}) |  | clip-46113640647-s3000-w5 |
| s3172-w1 | stream 3204-3270 s | 32-98 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 1 valid starts (window ends 66.0s): skip this moment |  | clip-46113640647-s3172-w1 |
| s3172-w2 | stream 3468-3528.5 s | 296-356.5 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 1 valid starts (window ends 60.5s): skip this moment |  | clip-46113640647-s3172-w2 |
| s3172-w3 | stream 3497-3557.5 s | 325-385.5 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 10 valid starts (window ends 60.5s): skip this moment |  | clip-46113640647-s3172-w3 |
| s3172-w4 | stream 3609-3675 s | 437-503 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 5 valid starts (window ends 66.0s): skip this moment |  | clip-46113640647-s3172-w4 |
| s3172-w5 | stream 3543-3606.6 s | 371-434.6 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 1 valid starts (window ends 63.6s): skip this moment |  | clip-46113640647-s3172-w5 |
| s600-w1 | stream 948-1011.5 s | 348-411.5 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover only 42% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-s600-w1 |
| s600-w2 | stream 629-691.9 s | 29-91.9 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover only 49% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-s600-w2 |
| s600-w4 | stream 855-921 s | 255-321 dense | FAIL | FAIL render: ValueError: dense cut: no word-end cut point 25-30 s after any of 1 valid starts (window ends 66.0s): skip this moment |  | clip-46113640647-s600-w4 |
| s600-w5 | stream 600-665.1 s | 0-65.1 dense | FAIL | FAIL render: ValueError: captions check failed (out/captions_check.json has every word): captions cover only 55% of the whole clip window with no exclusions (need 75%) |  | clip-46113640647-s600-w5 |
| c8 | clip #8 | - | SKIP | find gave no candidate window |  |  |
| c4-plain | clip #4 | 0-35 | TIMEOUT | FAIL render: stage captions_check timed out after 10 min (budget for this stage) |  | clip-46113640647-c4-plain |
| s600-w3 | stream 652-710 s | 52-110 dense | TIMEOUT | FAIL render: stage captions_check timed out after 10 min (budget for this stage) |  | clip-46113640647-s600-w3 |
