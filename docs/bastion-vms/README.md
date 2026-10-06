# Bastion / deployment VMs per provider

Remote VMs used to run `cloud-provisioner create cluster` (and other kind bootstraps) instead of the laptop.

Scripts live in [`scripts/`](scripts/): `eks-vm.sh` and `azure-vm.sh` run on the laptop; `vm-autostop.sh`, `bastion-tools.sh` and `tool-versions.*` are pushed to the VMs by them. Laptop commands below run from the repository root. Set `VM_OWNER` (default `$USER`) before `create` to choose the `owner` tag and the name of the first SSH rule.

**Why**: each kind bootstrap peaks at ~2.5 GB RAM; two or three in parallel on a 16 GB laptop have triggered `systemd-oomd` and killed the desktop session together with every running `create cluster`. On a VM the bootstrap is isolated and survives laptop reboots/VPN drops.

| Provider | VM | Has to be in the cluster network? | Access |
|---|---|---|---|
| GKE | `gke-vm-janr-10` (GCP `clusterapi-369611`, `europe-west4-a`) | **Yes** — GKE requires a bastion in the same network (`stratio-docs/.../installation.adoc`, "Bastion") | `gcloud compute ssh --tunnel-through-iap` |
| EKS | `eks-vm-janr` (AWS `eu-west-1`) | No — public VM in the default VPC | SSH, one Linux user + one `/32` SG rule per person |
| Azure | `azure-vm-janr` (subscription `EOS`, RG `bastion-vm-janr`, `westeurope`) | No — public VM | SSH, one Linux user + one `/32` NSG rule per person |

## Policy: stop, do not recreate

Keep each VM **stopped** when not in use (auto-stop does it every evening), never delete and recreate it routinely. A stopped VM bills only its disk and keeps `/deployments` (per-cluster `.kube/`, `secrets.yml`, backups), the docker image cache (faster bootstraps) and the colleagues' users. Recreate only if the VM is broken or for an OS upgrade — EKS and Azure are fully reproducible with `eks-vm.sh create` / `azure-vm.sh create` (Azure recreated from scratch 2026-10-01).

Housekeeping, monthly or when `df -h /` passes ~70%: `sudo docker image prune -a --filter until=168h` and remove `/deployments/<cluster>/` folders of clusters already deleted (move them to `/deployments/backup/` first if in doubt). Security patches: `unattended-upgrades` (installed by the EKS user-data and the Azure cloud-init).

## Persistent `/deployments` volume and its protection

`/deployments` lives on a **separate data volume**, never on the root disk, so the VM can be recreated without losing state. `create` reuses the existing volume (found by name/tag) and mounts it by label `LABEL=deployments`; group `deployers` has the fixed GID 2000 so permissions survive recreation.

| | EKS | Azure |
|---|---|---|
| Volume | EBS gp3 `<name>-data`, encrypted, tags `persistent=true`, `NoAutoDelete=true` | Managed disk `<name>-data` in its own RG `<rg>-data` |
| Survives VM deletion | Yes — attached after launch, so it is not deleted with the instance | Yes — different RG |
| Delete protection | **No native lock for EBS volumes.** Risk: Lambda `deleteUnusedEBS` (EventBridge `cron(0 23 ? * MON-FRI *)`, same account, eu-west-1) deletes unattached volumes. The volume is only unattached while recreating the VM. The original code (`delete_unused_ebs.py:18`) deleted every unattached volume in every region with no tag check; it was patched 2026-10-01 (PLT-4935) to skip volumes tagged `NoAutoDelete=true`. If that Lambda is ever redeployed from another source, re-check the filter | `CanNotDelete` lock on `<rg>-data`: blocks deleting the RG or the disk, still allows attach/detach. Remove the lock on purpose before a real deletion |

Rule for recreating the EKS VM: do it **before 23:00 UTC** on weekdays, or make sure the new VM attaches the volume the same session.

## Common layout (all VMs)

