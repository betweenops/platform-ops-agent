# AI Platform Operations Agent

AI-assisted troubleshooting for Kubernetes workloads and controller-driven platform operations.

## Status

This project is usable today and under active development.

Current capabilities include:

- analyzing replayable troubleshooting fixtures
- analyzing live Kubernetes workloads from a kubeconfig or in-cluster context
- summarizing condition-driven custom resources
- producing operator-focused failure summaries and next steps

## What It Does

The agent collects platform signals such as:

- workload status
- pod health
- Kubernetes events
- recent container logs
- custom resource conditions and related references

It then generates a concise report with:

- current health
- primary failure signals
- likely causes
- suggested next investigation steps

## Install

```bash
pip install -e .
```

## Local Usage

List bundled scenarios:

```bash
platform-ops-agent list-scenarios
```

Analyze a fixture:

```bash
platform-ops-agent analyze crashloop-api
```

Render JSON output:

```bash
platform-ops-agent analyze missing-secret --json
```

Analyze a generic Ansible/operator failure:

```bash
platform-ops-agent analyze ansible-provisioning-artifact-failure
```

Analyze an air-gap wait-loop scenario:

```bash
platform-ops-agent analyze ansible-airgap-wait-loop
```

## Live Cluster Usage

Analyze a live workload from your current kubeconfig:

```bash
platform-ops-agent analyze-live \
  --api-version apps/v1 \
  --kind Deployment \
  --namespace default \
  --name example-app
```

Useful options:

- `--context` to target a specific kubeconfig context
- `--kubeconfig` to use a non-default kubeconfig file
- `--container` to choose a specific container for log collection
- `--tail-lines` to control how much recent log output is collected

Custom resources are supported when they expose meaningful `status.conditions` and related references:

```bash
platform-ops-agent analyze-live \
  --api-version platform.example.io/v1alpha1 \
  --kind PlatformInstallation \
  --namespace ops-system \
  --name site-a
```

## Cluster Deployment

Starter manifests are included in `manifests/`:

- `namespace.yaml`
- `serviceaccount.yaml`
- `rbac.yaml`
- `job.yaml`

Typical rollout:

1. Build and push the image.
2. Apply the namespace, service account, and RBAC.
3. Extend `manifests/rbac.yaml` with read-only rules for your custom API groups if needed.
4. Update `manifests/job.yaml` with your image and target object.
5. Run the Job and inspect its logs.

Build locally:

```bash
docker build -t platform-ops-agent:latest .
```

Apply the starter manifests:

```bash
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/serviceaccount.yaml
kubectl apply -f manifests/rbac.yaml
kubectl apply -f manifests/job.yaml
kubectl logs -n platform-ops-agent job/platform-ops-agent-analysis
```

## Repo Layout

- `src/platform_ops_agent/` contains the CLI, analyzers, and live-cluster collector
- `fixtures/scenarios/` contains replayable troubleshooting scenarios
- `manifests/` contains starter Kubernetes deployment manifests
- `tests/` contains baseline unit tests

## Safety

This project is designed to start read-only.

Do not commit:

- API keys, tokens, passwords, or kubeconfigs
- internal repository URLs or hostnames
- local filesystem paths

If you need local environment values, keep them in an untracked file such as `vars/fixture-values.local.json`, starting from `vars/fixture-values.example.json`.
