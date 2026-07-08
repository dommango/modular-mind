# Cloud-audition batch worker (see CLAUDE.md "Cloud audition — known
# corpus"). Same Rack + source-built VCV-Recorder recipe as
# render-service/Dockerfile, but its CMD runs corpus_audition.py over a
# baked-in set of patches instead of serving HTTP.
#
# This SMOKE image bakes 5 real corpus patches (corpus-worker/smoke-data/)
# to validate the lin-x64 render + in-cloud plugin download + analyze path
# end-to-end on Railway before committing to the full ~2,544-patch run.
# The full run swaps the baked data for a mounted volume of data/raw/.
#
# Build context is the repo root (needs config.py, render_patch.py, etc.).

FROM --platform=linux/amd64 ubuntu:24.04 AS plugin-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git wget curl unzip zip autoconf automake \
        libtool pkg-config nasm cmake jq zstd python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN curl -sL -o sdk.zip https://vcvrack.com/downloads/Rack-SDK-2.6.6-lin-x64.zip \
    && unzip -q sdk.zip \
    && rm sdk.zip
ENV RACK_DIR=/opt/Rack-SDK

# VCV-Recorder isn't downloadable prebuilt without a login; build it from
# the SDK (vendors lame + libopus + ffmpeg). Slow layer (~15-25 min),
# cached on RECORDER_COMMIT.
ARG RECORDER_COMMIT=defcf7890bc9630e288ca7cfcc4dd998eb314ccf
RUN git clone https://github.com/VCVRack/VCV-Recorder.git /opt/VCV-Recorder \
    && cd /opt/VCV-Recorder \
    && git checkout ${RECORDER_COMMIT} \
    && git submodule update --init --recursive

WORKDIR /opt/VCV-Recorder
RUN make dep && make dist


FROM ubuntu:24.04 AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libx11-6 libxext6 libxrandr2 libxinerama1 \
        libxcursor1 libxi6 libasound2t64 libjack-jackd2-0 libpulse0 \
        libzstd1 python3 python3-venv curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN curl -sL -o rack.zip https://vcvrack.com/downloads/RackFree-2.6.6-lin-x64.zip \
    && unzip -q rack.zip \
    && rm rack.zip

# Rack's RPATH is the literal "." — libRack.so only resolves from its own
# directory, so assert ldd from there.
RUN cd /opt/Rack2Free && \
    if ldd ./Rack | grep -q 'not found'; then \
        echo 'MISSING SHARED LIBS for Rack:'; ldd ./Rack | grep 'not found'; exit 1; \
    fi && \
    if ldd ./libRack.so | grep -q 'not found'; then \
        echo 'MISSING SHARED LIBS for libRack.so:'; ldd ./libRack.so | grep 'not found'; exit 1; \
    fi

# First-run bootstrap extracts bundled Fundamental into plugins-lin-x64/;
# delete the log.txt it leaves so the next launch doesn't see a truncated
# log and pop a blocking crash dialog.
RUN mkdir -p /rack-userdir && \
    cd /opt/Rack2Free && \
    (timeout 30 ./Rack -h -u /rack-userdir < /dev/null || true) && \
    rm -f /rack-userdir/log.txt && \
    ls -la /rack-userdir/plugins-lin-x64/Fundamental

RUN echo '{"autoCheckUpdates": false}' > /rack-userdir/settings.json

COPY --from=plugin-builder /opt/VCV-Recorder/dist/VCV-Recorder /rack-userdir/plugins-lin-x64/VCV-Recorder

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

RUN pip install --no-cache-dir 'numpy>=1.24' 'soundfile>=0.12' 'requests>=2.31.0'

COPY config.py 03_parse_and_filter.py render_patch.py /app/
COPY analyze_audio.py plugin_sync.py corpus_audition.py /app/

# Baked smoke corpus: 5 real patches + a mini filtered_patches.json. The
# full run replaces this with a Railway volume mounted at /app/data.
COPY corpus-worker/smoke-data/raw /app/data/raw
COPY corpus-worker/smoke-data/output /app/data/output

ENV RACK_BINARY=/opt/Rack2Free/Rack
ENV RACK_HEADLESS_DIR=/rack-userdir

WORKDIR /app
CMD ["python3", "corpus_audition.py"]
