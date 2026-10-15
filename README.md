# Phigros Chart MCP

Standalone Python 3.11+ MCP server for repeatable structured chart authoring. Any MCP client can create/import, inspect bounded passages, plan, validate, atomically apply and export official Phigros v3 charts. PhiChain and the study corpus are optional. The agent interprets intent and supplies explicit musical evidence and operations; no arbitrary-speech parser is implied. Optional cached audio measurements corroborate timing without identifying instruments.

Install in an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/phigros-chart-mcp
```

Configure any client supporting local stdio MCP servers. Use absolute paths:

```json
{
  "mcpServers": {
    "phigros-chart": {
      "command": "/Users/jesse/Projects/phigros-chart-mcp/.venv/bin/python",
      "args": ["-m", "phigros_chart_mcp.server"]
    }
  }
}
```

For Codex, the CLI configuration is separate from the generic JSON example above:

```sh
codex mcp add phigros-chart -- /Users/jesse/Projects/phigros-chart-mcp/.venv/bin/python -m phigros_chart_mcp.server
```

Read `phigros-authoring://guide/v1` once, then retrieve only relevant preset/reference categories. `phigros-authoring://schemas/v1` supplies strict executable tool and discriminated operation schemas; tools also expose schemas/descriptions through normal MCP discovery. `operations/v1`, `recipes/v1` and `references/v1` are compact bundled resources. `authoring_reference(index)` lists 12 bounded lesson/case cards; individual cards retain exact verified windows, roles, transfer recipes, caveats and provenance without importing the corpus. The optional full copyrighted chart/audio corpus is not distributed or required. `examples/sdk_client.py` demonstrates the official SDK protocol.

Default design has one main **playing anchor**, note carriers that converge onto it (possibly one note each), and empty decorative lines. An engine line is not automatically another gameplay axis. Hidden carrier opacity does not itself hide notes; preview engine visibility. Holds require stable contact ownership through their tails. Boxes, inversion and opposing approaches are deliberate special modes, not routine defaults.

A cheap phrase workflow:

1. `chart_create` or `chart_import`; retain its exact digest. Resolve “faster” into beat density, approach speed or line motion before authoring.
2. Use `evidence_from_chart(chart_ref,digest,start,end)` to derive coordinate-free articulation and carry-in hold evidence from any imported chart. Exact global tempo/per-line source BPM/offset are handled by the backend. Import precomputed local phrase records by path with `evidence_import`, or register bounded caller-measured phrase evidence with `evidence_register`; identical source/settings/content returns a cache hit. Read selected fields through `evidence_get`. Chart-only extraction has no audio dependency. Optional `audio_analyze` measures cached register flux/RMS; pass its compact audio_ref to evidence_from_chart for explicit-lattice timing corroboration. These measurements are not instrument recognition or automatic musical truth.
3. Use `authoring_presets` for regular/irregular patterns, or `authoring_plan_phrase` with an evidence reference and compact motif config. The latter expands ownership, sustains, connected threads and attack/release rules server-side; `finger_order` is a priority list, while `allowed_fingers` is a hard label allowance for a two-finger recipe; returns a small receipt, not every generated note.
4. `authoring_plan` also supports explicit notes/patterns, stable-ID repair/delete/move, reversible phrase transforms and line effects. Inspect findings and counts, then `authoring_apply`. Planning changes no current chart; apply creates one atomic offline revision. Each transaction changes at most 1000 note objects. `section` is persisted metadata, not implicit operation scope.
5. `chart_read` a small interval and line with bounded notes; overlapping holds are included. Event reads state matching counts and explicit truncation. `authoring_diagnostics` pages warnings; `authoring_contacts` audits declared finger hypotheses, grouped drag contacts, rapid repeats and target travel with bounded witnesses. Checkpoint with `chart_export` into a new filename, then preview the phrase.

Restart by importing the `.authoring.json` sidecar, which verifies the model digest and preserves roles, phrase recipes and referenced cached evidence. Official JSON alone preserves scoring/presentation fields but cannot carry all design intent. `chart_revision_restore` is in-process undo; exported sidecars are durable checkpoints. Stale plans must be regenerated from `chart_context`, not reused with an invented fresh digest. No files are overwritten. Export reports a partial write if the second sidecar write fails.

