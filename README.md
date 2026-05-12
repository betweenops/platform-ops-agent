# AI Platform Operations Agent

A learning-focused project for building practical AI workflows around platform operations, incident investigation, and Kubernetes troubleshooting.

## Why This Project Exists

This repository is meant to help explore how AI can support day-to-day platform engineering work without overcommitting to a fixed stack too early.

The main goal is not to build a polished product on day one. The main goal is to learn by shipping small, useful capabilities such as:

- summarizing Kubernetes events and logs
- explaining likely causes of workload failures
- generating operator-friendly remediation suggestions
- turning noisy infrastructure data into concise incident context

## Problem Space

Platform and SRE work often involves:

- jumping between logs, events, deployment history, and cluster state
- interpreting repetitive but time-sensitive failure patterns
- writing the same summaries and handoff notes over and over
- needing a fast first-pass explanation before deeper debugging

This project explores whether an AI-assisted workflow can make those tasks faster, clearer, and more repeatable.

## Project Principles

- Start with a narrow workflow before expanding scope.
- Prefer a CLI or simple local interface before building a full UI.
- Keep integrations read-only until there is a strong reason to automate writes.
- Use realistic sample data and replayable scenarios before depending on a live cluster.
- Treat model quality, prompts, and evaluations as core product work.
- Keep technology choices flexible until the workflow proves valuable.

## Initial Direction

The first useful version of this project should answer a small set of questions well:

- "Why is this pod or deployment unhealthy?"
- "What changed recently that might explain this failure?"
- "Summarize the important warning signs in this namespace."
- "What are the most likely next troubleshooting steps?"

That can be built without a frontend, without a multi-service architecture, and without a full agent framework.

## Recommended MVP

The most efficient starting point is:

1. Build a local CLI tool.
2. Feed it structured inputs such as Kubernetes events, pod status, and logs.
3. Generate a concise diagnostic summary plus recommended next steps.
4. Save representative scenarios as test fixtures.
5. Evaluate outputs over time as prompts and tools improve.

This keeps the project focused on the hard part first: making the AI workflow actually useful.

## Possible Technical Path

These are options, not commitments.

Core app:
- Python
- simple CLI entrypoint
- local fixture files for events, logs, and cluster state

AI layer:
- OpenAI or Anthropic APIs
- prompt-driven workflow first
- tool calling only when it clearly improves outcomes
- agent orchestration only after a simple flow proves insufficient

Interfaces:
- CLI first
- optional API later
- optional frontend later

Future infrastructure:
- Docker
- Kubernetes
- GitHub Actions
- observability and tracing if the project grows beyond a local prototype

## What Not To Optimize Yet

- frontend framework choice
- backend framework choice
- multi-agent orchestration
- deployment topology
- production-grade hosting

Those choices matter later, but they are not the current learning bottleneck.

## Early Milestones

### Milestone 1

Create a CLI that accepts a small bundle of Kubernetes signals and returns:

- a short health summary
- likely root-cause candidates
- recommended next investigation steps

### Milestone 2

Add replayable scenarios such as:

- crash loop
- image pull failure
- failed rollout
- resource pressure
- misconfigured secret or config map

### Milestone 3

Add lightweight evaluation so outputs can be compared as prompts and tools evolve.

### Milestone 4

Decide whether the project actually needs:

- a REST API
- a web UI
- agent orchestration
- live cluster connectivity

## Example User Prompts

- "Summarize why this deployment is failing."
- "What are the highest-signal warnings in this namespace?"
- "Based on these logs and events, what should I check next?"
- "Create an incident summary from this rollout failure."

## Longer-Term Possibilities

If the core workflow proves useful, the project could later expand into:

- natural language querying over cluster state
- incident report generation
- guided troubleshooting playbooks
- Slack or chat-based operational assistance
- safe automation for clearly bounded remediation tasks

## Current Status

This project is in the discovery and prototyping phase.

The immediate objective is to build a small, credible MVP that improves operator understanding before expanding the architecture.

## Getting Started

This repository now includes a minimal local CLI for replayable troubleshooting scenarios.

Install in editable mode:

```bash
pip install -e .
```

List bundled scenarios:

```bash
platform-ops-agent list-scenarios
```

Analyze a scenario:

```bash
platform-ops-agent analyze crashloop-api
```

Render JSON output:

```bash
platform-ops-agent analyze missing-secret --json
```

Analyze a generic Ansible/operator provisioning failure:

```bash
platform-ops-agent analyze ansible-provisioning-artifact-failure
```

Analyze a hidden air-gap failure where the visible symptom is a blade never returning:

```bash
platform-ops-agent analyze ansible-airgap-wait-loop
```

## Sanitization

Sample fixtures in this repo use placeholder hostnames and redacted environment values only.

Do not commit:

- API keys, tokens, passwords, or kubeconfigs
- internal repository URLs or hostnames
- local filesystem paths

If you need environment-specific values while building local scenarios, keep them in an untracked file such as `vars/fixture-values.local.json`, starting from `vars/fixture-values.example.json`.

## Current Repo Shape

- `src/platform_ops_agent/`: CLI and analysis logic
- `fixtures/scenarios/`: replayable Kubernetes failure examples
- `fixtures/scenarios/ansible-*.json`: replayable Ansible/operator failure examples
- `tests/`: baseline tests for the deterministic analyzer

## Immediate Next Build Steps

- add more scenario fixtures from real-world Kubernetes failures
- add real controller log excerpts and map more task files to operator intent
- correlate downstream wait-loop failures with earlier PXE artifact and mirror failures
- improve the analyzer input schema
- integrate an LLM behind a well-defined interface
- add evaluation prompts and expected output checks