| Path | Content |
|---|---|
| `/deployments/` | Work root, group `deployers` (setgid), shared by every authorized user |
| `/deployments/<cluster>/` | One folder per cluster: `cluster.yaml` (descriptor copy), `secrets.yml`, optional `.tool-versions` pin, `create.log`, `.kube/` written by cloud-provisioner |
| `/deployments/descriptors/` | Descriptors and `secrets.yml` uploaded from the laptop (`secrets*` mode 640) |
| `/deployments/binaries/` | `cloud-provisioner-<version>` (release / rc / milestone) or `cloud-provisioner-PLT-XXXX` (dev build) — same naming as the GKE VM's `~/binaries/` |
| `/deployments/archives/` | Release tarballs (`cloud-provisioner-<version>.tar.gz`) and docker image tars (`cloud-provisioner-upgrade-<version>.tar`, `keos-install-<version>.tar`) — same role as the GKE VM's `~/archives/` |
| `/deployments/.asdf`, `/deployments/.tool-versions` | asdf tools and default versions (see [Tools, versions and updates](#tools-versions-and-updates)) |

The VMs cannot reach the internal Nexus (`qa.int.stratio.com`, VPN only): artifacts are downloaded (or built) on a laptop with VPN, then uploaded. cloud-provisioner itself is the only binary uploaded per version; cluster-operator ships as an image + chart pushed to the registries, nothing to upload to the VM.

**Disk sizing note**: the GKE VM's `~/archives/` holds 29 GB of docker image tars (1.5-3.8 GB each). Keep archives that size off the data volume or prune them: after the S6 measurement (PLT-4935) the data volume is 16 GB on EKS and Azure (asdf takes ~0.75 GB on EKS, ~0.3 GB on Azure).

`cloud-provisioner create cluster` runs as **root** (`sudo -i`). Under root `~` is `/root`, so reference uploaded files by absolute path.

### Uploading binaries, archives and descriptors

`eks-vm.sh upload` / `azure-vm.sh upload` pick the folder from the file and never overwrite an existing file (tested 2026-10-01 on both VMs):

| File | Goes to | Name |
|---|---|---|
| cloud-provisioner binary | `binaries/` | `cloud-provisioner-<Version>` read from `<file> version` (e.g. `cloud-provisioner-0.9.5-rc.1`), or the name you pass (e.g. `cloud-provisioner-PLT-4916`) |
| `*.tar`, `*.tar.gz`, `*.tgz` | `archives/` | Same file name |
| `*.yaml`, `*.yml` | `descriptors/` | Same file name; `secrets*` get mode 640 |

```bash
S=docs/bastion-vms/scripts/eks-vm.sh                     # or docs/bastion-vms/scripts/azure-vm.sh
$S upload <release-dir>/bin/cloud-provisioner                 # -> binaries/cloud-provisioner-<version>
$S upload bin/cloud-provisioner cloud-provisioner-PLT-XXXX      # dev build, explicit name
$S upload <release-dir>/cloud-provisioner-<version>.tar.gz    # -> archives/
$S upload <descriptor>.yaml
$S upload secrets.yml
```

Always check what the binary really is before using it: `cloud-provisioner version` prints the embedded version (e.g. the published `0.9.5` binary reports `0.9.5-rc.1`: tags `0.9.5-rc.1` and `0.9.5` are the same commit `a2212b33`, but report the exact version string).

### Kubeconfigs and `--retain`

Where `create cluster` leaves each kubeconfig (verified in Skind tag `0.9.5`):

| Kubeconfig | Path on the VM | Source |
|---|---|---|
| Workload cluster | `.kube/config` **relative to the directory `create` runs from** → `/deployments/<cluster>/.kube/config`, owner root, mode `0600`. Written right after "Creating the workload cluster", so it exists even if a later step fails | `createworker.go:64`, `:518` |
| Workload cluster, inside the bootstrap | `/kind/worker-cluster.kubeconfig` in `<cluster>-control-plane`, plus secret `worker-kubeconfig` | `createworker.go:63`, `:481`, `:504` |
| Bootstrap (kind) | `--kubeconfig`, else `$KUBECONFIG`, else `$HOME/.kube/config` → `/root/.kube/config` under `sudo -i`. Only useful with `--retain` | `create.go:186`, `createcluster.go:112` |

Never run `create` from `/root`: the workload `./.kube/config` and the bootstrap `$HOME/.kube/config` would be the same file.

**Getting the workload kubeconfig on the laptop**:

| Provider | How |
|---|---|
| Azure | `bash docs/bastion-vms/scripts/azure-vm.sh kubeconfig <cluster>` → `~/.kube/<cluster>.kubeconfig` (`0600`); then `kubectl --kubeconfig ~/.kube/<cluster>.kubeconfig get nodes`. It runs `sudo cat` over ssh because `scp` as `azureuser` cannot read the root `0600` file. It holds cluster-admin credentials: keep it `0600` |
| EKS | `aws eks update-kubeconfig` from the laptop (see [Deploying an EKS cluster from the VM](#deploying-an-eks-cluster-from-the-vm)) — no copy needed |
| GKE | GKE requires the bastion in the same network: run `kubectl` on `gke-vm-janr-10` with the kubeconfig in the cluster folder |

**`--retain`** (`create.go:130`, `:168`, `:195`): without it the bootstrap container is deleted both on failure and at the end of a successful create ("Cleaning up temporary cluster"); with it, it is kept in both cases. To work on a retained bootstrap:

```bash
docker exec -it <cluster>-control-plane bash      # aliases inside: k, kw, capi-logs, capa-logs/capz-logs/capg-logs, kc-logs
```

**Delete the retained bootstrap when done — mandatory**: `vm-autostop` treats a running `*-control-plane` container as a bootstrap in progress and postpones the evening stop until 23:30, so a forgotten bootstrap keeps the VM billed every night. From the cluster folder, as root:

```bash
/deployments/binaries/cloud-provisioner-<version> delete cluster --name <cluster>
```

It only deletes the local bootstrap containers and removes its context from the kubeconfig — it does not touch the cloud cluster (`internal/delete/delete.go`: `ListNodes` → `kubeconfig.Remove` → `DeleteNodes`).

## GKE — `gke-vm-janr-10` (existing)

Verified 2026-10-01 with `gcloud compute instances describe`: `n2-standard-8` (8 vCPU / 32 GB), Ubuntu 20.04, 200 GB disk, cgroup v2, network `hsbc-demo` / subnet `hsbc-demo-ca`, external IP, network tag `bastion`. Tools: Docker 27, helm 3.16, jq, yq, gcloud.

`vm-autostop` installed 2026-10-05 (18:00 `Europe/Madrid`, `poweroff` → instance `TERMINATED`); `stop-if-idle 5` tested live. Right after `instances start`, `gcloud compute ssh/scp` can fail for ~20-40 s with IAP `4003: failed to connect to backend` (sshd not up yet) — retry.

```bash
O=(--project=clusterapi-369611 --zone=europe-west4-a --tunnel-through-iap)
gcloud compute ssh gke-vm-janr-10 "${O[@]}"
gcloud compute scp "${O[@]}" Skind/bin/cloud-provisioner gke-vm-janr-10:/deployments/binaries/cloud-provisioner-<version>
```

## EKS — `eks-vm-janr`

Managed with [`docs/bastion-vms/scripts/eks-vm.sh`](scripts/eks-vm.sh) (AWS profile `cloud-provisioner`, region `eu-west-1`).

| Setting | Value |
|---|---|
| Type | `m6a.xlarge` (4 vCPU / 16 GiB, 0.1926 $/h) — cheapest non-burstable 16 GiB type sized for up to 4 parallel bootstraps (~2.5 GB peak each); `--type` to change |
| OS | Ubuntu 24.04 (latest Canonical AMI from SSM `/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id`), cgroup v2 |
| Disks | Root 32 GB gp3 (OS + docker images/volumes; 18 GB used with 4 parallel bootstraps), deleted with the instance; data 16 GB gp3 for `/deployments` (~0.75 GB asdf + binaries/descriptors), persistent (see below) |
| Network | Default VPC, default subnet in `eu-west-1a`, public IP (changes on every stop/start) |
| Security group | `eks-vm-janr-sg` — only inbound rule type is SSH from a `/32`, description `user:<name>` |
| IAM instance profile | `bastion-vm-ssm` — `AmazonSSMManagedInstanceCore` only (no S3/EC2 rights on the VM). SSM is a fallback path for the owner, not the access method for colleagues |
| IMDS | v2 only (`HttpTokens=required`) |
| Installed by user-data | Order matters: apt packages, data volume mount, group `deployers`, `vm-autostop` at 18:00 (see [Auto-stop](#auto-stop-after-hours-vm-autostop)), then — non-fatal — asdf tools from `tool-versions.eks` (see [Tools, versions and updates](#tools-versions-and-updates)). `vm-autostop` and `bastion-tools` are embedded gzipped + base64 (EC2 user data limit: 16 KB raw; rendered ~9.2 KB). Done when `/var/lib/cloud/instance/bastion-ready` exists (~3-5 min) |

### Lifecycle

```bash
S=docs/bastion-vms/scripts/eks-vm.sh
$S create --my-ip <your-public-ip> [--dry-run]   # SG + IAM profile + VM; SSH open to your /32 as user ubuntu
$S status                                        # id, state, public IP, SG rules (who has access from where)
$S stop                                          # stop when not in use — only the disk is billed while stopped
$S start                                         # prints the new public IP
$S ssh                                           # prints the ssh command for ubuntu
```

Your public IP: `curl -s https://checkip.amazonaws.com`. If it changes (VPN, home vs office), `revoke` + `grant` yourself again or add a second `/32`.

Termination is manual on purpose (`aws ec2 terminate-instances --instance-ids <id>`), after checking nobody has a bootstrap running in `/deployments`.

### Giving a colleague access

The colleague sends their **SSH public key** and their **public IP**. Then:

```bash
$S grant --user <name> --pubkey /path/to/<name>.pub --ip <their-ip>
$S revoke --user <name>
```

`grant` creates Linux user `<name>` (groups `docker`, `deployers`, passwordless sudo) with that key, and adds an SG rule `<ip>/32` described `user:<name>`. `revoke` deletes every SG rule `user:<name>` and the Linux user with its home. `status` lists the current rules, so the SG is the access list.

**What access means**: `docker` + sudo is root on the VM. Every authorized user can read every `secrets.yml` under `/deployments` (cloud credentials of every cluster deployed from that VM). Grant only to people who could already use those credentials.

### Deploying an EKS cluster from the VM

```bash
$S upload <binary>                       # -> /deployments/binaries/cloud-provisioner-<version>
$S upload <descriptor>.yaml; $S upload secrets.yml
$($S ssh)
sudo -i
install -d -m 2775 -g deployers /deployments/<cluster> && cd /deployments/<cluster>
install -m 664 -g deployers /deployments/descriptors/<descriptor>.yaml cluster.yaml
install -m 640 -g deployers /deployments/descriptors/secrets.yml secrets.yml
printf 'clusterctl 1.10.10\nclusterawsadm 2.9.3\n' > .tool-versions      # only for a 0.9.x cluster
/deployments/binaries/cloud-provisioner-<version> version
/deployments/binaries/cloud-provisioner-<version> create cluster --validate-only -d cluster.yaml --vault-password <pw> --name <cluster>
tmux new -s <cluster>
/deployments/binaries/cloud-provisioner-<version> create cluster -d cluster.yaml --vault-password <pw> --name <cluster> 2>&1 | tee create.log
```

Run long operations inside `tmux` (installed) so they survive an SSH disconnect; `vm-autostop` sees the running binary and postpones the evening stop.

`kubectl` against the EKS cluster is done **from the laptop** (`aws eks update-kubeconfig` with profile `cloud-provisioner-eks`): no AWS credentials are copied to the VM.

## Tools, versions and updates

[`docs/bastion-vms/scripts/bastion-tools.sh`](scripts/bastion-tools.sh), installed on the VM as `/usr/local/sbin/bastion-tools`. Version-sensitive CLIs are managed with **asdf**; everything else comes from apt.

| Tool | EKS VM | Azure VM | Installed by | Updated how |
|---|---|---|---|---|
| docker, git, jq, python3, unzip, curl, tmux | yes | yes | Ubuntu apt (curl, tmux already in the image) | `unattended-upgrades` daily — enabled origins are `${distro_codename}` and `${distro_codename}-security` only, **not** `-updates`: security fixes, no feature upgrades |
| kubectl | 1.36.5 (default), 1.33.13 | same | asdf (`asdf-community/asdf-kubectl`) | Never automatically — edit `.tool-versions` + `bastion-tools sync` |
| helm | 3.19.0 | 3.19.0 | asdf (`Antiarchitect/asdf-helm`) — same version as `DEPENDENCIES` | Same |
| yq | 4.54.1 | 4.54.1 | asdf (`sudermanjr/asdf-yq`) | Same |
| clusterctl | 1.13.6 (default), 1.10.10 | same | asdf (`pfnet-research/asdf-clusterctl`) — CAPI version of `DEPENDENCIES` | Same; required on the bastion for `clusterctl move` (`operations-manual.adoc`) |
| clusterawsadm | 2.13.0 (default), 2.9.3 | — | asdf (`kahun/asdf-clusterawsadm`, plugin last updated 2022 but works with asdf 0.20 — tested 2026-10-01) | Same |
| aws cli v2 | 2.37.7 | — | asdf (`MetricMike/asdf-awscli`, official pre-built installer) | Same |
| az (Azure CLI) | — | 2.90.0 at install | Microsoft apt repo (`/etc/apt/sources.list.d/azure-cli.sources`). **Not asdf**: the `azure-cli` asdf plugin calls `asdf local`, removed in asdf 0.16 | **Not** by unattended-upgrades (origin not allowed). `sudo apt-get update && sudo apt-get install --only-upgrade -y azure-cli` or `az upgrade` |
| asdf | v0.20.2 | v0.20.2 | Release binary in `/usr/local/bin/asdf` | Change `ASDF_VERSION` in `bastion-tools.sh`, then `tools sync` |
| cloud-provisioner | — | — | Uploaded per version with `eks-vm.sh upload` / `azure-vm.sh upload` to `/deployments/binaries/` | Manual |

Two versions of each version-sensitive tool are preinstalled to cover every cluster under test (k8s 1.32-1.37, cloud-provisioner 0.9.x and 0.10). A `.tool-versions` line can list several versions: all get installed, the **first** is the active default. Defaults live in `docs/bastion-vms/scripts/tool-versions.{eks,azure}`.

Which version a cluster folder should pin:

| Cluster | kubectl | clusterctl | clusterawsadm | Source |
|---|---|---|---|---|
| k8s 1.32, 1.33, 1.34 | `1.33.13` | — | — | kubectl supports ±1 minor of kube-apiserver ([k8s version skew policy](https://kubernetes.io/releases/version-skew-policy/)) |
| k8s 1.35, 1.36, 1.37 | `1.36.5` (default) | — | — | same |
| cloud-provisioner 0.9.x | — | `1.10.10` | `2.9.3` | `DEPENDENCIES` at tag `0.9.5` |
| cloud-provisioner 0.10 | — | `1.13.6` (default) | `2.13.0` (default) | `DEPENDENCIES` on `stratio/master` (`c0f6e40f`, 2026-09-30) |

helm is the same (`3.19.0`) on both lines (`0.9.5` and `master`), so one version is enough. Example for a 0.9.5 cluster on k8s 1.34:

```bash
sudo -i
cd /deployments/<cluster>
printf 'kubectl 1.33.13\nclusterctl 1.10.10\nclusterawsadm 2.9.3\n' > .tool-versions
asdf current
```

Python is **not** managed by asdf: aws cli v2 and az each bundle their own Python (3.14.6 at install time), `upgrade-provisioner.py` runs inside its `cloud-provisioner-upgrade` container, and helper scripts use the system `python3` (3.12 on Ubuntu 24.04, updated by apt).

### How asdf works here

- `ASDF_DATA_DIR=/deployments/.asdf` — plugins, downloads and installed versions live on the **persistent data volume** (measured with both version sets: ~810 MB on EKS with awscli, ~300 MB on Azure), so a recreated VM finds them already there.
- `/etc/profile.d/asdf.sh` puts `$ASDF_DATA_DIR/shims` first in `PATH`. Every tool is a shim that picks the version from the nearest `.tool-versions`, walking up from the current directory.
- Outside `/deployments` asdf falls back to `~/.tool-versions`: `bastion-tools install` (run by every `tools <sub>`) links it to `/deployments/.tool-versions` in `/root`, every `/home/*` and `/etc/skel`, so users added later by `grant` (`useradd -m`) get it too. Existing real files are left alone. Verified on both VMs 2026-10-02 (ubuntu/azureuser home, `/root`, a fresh `useradd -m` user, new tmux window).
- `/deployments/.tool-versions` holds the defaults (seeded from `tool-versions.default` only if missing, so edits survive). A cluster folder can pin its own:

```bash
sudo -i
cd /deployments/<cluster>
echo "kubectl 1.35.9" >> .tool-versions          # or: asdf set kubectl 1.35.9
SYNC_DIR=/deployments/<cluster> bastion-tools sync
kubectl version --client                          # v1.35.9 here, the default elsewhere
asdf current
```

- Root shells must be login shells (`sudo -i`) to get the shims; a bare `sudo kubectl` does not load `/etc/profile.d`.
- asdf >= 0.16 refuses to run without `$HOME` (`error loading config: $HOME is not defined`) and cloud-init runs user data without it; `bastion-tools` sets `HOME=/root` when missing (found on the first from-scratch Azure run, 2026-10-01).
- Useful commands: `asdf current` (what applies here and why), `asdf list kubectl`, `asdf list all kubectl 1.36`, `asdf plugin update --all`.

### Changing a default version

```bash
sudo -i
cd /deployments
asdf set kubectl 1.36.6          # rewrites /deployments/.tool-versions
bastion-tools sync               # installs it, reshims, regenerates completions
```

From the laptop, `eks-vm.sh tools sync` / `azure-vm.sh tools sync` pushes the current `bastion-tools.sh` + defaults and runs the same sync (`status`, `completions`, and on Azure `install-az`, also accepted). Changing the repo defaults (`tool-versions.*`) only affects VMs whose `/deployments/.tool-versions` does not exist yet.

### Bash completion

**Alias `k` = `kubectl`** for every user: `bastion-tools sync` / `completions` writes `/etc/profile.d/bastion-aliases.sh` (login shells: ssh, `sudo -i`, new tmux windows) and `completions/k`, which binds kubectl's completion to `k`. Verified on Azure and EKS 2026-10-02 in the three shells (`k ge<Tab>` → `get`).

`bash-completion` (2.11) ships with the image and `/etc/bash.bashrc` loads it. `bastion-tools sync` / `completions` writes one file per tool in `/usr/share/bash-completion/completions/`, loaded lazily on the first Tab (no shell start-up cost): `asdf completion bash`, `kubectl|helm|clusterctl|clusterawsadm completion bash`, `yq shell-completion bash`, `complete -C aws_completer aws`, and a link to azure-cli's own `/etc/bash_completion.d/azure-cli`. docker and git come with their packages. Completions are generated from the **default** versions; a per-cluster pin of another minor may differ in a few flags.

## Auto-stop after hours (`vm-autostop`)

[`docs/bastion-vms/scripts/vm-autostop.sh`](scripts/vm-autostop.sh), installed on the VM as `/usr/local/sbin/vm-autostop` with a systemd timer that runs every 15 min.

**Rule**: from `TIME` (default 18:00 `Europe/Madrid`, DST-aware) until 23:30, if the VM is **idle** it announces the stop with `wall` and schedules it 5 min later (transient timer `vm-autostop-stop`); when that fires it **re-checks activity** and only then powers off (AWS/GCP) or deallocates (Azure). Busy = a kind container `*-control-plane`, a `cloud-provisioner-upgrade` container, or a running `cloud-provisioner*` **binary** (a `tail`/`tee`/`ssh` that only mentions it does not count). If busy, the stop is postponed and retried 15 min later. Past 23:30 it gives up for that night. Log: `journalctl -t vm-autostop`.

| Command (on the VM, `sudo`) | Effect |
|---|---|
| `vm-autostop status` | Enabled?, time, next timer run, skip flag, pending shutdown, current activity |
| `vm-autostop enable [HH:MM]` | Turn on / change the time |
| `vm-autostop disable` | Turn off until `enable` |
| `vm-autostop skip-today` | No stop tonight (also cancels a pending one); normal again tomorrow |
| `vm-autostop cancel` | Cancel an announced shutdown — the timer retries in 15 min, use `skip-today` to stop it for the night |
| `vm-autostop stop-if-idle [MIN]` | Stop now (default 1 min) only if idle; exit code 2 and no stop if busy |
| `vm-autostop install [HH:MM] [poweroff\|azure-deallocate]` / `uninstall` | Add / remove binary, timer, service and config |

### Changing the time, disabling and re-enabling — per provider

The setting lives on the VM (`/etc/vm-autostop.conf` + `vm-autostop.timer`, `vm-autostop.sh:114-126`): it survives reboots and stop/start. The VM must be running to change it. `enable` without a time keeps the saved one.

```bash
# EKS — eks-vm-janr
S=docs/bastion-vms/scripts/eks-vm.sh
bash $S autostop status
bash $S autostop enable 19:30     # change the time (also re-enables)
bash $S autostop disable          # off until enable
bash $S autostop enable           # re-enable with the saved time
bash $S autostop skip-today       # only tonight
bash $S autostop install 18:00    # existing VM without it
```

```bash
# Azure — azure-vm-janr (stop = deallocate)
S=docs/bastion-vms/scripts/azure-vm.sh
bash $S autostop status
bash $S autostop enable 19:30
bash $S autostop disable
bash $S autostop enable
bash $S autostop skip-today
bash $S autostop install 18:00    # always installed with azure-deallocate
```

```bash
# GKE — gke-vm-janr-10 (shared, no wrapper script)
O=(--project=clusterapi-369611 --zone=europe-west4-a --tunnel-through-iap)
gcloud compute ssh gke-vm-janr-10 "${O[@]}" --command 'sudo vm-autostop status'
gcloud compute ssh gke-vm-janr-10 "${O[@]}" --command 'sudo vm-autostop enable 19:30'
gcloud compute ssh gke-vm-janr-10 "${O[@]}" --command 'sudo vm-autostop disable'
gcloud compute ssh gke-vm-janr-10 "${O[@]}" --command 'sudo vm-autostop enable'
gcloud compute ssh gke-vm-janr-10 "${O[@]}" --command 'sudo vm-autostop skip-today'
gcloud compute scp "${O[@]}" docs/bastion-vms/scripts/vm-autostop.sh gke-vm-janr-10:/tmp/
gcloud compute ssh gke-vm-janr-10 "${O[@]}" --command 'sudo bash /tmp/vm-autostop.sh install 18:00'
```


`eks-vm.sh create` and `azure-vm.sh create` install it by default at 18:00 (`--autostop HH:MM|off`); on GKE it was installed by hand (2026-10-05).

**Why on the VM and not a cloud scheduler** (EventBridge, GCE instance schedules): only the VM can see whether a bootstrap is running; a cloud-side schedule would stop it in the middle of a `create cluster`.

**Billing after a guest shutdown** (official docs, read 2026-10-01): AWS — an EBS-backed instance **stops** ([EC2 docs](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Using_ChangingInstanceInitiatedShutdownBehavior.html)); GCP — the instance stops and "you keep incurring charges for any resources that remain attached" (disk, IP) ([GCE docs](https://docs.cloud.google.com/compute/docs/instances/stop-start-instance)); **Azure — it ends in *Stopped (allocated)*, which is still billed** ([Azure docs](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing)): the Azure VM needs a *deallocate* through the API instead of a guest poweroff.

Starting the VMs in the morning stays manual (`eks-vm.sh start`, `azure-vm.sh start`, `gcloud compute instances start gke-vm-janr-10 --project=clusterapi-369611 --zone=europe-west4-a`).

## Azure — `azure-vm-janr`

Managed with [`docs/bastion-vms/scripts/azure-vm.sh`](scripts/azure-vm.sh) — same actions and options as `eks-vm.sh` (`create --my-ip`, `status`, `start`, `stop`, `ssh`, `grant`, `revoke`, `autostop …`, `tools …`, `upload`) plus `kubeconfig <cluster>`. Uses the `az` session of the laptop (subscription `EOS`). Giving a colleague access works as on EKS (`grant`/`revoke`, NSG rule `user-<name>` instead of an SG rule).

| Setting | Value |
|---|---|
| Resource group | `bastion-vm-janr` (`westeurope`), dedicated — deleting it removes everything |
| Size | `Standard_D4as_v5` (4 vCPU / 16 GB, 0.208 $/h) — cheapest non-burstable 16 GB size; `--size` to change |
| OS | Ubuntu 24.04 (`Canonical:ubuntu-24_04-lts:server:latest`, Gen2), admin user `azureuser`, key `~/.ssh/azure_rsa` |
| Disks | Root 32 GB StandardSSD (tier E4, 2.40 $/month); data 16 GB StandardSSD (tier E3, 1.20 $/month) in RG `<rg>-data`. Standard SSD base throughput is up to 100 MB/s (below gp3's 125 MB/s); not measured under 4 parallel bootstraps |
| Network | Static Standard public IP (kept across deallocate); NSG `azure-vm-janr-nsg` with one rule `user-<name>` (SSH from `/32`) per person, created with `--nsg-rule NONE` so there is no open-to-the-world SSH rule |
| Identity | System-assigned managed identity with **Virtual Machine Contributor scoped to this VM only** — lets `vm-autostop` deallocate it |
| cloud-init | Same as EKS, with `tool-versions.azure`, Azure CLI from Microsoft's apt repo, and `vm-autostop install 18:00 azure-deallocate`. Must be ASCII-only: `az vm create --custom-data` encodes it as latin-1 and aborts otherwise (the script refuses non-ASCII custom data) |

`stop` always **deallocates** (`az vm deallocate`): a plain `az vm stop` or a guest poweroff leaves the VM *Stopped (allocated)* and compute is still billed. `vm-autostop` with `STOP_METHOD=azure-deallocate` gets a token from IMDS and POSTs `…/deallocate` to ARM; if that fails (HTTP ≠ 200/202) it logs it and leaves the VM running so the next 15-min check retries — it never falls back to a billed poweroff.

Tested live 2026-10-05: `vm-autostop` deallocate (→ `PowerState/deallocated`, not `stopped`), `grant`/`revoke` with a throwaway user (login, `sudo`, `docker`, write in `/deployments`, alias `k` + `kubectl` in a login shell; after `revoke`: `Permission denied (publickey)`, user, home, sudoers file and NSG rule gone) and `kubeconfig <cluster>`.

### Deploying an Azure cluster from the VM

```bash
S=docs/bastion-vms/scripts/azure-vm.sh
$S upload <binary>                       # -> /deployments/binaries/cloud-provisioner-<version>
$S upload <descriptor>.yaml; $S upload secrets.yml
$($S ssh)
sudo -i
install -d -m 2775 -g deployers /deployments/<cluster> && cd /deployments/<cluster>
install -m 664 -g deployers /deployments/descriptors/<descriptor>.yaml cluster.yaml
install -m 640 -g deployers /deployments/descriptors/secrets.yml secrets.yml
printf 'clusterctl 1.10.10\n' > .tool-versions      # only for a 0.9.x cluster
/deployments/binaries/cloud-provisioner-<version> version
/deployments/binaries/cloud-provisioner-<version> create cluster --validate-only -d cluster.yaml --vault-password <pw> --name <cluster>
tmux new -s <cluster>
/deployments/binaries/cloud-provisioner-<version> create cluster -d cluster.yaml --vault-password <pw> --name <cluster> 2>&1 | tee create.log
```

Then, from the laptop: `bash docs/bastion-vms/scripts/azure-vm.sh kubeconfig <cluster>` and `kubectl --kubeconfig ~/.kube/<cluster>.kubeconfig get nodes`. If the create fails after "Creating the workload cluster ✓", the VMs already exist: `az group delete --name <cluster> --yes --no-wait` and relaunch only once `az group show --name <cluster>` returns not found.
