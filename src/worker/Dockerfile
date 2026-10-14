# Worker image: the pinned conversion stack. Every node runs jobs in this
# same image, so both execution modes use identical converter versions.
#
#   docker build -f src/worker/Dockerfile -t cstore-worker:demo2 .     (from the repo root)
#
# Python 3.12 from Ubuntu 24.04 (the hosts' OS); CPU-only PyTorch (the
# conversion never touches a GPU, and the CPU wheels keep the image ~7 GB smaller).
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
# Where torch/torchvision come from. The default is PyTorch's CPU-only index;
# TORCH_LOCAL_VERSION is the matching local version label. If that index is
# unreachable, build with the (CUDA-bundling, ~7 GB larger) PyPI wheels instead:
#   DOCKER_BUILD_ARGS="--build-arg TORCH_INDEX_URL=https://pypi.org/simple --build-arg TORCH_LOCAL_VERSION="
# (the setup scripts and experiments/00 pass $DOCKER_BUILD_ARGS to docker build).
# Both nodes always run the same image, so either choice keeps the comparison fair.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_LOCAL_VERSION=+cpu

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3.12 python3.12-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --index-url "$TORCH_INDEX_URL" \
        "torch==2.11.0${TORCH_LOCAL_VERSION}" "torchvision==0.26.0${TORCH_LOCAL_VERSION}"
COPY src/worker/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt \
 && python -c "import lerobot, tensorflow_datasets, torch; print('torch', torch.__version__)"

COPY src /opt/cstore/src
ENV PYTHONPATH=/opt/cstore/src \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TF_CPP_MIN_LOG_LEVEL=3
WORKDIR /opt/cstore
CMD ["python", "-m", "cstore.worker", "--help"]
