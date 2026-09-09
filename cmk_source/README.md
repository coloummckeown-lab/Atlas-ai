# CMK 4.3.1 source mirror

This directory mirrors the key self-development source used by the deployed 4.3.1 package. It is intentionally kept alongside the packaged deployment artifact so Merlin can be moved toward direct file-by-file development while preserving the current Render build path.

Current editable files mirrored here:
- app/config.py
- app/evolution/engine.py
- app/evolution/publisher.py

The live deployment artifact remains `ATLAS_COMPLETE_PACKAGE.zip` until the Render build path is migrated to the source tree.