Timing uses exact rational beat strings or `[whole,numerator,denominator]`, with holds measured in beats and explicit tempo maps. Offset is milliseconds; model timestamps are exact chart-relative seconds. Logical centered x/y, official `positionX=18*x`, native `x=1350*x`, native `y=900*y` are adapter conventions requiring actual rendered geometry verification. Rotation is degrees; opacity is 0..1, converted to native 0..255. Official scroll speed converts to native by ×4.5. Note speed is a multiplier; official hold encoding also multiplies onset scroll speed.

Official v3 import preserves unknown fields and untouched source lines/records. Instantaneous and reversed source events and unusual imported speeds are preserved loss-aware; reversed tracks are quarantined and related edits/native conversion explicitly refuse incompatible cases. Ambiguous overlapping tracks are preserved for no-op official export, but regenerated ambiguous tracks refuse. Native format 6 export does not require PhiChain, but beat precision, unusual source speeds and overlap compatibility are checked. Version 1 import is not supported. No-op export is semantic JSON preservation, not original whitespace/byte preservation.

Checks separate simultaneous objects, fresh strikes and raw sustained objects: none equals verified fingers. Study examples show Rrhar'il IN and AT both have 1300 notes while gesture vocabulary changes; Distorted Fate AT has fewer objects than IN. No four-type quota is imposed. Logical geometry checks and event-boundary hold sampling are useful repair signals, not complete approach simulation or official-quality proof.

For optional previews, `examples/render_preview.py` wraps a configured renderer without depending on PhiChain in the server. Prepare a renderer project containing native `chart.json`, `meta.json` and its music file; export a native checkpoint through MCP first. Example executable used by this study:

```sh
.venv/bin/python examples/render_preview.py \
  --renderer /Users/jesse/Projects/PhiChain/local/PhiChain.app/Contents/MacOS/phichain-renderer \
  --project /absolute/renderer-project --output /absolute/new-preview.mp4 \
  --from-seconds 20 --to-seconds 32 --width 960 --height 540 --fps 30
```

This invokes `PROJECT_DIR -o NEW.mp4 --from 20 --to 32 --width 960 --height 540 --fps 30 --no-telemetry`. Preview range is capped at 32 seconds and outputs cannot exist. Actual music/project setup and visual/playability assessment remain external; the adapter reports paths/settings, never claims to judge quality.

Run `.venv/bin/python -m unittest discover -s tests`. Tests include real SDK stdio discovery, schemas, all four note types, transactional/stale behavior, bounded context, checkpoint recovery, source preservation and independent timing/geometry goldens. Future cheap-model intent tasks are in [evaluation/tasks.json](evaluation/tasks.json); GPT-6 Luna quality has **not** been evaluated. Record tool calls, context bytes, CPU/render cost and musical/ownership acceptance separately. Revision diagnostics are cached; compiled event channels make first-pass geometry logarithmic in event count. Measured real Rrhar'il IN single-note plan improved from minutes to 0.86 seconds on this workstation; that observation is not a timing guarantee.

Optional measured-audio setup:

```sh
.venv/bin/python -m pip install -e '.[analysis]'
# Install ffmpeg with your system package manager.
```

`audio_analyze(source_path, settings)` reads a local file, caches source-hash/settings/decoder/NumPy/producer measurements on disk and returns a compact reference. No feature arrays pass through model context. Bounds are 256MiB, 600 seconds; FFT work is blocked. `evidence_from_chart(..., audio_ref=...)` maps its exact beat lattice to AUDIO-relative times with offset, then matches measured register peaks. A match is supporting evidence, not a command to fill every beat. Source carry-in holds remain separate from new heads; inspect them rather than invent a phrase-boundary onset. Rational mirror fields drive exact timing through compact motif compilation.

`examples/derive_phrase.py` is an any-chart SDK workflow. It accepts source/audio paths, exact beat bounds and a compact motif JSON config; it does not hardcode song names, BPM or official raw-time divisions. Example:

```sh
.venv/bin/python examples/derive_phrase.py --source /absolute/source-chart.json   --start 32 --end 40 --config /absolute/motif-config.json   --output /absolute/new-study-chart.json
```

This is a source-articulation study draft, not a finished automatic song chart. Use appropriate licensed/local musical material and intentional phrase decisions; preserve rests, review contact ownership and add presentation separately.
