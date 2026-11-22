# Static glibc binary in an empty image: no interpreter, nothing to start but the pipeline.
# The binary is built with profile-guided optimisation trained on data from the organisers' generator (tools/gen).
FROM rust:1.100-bookworm AS build
RUN rustup component add llvm-tools-preview || true
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY tools/gen /gen
COPY pgo.sh /pgo.sh
RUN sh /pgo.sh

FROM scratch
COPY --from=build /shop /shop
USER 1000:1000
ENTRYPOINT ["/shop"]
