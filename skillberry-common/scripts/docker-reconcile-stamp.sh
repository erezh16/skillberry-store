#!/usr/bin/env bash
#
# docker-reconcile-stamp.sh <docker-cmd> <full-image-name> <image-tag> <dbt>
#
# Enforces concept 3 of docs/design/build_concepts.md: a Docker build/get stamp
# is only valid if a Docker image with the current primary tag actually exists.
#
# For DBT=local: if the get/build stamps for the current image tag exist but
# the local image is gone (e.g., `docker rmi` was run), delete those stamps so
# the next `make` invocation rebuilds. The tag is the stamp key because it is
# what identifies the artifact in every tagging scheme -- it embeds the git
# label plus IMAGE_TAG_SUFFIX by default, and is CUSTOM_TAG when that is set.
#
# For DBT=registry: no reliable local check is possible without hitting the
# registry; do nothing. Concept 3 accepts push presence as satisfying the
# invariant for registry builds.
#
# Called at make parse time via $(shell ...); must be silent on the happy
# path and produce no stdout.

set -eu

if [ "$#" -lt 4 ]; then
    exit 0
fi

docker_cmd="$1"
full_image_name="$2"
image_tag="$3"
dbt="$4"

# Only reconcile local builds; skip if the tag is empty or the docker command
# is missing.
if [ "$dbt" != "local" ] || [ -z "$image_tag" ]; then
    exit 0
fi

if ! command -v "$docker_cmd" > /dev/null 2>&1; then
    exit 0
fi

stamp_dir=".stamps"
get_stamp="${stamp_dir}/docker-get-${image_tag}"
build_stamp="${stamp_dir}/docker-build-local-${image_tag}"

# Only bother running docker if there's a stamp to potentially invalidate.
if [ ! -f "$get_stamp" ] && [ ! -f "$build_stamp" ]; then
    exit 0
fi

if "$docker_cmd" image inspect "${full_image_name}:${image_tag}" > /dev/null 2>&1; then
    # Image present at the current tag; stamps are valid.
    exit 0
fi

# Image is gone. Invalidate the tag-scoped stamps so Make rebuilds.
rm -f -- "$get_stamp" "$build_stamp"
exit 0
