# The Rust planner is built against this image's glibc with the official Rust 1.85.0
# components in deps/rust (checked against infra/rust-toolchain.sha256).
FROM quay.io/ceph/ceph@sha256:09ee90f6f3e0c7b9954f71d214ee05e9bbaaaea3716b1dd619603283b829f8b8 AS native
USER root
RUN dnf install -y xz && dnf clean all
COPY deps/rust /tmp/rust
COPY infra/rust-toolchain.sha256 /tmp/rust/
RUN cd /tmp/rust && sha256sum -c rust-toolchain.sha256 \
    && for component in rustc rust-std cargo; do \
        tar -xJf "$component-1.85.0-x86_64-unknown-linux-gnu.tar.xz" \
        && "./$component-1.85.0-x86_64-unknown-linux-gnu/install.sh" --prefix=/opt/rust \
            --disable-ldconfig > /dev/null || exit 1; \
    done
COPY native /src/native
RUN PATH=/opt/rust/bin:$PATH cargo build --release --offline --manifest-path /src/native/Cargo.toml

FROM quay.io/ceph/ceph@sha256:09ee90f6f3e0c7b9954f71d214ee05e9bbaaaea3716b1dd619603283b829f8b8
USER root
RUN dnf install -y iproute iproute-tc iperf3 python3-numpy && dnf clean all \
    && command -v tc && command -v iperf3 && python3 -c 'import rados, numpy'
WORKDIR /app
RUN mkdir -p /app/results
COPY cephdag /app/cephdag
COPY experiments /app/experiments
COPY deps/wheels /app/wheels
# The DROID lock is optional; the [k] pattern lets COPY succeed when it is absent.
COPY infra/requirements-linux-amd64.lock infra/requirements-droid-linux-amd64.loc[k] /app/locks/
RUN locks="-r /app/locks/requirements-linux-amd64.lock"; \
    if [ -f /app/locks/requirements-droid-linux-amd64.lock ]; then \
        locks="$locks -r /app/locks/requirements-droid-linux-amd64.lock"; \
    fi; \
    PYTHONPATH=/app/wheels/pip-25.2-py3-none-any.whl python3 -m pip install \
    --no-index --no-cache-dir --require-hashes --find-links /app/wheels --target /app/vendor $locks
COPY --from=native /src/native/target/release/libcephdag_native.so /app/native/target/release/libcephdag_native.so
COPY infra/entrypoint.sh /app/entrypoint.sh
COPY infra/network.py /app/network.py
ENV PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=/app/vendor:/app
ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]
