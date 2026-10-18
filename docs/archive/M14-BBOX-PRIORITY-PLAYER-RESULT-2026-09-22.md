# Bounding-box priority: player test accepted, 2026-09-22

Tested revision: `9eb8ab83b906`; log: `launch(20260922-184816).log`.
Player: "looks fine, a little jitter, no flip stall".

| Measurement | Previous baseline capture | Priority capture |
| --- | --- | --- |
| Median completed game FPS | 60.0 | 60.0 |
| Producer timing windows | 103 | 59 |
| Longest display-processing call | 6,763.930 ms | 108.947 ms |
| Sampled bbox map waits | 13.830–20.692 ms | First 19.093 ms; subsequent 0.071–0.459 ms |

Previous log: `launch(20260922-143225).log`. These are separate player runs,
not a controlled frame-for-frame benchmark. Readback logging is sparse;
ranges above are observed samples, not percentiles or all-query maxima.

The priority marker is present. The new capture reaches at least 1,024 GPU
bbox maps with fresh results and 3,069 register-cache hits. The host presenter
reports 3,529 source publications and 5,947 presents. Replay preparation is off.

Accept bbox priority as the player-tested baseline improvement. The multi-second
flip stall is resolved in this test. Do not claim all hitches or jitter are gone:
display calls still reach about 109 ms, and the player reports residual jitter.
The dev140a preparation performance gate remains failed; this change does not
make interpolated output ready. Next focus remains reducing preparation cost
before advancing to dev140b, while keeping this bbox priority behavior intact.
