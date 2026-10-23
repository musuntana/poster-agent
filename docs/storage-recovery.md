# Crash-safe local poster persistence

## Scope

The current supported deployment is a single-process local Demo. This increment makes the existing filesystem store more resilient to interrupted writes without introducing another database or storage service.

## Write protocol

- PNG assets, version PNGs, version specifications, and metadata are written to unique sibling temporary files.
- Each temporary file is flushed and \`fsync\`'d before \`os.replace\` atomically publishes it over the target path.
- New posters are assembled under a hidden \`.staging-<32 hex chars>\` directory. The final UUID directory is published by a same-filesystem directory rename only after assets, version 1 PNG/spec, and \`meta.json\` are complete.
- Ordinary exceptions remove the current staging directory. If the process is forcibly killed, the next create operation removes only matching hidden staging directories left by this storage implementation.
- Version updates publish the new PNG and spec before atomically advancing \`meta.json.latest_version\`. A failed write therefore leaves the prior latest version readable. An orphaned unpublished version may remain and is safely replaced on retry.

## Recovery guarantees

The file-level atomic replacement prevents a reader from observing a partially written individual PNG or JSON file. The staging rename prevents a half-created poster from appearing as a normal poster directory. The metadata pointer is the commit marker for version visibility.

This does **not** provide a cross-resource transaction spanning poster files and the SQLite execution ledger. If the process dies after the poster directory is published but before the execution ledger is marked COMPLETED, the execution may remain UNKNOWN even though the poster files exist. Do not automatically retry a paid AI request in that state.

## Boundaries

- \`storage.LOCK\` is process-local; concurrent multi-process writers are unsupported.
- The filesystem and SQLite ledger are not a distributed transaction or production object store.
- This is crash-consistency hardening, not a backup strategy. Keep independent backups of important local artifacts.
- CI/unit tests exercise simulated write failures; they are not a power-loss durability test or production certification.
