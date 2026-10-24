# pop-os recovery

Verified at **2026-10-24 06:42:51 UTC / 09:42:51 Athens**.
This follow-up supersedes the earlier snapshot only for worker availability;
the original diagnostic evidence and scores remain intact.

## Changes

- Image, readiness-asset and result streams now use SSH keepalives at 15-second
  intervals with 12 unanswered probes allowed, approximately three minutes.
  Short RPCs retain their five-second/two-probe policy. Cancellation, host-key
  verification and total transfer deadlines remain enforced.
- Source fingerprints now use full Git index hashes. Final checks exposed
  identical edits producing different fingerprints because the controller's
  repository used eight-character automatic abbreviations and the workers used
  seven. The regression covers abbreviation settings 7, 8 and 40.
- The fixes are deployed to the controller and both supervisors. Their running
  images report the same revision and fingerprint. Existing simulation containers
  were preserved through the supervisor/controller replacements.

The observed overnight failure was an SSH liveness timeout on the controller's
producer connection. Its underlying transient network or host stall was not
reproduced. The completed recovery verifies the actual transfer and preparation
path after relaxing the overly short bulk-connection policy.

## Live verification

The exact frozen application image transferred successfully. Docker on pop-os
assigned local ID
`sha256:fcc9de735568fc2a0ed1ff6a4e5fb470b50079baa43f9e400d85a4dffb76632a`;
the existing cross-store verification accepted its matching runnable content.
The sparse guest disk/checkpoint transfer, asset integrity checks, shared
calibration and frozen input validation completed. pop-os preparation is `ready`.

One failed case was retried to verify dispatch: `case-000`, attempt
`fa6af66c0bd444b1906a24406675ae1a`, is running on pop-os with the saved gem5
checkpoint loaded and new effort commands delivered. Its score is pending; this
recovery does not claim to fix perception freshness or qualify a catch.

The original `case-005` attempt completed during recovery, and the original
`case-007` attempt continued running on ubuntu-george. Both machines are online
with no controller machine error. See the [live verification snapshot](recovery.json).

Validation: **209 tests passed** across transport, source/build identity,
scheduler, asset lifecycle, storage lifecycle and agent tests. `git diff --check`
passed. Deployment source fingerprint:
`2eddb5b81c57474c3af2c948e6679b756bdd2bc060d4eb2a9810263ead36210f`.
