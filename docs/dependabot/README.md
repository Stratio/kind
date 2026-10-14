# Automated Go dependency updates (Dependabot)

This repository uses [Dependabot version updates](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/about-dependabot-version-updates) to keep the Go module dependencies of `cloud-provisioner` current. Dependabot opens pull requests with minor and patch updates against `master`. They are reviewed, built by Jenkins and merged like any other pull request: nothing is merged automatically.

The configuration lives in `.github/dependabot.yml`.

**Why**: dependency drift is what turns a routine vulnerability report into a multi-day upgrade. Small, frequent updates that pass the same pipeline as any other change keep the module graph current, so security fixes in libraries arrive as one more pull request instead of a release-blocking task.

## What it covers and what it does not

| Covered | Not covered |
|---|---|
| Every Go module in the root `go.mod` (direct and indirect), minor and patch versions | Major versions — upgraded by hand |
| The `master` branch | Release branches (`branch-X.Y`) — an artifact under QA must not change underneath it |
| | `hack/tools/go.mod` and `images/kindnetd/go.mod` — inherited from upstream kind, not part of the `cloud-provisioner` build |
| | Versions that `cloud-provisioner` installs: providers, charts and images in `DEPENDENCIES`, in the Go code and in the templates |
| | The Python dependencies of the upgrade image (`upgrade/resources/scripts`) |
| | Container base images (`Dockerfile`s) and the build image of the pipeline (`Jenkinsfile`) |
| | Security updates (Dependabot alerts / security pull requests) — not enabled |

## How it works

```
daily 06:30 UTC  Dependabot reads go.mod on master
      │
      ├─► opens one pull request for the group, plus a standalone pull request
      │   for any update it cannot place in the group
      │
      ├─► Jenkins builds the pull request (required status check "Jenkins")
      │
      └─► a reviewer checks the go.mod diff and the build, approves and
          squash-merges it, as with any other pull request
```

### `.github/dependabot.yml`

```yaml
version: 2
updates:
  - package-ecosystem: "gomod"
    directory: "/"
    target-branch: "master"
    labels:
      - "master"
      - "go"
      - "dependencies"
      - "skip doPushDockerECR"
    schedule:
      interval: "daily"
      time: "06:30"
    cooldown:
      default-days: 7
      semver-patch-days: 1
    allow:
      - dependency-type: "direct"
      - dependency-type: "indirect"
    groups:
      gomod-minor-patch:
        update-types: ["minor", "patch"]
    ignore:
      - dependency-name: "*"
        update-types: ["version-update:semver-major"]
      # Internal modules of the AWS SDK only move together with the SDK packages that use them
      - dependency-name: "github.com/aws/aws-sdk-go-v2/internal/*"
```

