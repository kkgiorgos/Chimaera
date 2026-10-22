# Legacy pair selection audit

The later READY audit adds `initialized_m5_ready` as a distinct identity rather
than changing any final pair pins. Historical candidate-v2 timing/KVM Ball runs
selected bundle `ea049668…`, app and executed guest `fd32c7e1…`, driver
`a507655c…`. Static disassembly shows a page-boundary write loop before receive.
The unpacked `source-v2` has the same helper; both retained v2 source archives
omit it. `.chimaera-m5/initialized-m5-ready/source-provenance.json` therefore
pins a newly retained source snapshot with exact binary/source correspondence
explicitly **UNVERIFIED**. Binary identity is independently established.

The final `initialized_m5` identity, archive pins, rejected-ready check and raw
failures remain unchanged. READY enters the eight-variant main cohort subject
to parent qualification; final remains separately selectable diagnostic evidence.
The exact prior catalog hash is accepted for the original three labels only
when their embedded selected entry matches current pins. READY is current-only.
The historical audit below describes the earlier final-only selection; its
then-closed catching gate and pending-test statements are historical context.

This follow-up changes source and metadata under `profiling/chimaera_m5/` only.
The actual KVM catching gate remains closed. No tests, staging, freezing, matrix
generation, probe, build, benchmark or runtime was executed for this follow-up.
Asset SHA256 reads, archive listings/member reads and source inspection establish
the identities below. They do not establish runtime compatibility or throughput.
This supersedes the earlier handoff's pending-adapter notes for the two rewrites.

`legacy-identities.json` contains the complete expected hashes. Each selection
must opt into its own `legacy_implementation`; original's existing
`legacy_original_adapter:true` and CLI `--legacy-original` remain restricted to
`original_m5`. Identical legacy JSON/startup bytes are not treated as evidence
that different binary pairs share a wire protocol. Each pair gets its own
transport identity, while original transport metadata remains byte-for-byte.

* Original: `.chimaera-m5/original/runtime` and `guest.tar.gz` recovered from
  image `642cd426444f…`. The original endpoint/driver/clock/bundle match all four
  historical original raw hashes. Driver `07e44385…`, both bridges `4a89aa84…`,
  bundle `f0df2606…`. Source provenance is the recovered image build manifest,
  SHA256 `e21d460e…`; exact source revision remains unknown.
* Streamed: `.chimaera-rewrite/20261020/runtime` matches `guest.tar.gz`:
  driver `dbd2e60b…`, both bridges `70c6e782…`, bundle `593cc9df…`.
  Retained source archive `source.tar.gz` is `13ecdae6…`. The driver, application
  endpoint and bundle also match the streamed pilot's raw `run.json`.
* Final initialized: use the **complete installed prefix**
  `.chimaera-rewrite/20261020/guest-v2-final/opt/chimaera/latency/app`
  with `guest-v2-final.tar.gz`. Driver `a507655c…`, app and executed bridges
  `cb0671f5…`, bundle `cf113e53…`. Source archive `source-v2-final.tar.gz` is
  `cd2d87c6…`. The final prefix's host/gazebo bridge binaries also match the
  retained host prefix, without substituting executables.

The mutable `runtime-v2` prefix **does not match the final guest bridge**:
it contains `fd32c7e1…`, also found in `guest-v2-ready.tar.gz` (`ea049668…`).
Its driver matches final, so a driver-only check would miss this mixed pair.
The final adapter rejects that guest bridge. The historical `candidate-v2`
pilot used the ready bundle; those historical observations must remain separate
from a newly measured final initialized pair. Nothing is silently restored or
copied into `runtime-v2`; the parent stages the matching complete final prefix
into a new output directory after the catching gate.

For each legacy pair, the declarative Python bootstrap accepts current optional
manifest fields but sends the preserved guest bridge only its old supported
parameters. All three retained runner sources have the same supported guest
parameter set. Both rewrite source archives retain the old strict route schema:
inactive `coalesce_group` metadata is removed when coalescing is off; latest
stereo grouping is retained. No explicit DDS lane field is supplied to an old
bridge. Application sensor messages and the original bridge/controller binaries
continue to provide the payload path.

The catalog pins the common application SDK image
`sha256:c841ebb1000488c0010e21bc32a62b9d432b1a47e47f64e16501dc53bd66c8d4`.
Staging records it; freezing requires the suite's declared SDK to match it and
hashes the extended endpoint, ABI libraries, declarative bootstrap and catalog.
This is the SDK for newly shared application assets, not an invented historical
compiler identity. Original's recovered image ID is known. Exact build-image
IDs for the streamed/final legacy binaries are not independently recorded in
their pilot case metadata; the source archives, immutable bundle hashes and
distinct retained executable hashes remain explicit instead.

The parent should execute the three staging commands and KVM qualification
matrix in `README.md` only after the actual catching pass. Each adapter is
prepared to admit its identified pair without rewriting a marker to protocol3;
successful source/identity checks do not claim the qualification passed.
