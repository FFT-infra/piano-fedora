# Camera source

`piano-camerad.c` originates from `camera/piano-camerad.c` in [debian-piano](https://github.com/blu-sharky/debian-piano). Its imported revision is recorded by `debian-piano-current` in the repository’s `sources.lock.json`. The original MIT copyright and license are retained in `LICENSE-MIT`. The camera changes previously applied by the runtime builder are maintained directly in this directory.

The release runtime builder and the Nix package build this same source through `Makefile`. The camera owns its existing control socket and V4L2 capture path; it is not run alongside another sensor controller.

Local additions on top of the imported revision: the control socket still (`camera_still_request` in `piano-camerad.c`) and its `PIANO_PHOTO_STILL_FRAME` record flag in `photo/protocol.h`. They keep the still capture the package shipped before the import and are the only local changes to the upstream photo protocol; re-apply them when the source is re-imported.
