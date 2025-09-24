# Copyright 2019 The Kubernetes Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# first stage build kindnetd binary
# NOTE: tentatively follow upstream kubernetes go version based on k8s in go.mod
FROM golang:1.25.1 AS build
WORKDIR /go/src/images/kindnetd

# Copy only kindnetd module files for dependency resolution (module lives under images/kindnetd)
COPY images/kindnetd/go.mod images/kindnetd/go.sum ./
RUN go mod download

# Copy the remaining kindnetd sources
COPY images/kindnetd/ ./

# Build static binary
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /go/bin/kindnetd ./cmd/kindnetd

# build real kindnetd image
FROM registry.k8s.io/build-image/distroless-iptables:v0.1.1
COPY --from=build --chown=root:root /go/bin/kindnetd /bin/kindnetd
CMD ["/bin/kindnetd"]
