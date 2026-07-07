# Rack render image for the audio listening loop (see CLAUDE.md's "Audio
# Listening Loop" section). Runs official VCV Rack Free 2.6.6 lin-x64
# headless, driven the same way as the local Windows/WSL-interop path
# (render_patch.py), so this image reuses that module unchanged.
#
# One image, two Railway services selected by start command:
#   uvicorn app:app          -> on-demand render service (default CMD)
#   python3 corpus_audition  -> batch worker over the known corpus
#                               (mount a volume at /app/data)

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
# the SDK. Its Makefile vendors lame 3.100 + libopus 1.5.2 + a pinned
# ffmpeg submodule — this is the slow layer (~15-25 min), cached as long
# as RECORDER_COMMIT doesn't change.
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

# Rack's RPATH is the literal string "." (not $ORIGIN) — it only finds
# libRack.so when its cwd is its own directory. Assert from there, or a
# clean ldd read from elsewhere reports a false "not found".
RUN cd /opt/Rack2Free && \
    if ldd ./Rack | grep -q 'not found'; then \
        echo 'MISSING SHARED LIBS for Rack:'; ldd ./Rack | grep 'not found'; exit 1; \
    fi && \
    if ldd ./libRack.so | grep -q 'not found'; then \
        echo 'MISSING SHARED LIBS for libRack.so:'; ldd ./libRack.so | grep 'not found'; exit 1; \
    fi

# First-run bootstrap: headless Rack extracts its own bundled Fundamental
# .vcvplugin into <userdir>/plugins-lin-x64/ on first launch. The log.txt
# this run leaves behind must be deleted — an unclean-looking previous
# exit makes logger::wasTruncated() pop a blocking "Rack crashed" dialog
# before loading the next patch, even headless.
RUN mkdir -p /rack-userdir && \
    cd /opt/Rack2Free && \
    (timeout 30 ./Rack -h -u /rack-userdir < /dev/null || true) && \
    rm -f /rack-userdir/log.txt && \
    ls -la /rack-userdir/plugins-lin-x64/Fundamental

RUN echo '{"autoCheckUpdates": false}' > /rack-userdir/settings.json

COPY --from=plugin-builder /opt/VCV-Recorder/dist/VCV-Recorder /rack-userdir/plugins-lin-x64/VCV-Recorder

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY render-service/requirements.txt /app/render-service-requirements.txt
# numpy + soundfile: the corpus worker's analyze_audio step (the render
# service itself needs only fastapi/uvicorn, but sharing one image is
# cheaper than a second 20-min VCV-Recorder build).
RUN pip install --no-cache-dir -r /app/render-service-requirements.txt \
        'numpy>=1.24' 'soundfile>=0.12' 'requests>=2.31.0'

COPY config.py 03_parse_and_filter.py render_patch.py /app/
COPY render-service/app.py render-service/validation.py /app/
COPY analyze_audio.py plugin_sync.py corpus_audition.py /app/

ENV RACK_BINARY=/opt/Rack2Free/Rack
ENV RACK_HEADLESS_DIR=/rack-userdir

WORKDIR /app
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