| Setting | Value | Why |
|---|---|---|
| `target-branch` | `master` | Release branches are never touched |
| `schedule` | daily, 06:30 UTC | Updates are ready at the start of the working day |
| `cooldown` | 7 days (1 day for patches) | A release is proposed only after it has been public for a week, which filters out releases that are yanked or quickly fixed. Patches carry fixes and wait only one day |
| `groups` | one group | Every minor and patch update goes in the same pull request. Updates that do not match a group rule are opened as individual pull requests ([GitHub docs](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference)) |
| `open-pull-requests-limit` | not set | GitHub's default applies: at most five open version-update pull requests; no new ones are raised until some are merged or closed |
| `ignore` | majors; AWS SDK internal modules | An internal module of `aws-sdk-go-v2` updated on its own can be incompatible with the SDK packages that import it: `internal/ini` 1.8.6 proposed alone did not compile against `config` v1.18.17 (`undefined: ini.NewIntValue`). With the entry, the Dependabot log reports `All versions of github.com/aws/aws-sdk-go-v2/internal/... ignored, no update allowed`. Those modules still move when an updated SDK package requires a newer version of them, because Go selects the highest version any module requires ([Go modules reference](https://go.dev/ref/mod#minimal-version-selection)) |
| `labels` | see "Labels" | The pipeline uses them |

## Repository requirements

| Setting | Where | Required value |
|---|---|---|
| Labels | Issues → Labels | Every label listed in `dependabot.yml` must exist |
| Dependabot version updates | Settings → Advanced Security → Dependabot version updates | Enabled. `Stratio/kind` is a fork of `kubernetes-sigs/kind`, and "version updates are not automatically enabled on forks when a `dependabot.yml` configuration file is present" ([GitHub docs](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuring-dependabot-version-updates)): merging `dependabot.yml` alone starts nothing. A repository administrator enables it once in the UI |

No other setting changes: branch protection, required reviews, merge methods and secrets stay as they are.

The upstream kind workflows in `.github/workflows/` (`cgroup2`, `docker`, `podman`) only run on pull requests against `main` and do not take part in this flow.

## Labels

| Label | Purpose |
|---|---|
| `master` | Target branch, same convention as any other pull request |
| `go`, `dependencies` | Classification |
| `skip doPushDockerECR` | Skip the pipeline stage `doPushDockerECR` on the pull request: a dependency update needs the images built and scanned, not pushed to ECR. Same `skip do<Stage>` convention as the existing `skip doUT` and `skip doGrypeScan` labels |

`doDeploy` cannot be skipped: besides uploading the package to Nexus, `bin/deploy.sh` renames `bin/cloud-provisioner-<version>.tar.gz` to `bin/cloud-provisioner.tar.gz`, the file the next stage (extraction of the binary) unpacks. With a `skip doDeploy` label that stage fails, so Dependabot pull requests upload their package to Nexus like any other pull request.

The labels `go`, `dependencies` and `skip doPushDockerECR` have to be created before enabling Dependabot. If a label does not exist, Dependabot ignores it ([GitHub docs](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference)): the pull request is opened without it and Dependabot leaves a comment listing the missing labels. A missing `skip` label means the pull request runs those stages like any other.

## Reviewing a Dependabot pull request

1. Read the `go.mod` diff, not only the description: a group can raise a module further than the description says, because Go takes the highest version any updated module requires.
2. Wait for `Jenkins` to be green.
3. Approve and squash-merge. The squash title can be edited at merge time, for example to add a Jira key; by default it is the Dependabot title (`Bump the gomod-minor-patch group with N updates` or `Bump <module> from X to Y in the gomod-minor-patch group`).

Dependabot rebases its own pull requests when they conflict with `master`. If one is only behind `master`, comment `@dependabot rebase` on it.

Observed volume over 8 days with this configuration: 4 pull requests (1 to 21 updates each), all in the group. The first run on a repository that has not been updated for a while opens a large group pull request.

## Validation gate: what is checked and what is not

Every update goes through the same Jenkins pipeline as any other pull request. Its stages (`Jenkinsfile`): `doPackage` (build of the `cloud-provisioner` package), `doDeploy`, extraction of the binary, `doDockers` (images `cloud-provisioner` and `cloud-provisioner-upgrade`), `doPushDockerECR` and `doGrypeScan` on the binary.

The pipeline has **no unit-test stage**. Not covered either:

- **Descriptor validation end to end.** No test loads a full cluster descriptor through `commons.GetClusterDescriptor`. A library update that changes validation behaviour builds and passes the pipeline, and only fails when the binary runs. Example: `github.com/go-playground/validator/v10` v10.30 panics on a `dive` tag placed on a struct field, where older versions accepted it. When a pull request moves `validator`, the cloud SDKs or `cobra`, run `cloud-provisioner create cluster --validate-only` with a descriptor of each provider before merging.
- **Cluster creation.** The pipeline does not create a cluster, so runtime changes in the cloud SDKs (retries, default endpoints, credential chains) are not detected at this stage. They are covered by the release process: every release is validated by QA on real clusters before it is published.

## Handling failures

| Situation | What happens | What to do |
|---|---|---|
| `Jenkins` is red on a Dependabot pull request | It stays open | Open the build log. If the update needs a code change, push it to the Dependabot branch or close the pull request and do the update in a regular change |
| `Dependabot Updates` job red with `go_module_path_mismatch` | A module moved to a new import path; Dependabot cannot rename imports, so that dependency is not updated and the job run is reported as failed. The pull requests for the other dependencies are still opened | Change the import path in the code in a regular change (as done for `al.essio.dev/pkg/shellescape`) |
| A standalone pull request for an indirect module | Dependabot can propose an indirect module outside the group. If it is only compatible with a newer version of its parent library, the build fails. When a later merge removes the module from `go.mod`, Dependabot closes the pull request with "no longer a dependency" | Nothing, or add an `ignore` entry if it repeats |

## Disabling it

- Temporarily: set `open-pull-requests-limit: 0` in `dependabot.yml`, which GitHub documents as the way to temporarily disable version updates. The `Dependabot Updates` workflow shown in the Actions tab cannot be disabled from there (the API answers HTTP 422).
- Permanently: delete `.github/dependabot.yml`.
