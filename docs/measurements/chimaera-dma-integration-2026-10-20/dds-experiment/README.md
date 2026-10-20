# DDS message-limit experiment

`performance.json` and `comparison.csv` contain the six 640×480 performance
cases, with both matched native modes verified. The selection is explicitly
`verify_payload == false` from the retained nine-case matrix.

`history/attempts.json` retains all nine completed attempts. The final large
full-byte diagnostic has zero measured effort publications, so strict matched
export of all nine cases correctly fails. Its empty source cohort is retained;
this diagnostic establishes image integrity, not mixed-stream performance.
The SHM-only experiment did not establish an image-latency improvement.
