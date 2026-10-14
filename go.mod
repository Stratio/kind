module sigs.k8s.io/kind

go 1.26.0

require (
	github.com/Azure/azure-sdk-for-go/sdk/azcore v1.23.2
	github.com/Azure/azure-sdk-for-go/sdk/azidentity v1.13.1
	github.com/BurntSushi/toml v1.6.0
	github.com/apparentlymart/go-cidr v1.1.1
	github.com/aws/aws-sdk-go-v2/config v1.33.6
	github.com/aws/aws-sdk-go-v2/credentials v1.20.6
	github.com/aws/aws-sdk-go-v2/service/ecr v1.66.1
	github.com/evanphx/json-patch/v5 v5.9.11
	github.com/fatih/structs v1.1.0
	github.com/go-playground/validator/v10 v10.30.5
	github.com/google/safetext v0.0.0-20220905092116-b49f7bc46da2
	github.com/mattn/go-isatty v0.0.24
	github.com/oleiade/reflections v1.1.0
	github.com/pelletier/go-toml v1.9.5
	github.com/pkg/errors v0.9.1
	github.com/sosedoff/ansible-vault-go v0.2.0
	github.com/spf13/cobra v1.10.2
	github.com/spf13/pflag v1.0.9
	golang.org/x/crypto v0.57.0 // indirect
	golang.org/x/term v0.46.0
	google.golang.org/api v0.299.0
	gopkg.in/yaml.v3 v3.0.1
	sigs.k8s.io/yaml v1.5.0
)

require (
	al.essio.dev/pkg/shellescape v1.6.1
	github.com/Azure/azure-sdk-for-go/sdk/resourcemanager/compute/armcompute/v5 v5.7.0
	github.com/Azure/azure-sdk-for-go/sdk/resourcemanager/containerservice/armcontainerservice/v3 v3.0.0
	github.com/Azure/azure-sdk-for-go/sdk/resourcemanager/msi/armmsi v1.3.0
	github.com/aws/aws-sdk-go-v2 v1.47.1
	github.com/aws/aws-sdk-go-v2/service/ec2 v1.337.0
	golang.org/x/exp v0.0.0-20250911091902-df9299821621
	golang.org/x/oauth2 v0.37.0
)

require (
	cloud.google.com/go/auth v0.23.3 // indirect
	cloud.google.com/go/auth/oauth2adapt v0.3.0 // indirect
	github.com/aws/aws-sdk-go-v2/internal/v4a v1.5.4 // indirect
	github.com/aws/aws-sdk-go-v2/service/internal/accept-encoding v1.13.19 // indirect
	github.com/aws/aws-sdk-go-v2/service/signin v1.10.1 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/containers/libtrust v0.0.0-20230121012942-c1716e8a8d01 // indirect
	github.com/containers/ocicrypt v1.3.2 // indirect
	github.com/containers/storage v1.59.1 // indirect
	github.com/distribution/reference v0.6.0 // indirect
	github.com/docker/distribution v2.8.3+incompatible // indirect
	github.com/docker/docker v28.3.2+incompatible // indirect
	github.com/docker/docker-credential-helpers v0.9.9 // indirect
	github.com/docker/go-connections v0.8.1 // indirect
	github.com/docker/go-units v0.5.0 // indirect
	github.com/felixge/httpsnoop v1.1.0 // indirect
	github.com/gabriel-vasile/mimetype v1.4.15 // indirect
	github.com/go-logr/logr v1.4.4 // indirect
	github.com/go-logr/stdr v1.2.2 // indirect
	github.com/golang-jwt/jwt/v5 v5.3.0 // indirect
	github.com/google/s2a-go v0.1.10 // indirect
	github.com/gorilla/mux v1.8.1 // indirect
	github.com/hashicorp/errwrap v1.1.0 // indirect
	github.com/hashicorp/go-multierror v1.1.1 // indirect
	github.com/kr/text v0.2.0 // indirect
	github.com/moby/sys/capability v0.4.0 // indirect
	github.com/moby/sys/mountinfo v0.7.2 // indirect
	github.com/moby/sys/user v0.4.0 // indirect
	github.com/opencontainers/go-digest v1.0.0 // indirect
	github.com/opencontainers/image-spec v1.1.1 // indirect
	github.com/opencontainers/runtime-spec v1.2.1 // indirect
	github.com/sirupsen/logrus v1.9.4 // indirect
	github.com/ulikunitz/xz v0.5.17 // indirect
	go.opentelemetry.io/auto/sdk v1.2.1 // indirect
	go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp v0.69.0 // indirect
	go.opentelemetry.io/otel v1.46.0 // indirect
	go.opentelemetry.io/otel/metric v1.46.0 // indirect
	go.opentelemetry.io/otel/trace v1.46.0 // indirect
	go.yaml.in/yaml/v2 v2.4.2 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260921155816-b14227669459 // indirect
)

require (
	cloud.google.com/go/compute/metadata v0.9.1 // indirect
	github.com/Azure/azure-sdk-for-go/sdk/internal v1.12.0 // indirect
	github.com/Azure/azure-sdk-for-go/sdk/resourcemanager/network/armnetwork/v4 v4.3.0
	github.com/Azure/azure-sdk-for-go/sdk/resourcemanager/resources/armsubscriptions v1.3.0
	github.com/AzureAD/microsoft-authentication-library-for-go v1.6.0 // indirect
	github.com/aws/aws-sdk-go-v2/feature/ec2/imds v1.20.1 // indirect
	github.com/aws/aws-sdk-go-v2/internal/configsources v1.5.4 // indirect
	github.com/aws/aws-sdk-go-v2/internal/endpoints/v2 v2.8.4 // indirect
	github.com/aws/aws-sdk-go-v2/service/internal/presigned-url v1.14.4 // indirect
	github.com/aws/aws-sdk-go-v2/service/sso v1.38.1 // indirect
	github.com/aws/aws-sdk-go-v2/service/ssooidc v1.43.1 // indirect
	github.com/aws/aws-sdk-go-v2/service/sts v1.51.1
	github.com/aws/smithy-go v1.28.1 // indirect
	github.com/containers/common v0.64.2
	github.com/containers/image/v5 v5.36.2
	github.com/go-playground/locales v0.14.1 // indirect
	github.com/go-playground/universal-translator v0.18.1 // indirect; indirect=
	github.com/google/uuid v1.6.0 // indirect
	github.com/googleapis/enterprise-certificate-proxy v0.3.22 // indirect
	github.com/googleapis/gax-go/v2 v2.24.1 // indirect
	github.com/inconshreveable/mousetrap v1.1.0 // indirect
	github.com/kylelemons/godebug v1.1.0 // indirect
	github.com/leodido/go-urn v1.5.0 // indirect
	github.com/pkg/browser v0.0.0-20240102092130-5ac0b6a4141c // indirect
	golang.org/x/net v0.59.0 // indirect
	golang.org/x/sys v0.48.0 // indirect
	golang.org/x/text v0.42.0 // indirect
	google.golang.org/grpc v1.84.0 // indirect
	google.golang.org/protobuf v1.36.12 // indirect
)
