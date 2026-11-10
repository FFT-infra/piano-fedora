#!/bin/bash
# TuneD runs this with "start" when the profile is loaded and "stop" when it is
# unloaded. The mode has to survive a switch, so "stop" does nothing and the
# next profile sets its own. The real work is in /usr/lib/piano/piano-power-mode
# because uperf-linux owns the frequency knobs on this image.
case "${1:-}" in
    start) exec /usr/lib/piano/piano-power-mode balance ;;
    *) exit 0 ;;
esac
