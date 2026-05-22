from __future__ import annotations

import json
from pathlib import Path


FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "scenarios"


def available_scenarios() -> list[str]:
    return sorted(path.stem for path in FIXTURES_DIR.glob("*.json"))


def load_scenario(identifier: str) -> dict:
    candidate = Path(identifier)
    if candidate.exists():
        path = candidate
    else:
        path = FIXTURES_DIR / f"{identifier}.json"

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def analyze_scenario(scenario: dict) -> dict:
    scenario_type = scenario.get("scenario_type", "kubernetes_workload")
    if scenario_type == "ansible_operator_failure":
        return _analyze_ansible_operator_failure(scenario)
    if scenario_type == "custom_resource":
        return _analyze_custom_resource(scenario)

    metadata = scenario.get("metadata", {})
    pod_status = scenario.get("pod_status", {})
    deployment = scenario.get("deployment_status", {})
    events = scenario.get("events", [])
    logs = scenario.get("logs", [])

    signals: list[str] = []
    causes: list[str] = []
    next_steps: list[str] = []
    health = "degraded"

    waiting_reason = (
        pod_status.get("container_state", {})
        .get("waiting", {})
        .get("reason", "")
    )
    rollout_condition_reason = deployment.get("condition_reason", "")
    last_termination_reason = (
        pod_status.get("container_state", {})
        .get("last_terminated", {})
        .get("reason", "")
    )

    event_reasons = {event.get("reason", "") for event in events}
    event_messages = " ".join(event.get("message", "") for event in events).lower()
    joined_logs = "\n".join(logs).lower()

    if waiting_reason == "CrashLoopBackOff" or "BackOff" in event_reasons:
        health = "failed"
        signals.append("Container is repeatedly crashing and backing off.")
        causes.append("The application process exits shortly after startup.")
        next_steps.extend(
            [
                "Inspect the most recent container logs for the first fatal error.",
                "Check recent config, secret, or dependency changes that affect startup.",
            ]
        )

    if waiting_reason in {"ImagePullBackOff", "ErrImagePull"} or waiting_reason.startswith("ImagePull"):
        health = "failed"
        signals.append("Kubernetes cannot pull the container image.")
        causes.append("The image reference, registry access, or image tag is invalid.")
        next_steps.extend(
            [
                "Verify the image name and tag exist in the target registry.",
                "Check image pull secrets and registry authentication for the namespace.",
            ]
        )

    if "FailedScheduling" in event_reasons and (
        "insufficient cpu" in event_messages or "insufficient memory" in event_messages
    ):
        health = "failed"
        signals.append("The workload cannot be scheduled because cluster capacity is insufficient.")
        causes.append("Requested resources exceed currently available node capacity.")
        next_steps.extend(
            [
                "Compare pod resource requests against allocatable cluster capacity.",
                "Check whether autoscaling, pending drain activity, or quota limits are blocking placement.",
            ]
        )

    if "ProgressDeadlineExceeded" in event_reasons or rollout_condition_reason == "ProgressDeadlineExceeded":
        health = "failed"
        signals.append("The deployment rollout exceeded its progress deadline.")
        causes.append("New replicas did not become ready within the rollout window.")
        next_steps.extend(
            [
                "Inspect ReplicaSet and pod events to find why new replicas did not become ready.",
                "Compare the new rollout against the previous successful revision.",
            ]
        )

    if "FailedMount" in event_reasons or "secret" in event_messages or "configmap" in event_messages:
        health = "failed"
        signals.append("Startup is blocked by a missing or invalid mounted dependency.")
        causes.append("A required Secret, ConfigMap, or mounted volume is unavailable or misconfigured.")
        next_steps.extend(
            [
                "Confirm the referenced Secret and ConfigMap names exist in the same namespace.",
                "Check key names and mounted paths used by the container startup command.",
            ]
        )

    if last_termination_reason == "OOMKilled" or "oomkilled" in joined_logs:
        health = "failed"
        signals.append("The container was terminated by the kernel for exceeding memory limits.")
        causes.append("Memory limits are too low or the application has a memory spike or leak.")
        next_steps.extend(
            [
                "Review memory requests and limits against observed runtime behavior.",
                "Check whether a recent code path or dataset change increased memory usage.",
            ]
        )

    if not signals:
        signals.append("No known failure signature matched the supplied scenario.")
        causes.append("The issue needs either richer input data or additional detection rules.")
        next_steps.extend(
            [
                "Add more events, status fields, and representative logs to the scenario fixture.",
                "Expand rule coverage or replace heuristics with model-assisted reasoning later.",
            ]
        )

    if any(token in joined_logs for token in ("connection refused", "timeout", "timed out")):
        causes.append("A downstream dependency appears unavailable during startup or readiness checks.")
        next_steps.append("Verify network reachability and the health of dependent services.")

    unique_causes = _dedupe(causes)
    unique_steps = _dedupe(next_steps)
    unique_signals = _dedupe(signals)

    summary = (
        f"{metadata.get('name', 'workload')} in namespace "
        f"{metadata.get('namespace', 'default')} is {health}. "
        f"Primary signal: {unique_signals[0]}"
    )

    return {
        "metadata": metadata,
        "health": health,
        "summary": summary,
        "signals": unique_signals,
        "likely_causes": unique_causes,
        "next_steps": unique_steps,
        "evidence": {
            "event_count": len(events),
            "log_line_count": len(logs),
            "waiting_reason": waiting_reason or None,
            "last_termination_reason": last_termination_reason or None,
        },
    }


def render_text_report(report: dict) -> str:
    metadata = report["metadata"]
    lines = [
        f"Scenario: {metadata.get('name', 'unknown')}",
        f"Namespace: {metadata.get('namespace', 'default')}",
        f"Health: {report['health']}",
        "",
        "Summary",
        f"- {report['summary']}",
        "",
        "Signals",
    ]
    lines.extend(f"- {signal}" for signal in report["signals"])
    lines.append("")
    lines.append("Likely Causes")
    lines.extend(f"- {cause}" for cause in report["likely_causes"])

    task_intent = report.get("task_intent")
    if task_intent:
        lines.append("")
        lines.append("Task Intent")
        lines.append(f"- {task_intent}")

    operator_context = report.get("operator_context")
    if operator_context:
        lines.append("")
        lines.append("Operator Context")
        for label, value in operator_context.items():
            if value:
                lines.append(f"- {label.replace('_', ' ')}: {value}")

    lines.append("")
    lines.append("Next Steps")
    lines.extend(f"- {step}" for step in report["next_steps"])
    lines.append("")
    lines.append("Evidence")
    evidence = report["evidence"]

    if "event_count" in evidence:
        lines.append(f"- events: {evidence['event_count']}")
    if "log_line_count" in evidence:
        lines.append(f"- log lines: {evidence['log_line_count']}")

    waiting_reason = evidence.get("waiting_reason")
    if waiting_reason:
        lines.append(f"- waiting reason: {waiting_reason}")

    last_termination_reason = evidence.get("last_termination_reason")
    if last_termination_reason:
        lines.append(f"- last termination reason: {last_termination_reason}")

    task_name = evidence.get("task_name")
    if task_name:
        lines.append(f"- task name: {task_name}")

    message = evidence.get("message")
    if message:
        lines.append(f"- message: {message}")

    condition_types = evidence.get("condition_types")
    if condition_types:
        lines.append(f"- condition types: {condition_types}")

    redfish_message_id = evidence.get("redfish_message_id")
    if redfish_message_id:
        lines.append(f"- redfish message id: {redfish_message_id}")

    dependency_wait_targets = evidence.get("dependency_wait_targets")
    if dependency_wait_targets:
        lines.append(f"- dependency wait targets: {dependency_wait_targets}")

    dhcp_expected = evidence.get("dhcp_expected_systems")
    if dhcp_expected is not None:
        lines.append(f"- dhcp expected systems: {dhcp_expected}")

    dhcp_rendered = evidence.get("dhcp_rendered_reservations")
    if dhcp_rendered is not None:
        lines.append(f"- dhcp rendered reservations: {dhcp_rendered}")

    dhcp_missing_hosts = evidence.get("dhcp_missing_hosts")
    if dhcp_missing_hosts:
        lines.append(f"- dhcp missing hosts: {dhcp_missing_hosts}")

    peer_success_count = evidence.get("peer_success_count")
    if peer_success_count is not None:
        lines.append(f"- peer success count: {peer_success_count}")

    return "\n".join(lines)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def _analyze_custom_resource(scenario: dict) -> dict:
    metadata = scenario.get("metadata", {})
    conditions = scenario.get("conditions", [])
    events = scenario.get("events", [])
    related_resources = scenario.get("related_resources", [])
    logs = scenario.get("logs", [])
    crossplane_owner = scenario.get("crossplane_owner") or {}

    signals: list[str] = []
    causes: list[str] = []
    next_steps: list[str] = []

    if crossplane_owner:
        owner_namespace = crossplane_owner.get("namespace", "default")
        owner_name = crossplane_owner.get("name", "unknown")
        signals.append(
            f"This resource is wrapped by a Crossplane Object ({owner_namespace}/{owner_name}); "
            f"direct edits to this CR will be reverted on the next Crossplane reconcile."
        )
        next_steps.append(
            "To force a re-run of the underlying role, patch the owning Object's "
            "spec.forProvider.manifest.spec.this_ansible_run_id (bump it to any new value). "
            "Do not annotate or edit this inner CR directly."
        )

    condition_index = {
        item.get("type", ""): item
        for item in conditions
        if item.get("type")
    }

    failure_condition = next(
        (
            item for item in conditions
            if str(item.get("type", "")).lower() in {"failure", "failed", "degraded"}
            and str(item.get("status", "")).lower() == "true"
        ),
        None,
    )
    unhealthy_condition = next(
        (
            item for item in conditions
            if str(item.get("type", "")).lower() in {"ready", "healthy", "synced", "successful"}
            and str(item.get("status", "")).lower() == "false"
        ),
        None,
    )
    running_condition = next(
        (
            item for item in conditions
            if str(item.get("type", "")).lower() == "running"
        ),
        None,
    )

    health = "healthy"
    primary_condition = failure_condition or unhealthy_condition or running_condition

    if failure_condition:
        health = "failed"
        signals.append(
            f"Condition '{failure_condition.get('type')}' is True with reason "
            f"'{failure_condition.get('reason', 'unknown')}'."
        )
        causes.append(
            failure_condition.get("message")
            or "The custom resource reported an explicit failure condition."
        )

    if unhealthy_condition:
        health = "failed"
        signals.append(
            f"Condition '{unhealthy_condition.get('type')}' is False with reason "
            f"'{unhealthy_condition.get('reason', 'unknown')}'."
        )
        causes.append(
            unhealthy_condition.get("message")
            or "The resource is not yet ready or synchronized."
        )

    if running_condition and health != "failed":
        running_reason = str(running_condition.get("reason", "")).lower()
        if running_reason in {"failed", "error"}:
            health = "failed"
        elif str(running_condition.get("status", "")).lower() == "true":
            health = "progressing"
        signals.append(
            f"Condition 'Running' reports reason '{running_condition.get('reason', 'unknown')}'."
        )
        if running_condition.get("message"):
            causes.append(running_condition["message"])

    event_reasons = {event.get("reason", "") for event in events}
    event_messages = " ".join(event.get("message", "") for event in events).lower()
    joined_logs = "\n".join(logs).lower()

    if any(reason in event_reasons for reason in ("Warning", "Failed", "BackOff", "FailedMount")):
        health = "failed" if health == "healthy" else health
        signals.append("Recent warning events were emitted for this resource or its dependents.")

    if "forbidden" in event_messages or "forbidden" in joined_logs:
        health = "failed"
        causes.append("The controller likely hit an RBAC denial while reconciling this resource.")
        next_steps.append("Check the controller service account permissions for this resource and its dependencies.")

    if any(token in event_messages or token in joined_logs for token in ("not found", "missing", "does not exist")):
        causes.append("A referenced dependency or generated object may be missing.")
        next_steps.append("Verify referenced objects exist with the expected names, kinds, and namespaces.")

    if any(token in event_messages or token in joined_logs for token in ("timeout", "timed out", "connection refused")):
        causes.append("A downstream service or endpoint was unreachable during reconciliation.")
        next_steps.append("Check network reachability and dependent service health from the controller's execution path.")

    if related_resources:
        signals.append("The resource declares dependencies or related objects that may explain the reconciliation state.")
        next_steps.append("Inspect the related resources and compare their status conditions against this resource's failure point.")

    if not signals:
        if conditions:
            health = "progressing"
            signals.append("The resource has conditions, but none matched a known failure signature.")
            causes.append("The controller may still be reconciling, or the condition types are not yet covered by the analyzer.")
            next_steps.append("Review the current condition set and add a rule for this controller's status model if needed.")
        else:
            health = "unknown"
            signals.append("The resource exposed no status conditions and no related workload evidence was collected.")
            causes.append("The analyzer needs either controller-specific conditions or richer related-object context.")
            next_steps.append("Capture controller logs and related object status for this custom resource type.")

    summary = (
        f"{metadata.get('kind', 'resource')} {metadata.get('name', 'unknown')} in namespace "
        f"{metadata.get('namespace', 'default')} is {health}. "
        f"Primary signal: {signals[0]}"
    )

    operator_context_out: dict = {
        "condition_count": len(conditions),
        "primary_condition_type": primary_condition.get("type") if primary_condition else None,
        "primary_condition_reason": primary_condition.get("reason") if primary_condition else None,
        "related_resources": ", ".join(
            f"{item.get('kind', 'Resource')}/{item.get('name', 'unknown')}"
            for item in related_resources
        ) or None,
    }

    if crossplane_owner:
        owner_namespace = crossplane_owner.get("namespace", "default")
        owner_name = crossplane_owner.get("name", "unknown")
        operator_context_out["crossplane_owner_object"] = f"{owner_namespace}/{owner_name}"
        if crossplane_owner.get("api_version"):
            operator_context_out["crossplane_owner_api_version"] = crossplane_owner["api_version"]

    return {
        "metadata": metadata,
        "health": health,
        "summary": summary,
        "signals": _dedupe(signals),
        "likely_causes": _dedupe(causes),
        "next_steps": _dedupe(next_steps),
        "operator_context": operator_context_out,
        "evidence": {
            "event_count": len(events),
            "log_line_count": len(logs),
            "condition_types": ", ".join(sorted(condition_index.keys())) or None,
        },
    }


PLAYBOOK_INTENTS = {
    "provisioning": "Prepare DHCP, DNS, and boot artifacts so target systems can install successfully.",
    "hardware-control": "Discover out-of-band management endpoints and apply boot or reboot actions for target systems.",
    "node-config": "Bring target nodes online and configure repositories, networking, and monitoring.",
    "object-storage": "Install and configure object storage components and collect resulting service details.",
    "cluster-bootstrap": "Build and deploy the Kubernetes cluster components required for the target environment.",
    "platform-apps": "Deploy higher-level platform applications on top of the provisioned cluster.",
    "network-appliance": "Build and start firewall or network appliance virtual machine resources.",
}


def _analyze_ansible_operator_failure(scenario: dict) -> dict:
    metadata = scenario.get("metadata", {})
    operator_context = scenario.get("operator_context", {})
    failure = scenario.get("ansible_failure", {})
    environment = scenario.get("environment", {})
    related_resources = scenario.get("related_resources", [])
    logs = scenario.get("logs", [])

    playbook_family = failure.get("playbook_family") or _infer_playbook_family(operator_context)
    task_name = failure.get("task_name", "unknown task")
    task_file = failure.get("task_file", "")
    host = failure.get("host", "")
    module = failure.get("module", "")
    message = failure.get("message", "")
    stderr = failure.get("stderr", "")
    focused_text = "\n".join(
        part for part in [task_name, task_file, module, message, stderr, "\n".join(logs)] if part
    ).lower()
    task_text = "\n".join(
        part for part in [task_name, task_file, module, message, stderr] if part
    ).lower()

    signals = [
        (
            f"{metadata.get('kind', 'resource')} reconciliation failed in the "
            f"{playbook_family or 'unknown'} playbook family on host {host or 'unknown'}."
        ),
        f"Ansible task failure occurred at '{task_name}'.",
    ]
    causes: list[str] = []
    next_steps: list[str] = []
    extra_operator_context: dict = {}
    extra_evidence: dict = {}

    health: str = "failed"
    summary_override: str | None = None
    matched_real_failure: bool = False
    gate_matched: bool = False

    redfish_response = scenario.get("redfish_response", {}) or {}
    dependency_wait = scenario.get("dependency_wait", {}) or {}
    rendered_artifacts = scenario.get("rendered_artifacts", {}) or {}
    peer_evidence = scenario.get("peer_evidence", {}) or {}

    if "check_dependencies" in task_file or "all conditions do not" in focused_text:
        gate_matched = True
        # Real classification of this branch happens after all other rules
        # have run, so we know whether to treat it as gate-only or as a
        # co-occurring real failure. See post-branch block below.

    if any(token in focused_text for token in ("nexus", "docker_login", "manifests", "failed to fetch")):
        matched_real_failure = True
        signals.append("The failure happened while resolving artifacts from an internal registry or mirrored package source.")
        causes.append("Registry connectivity, credentials, repository paths, or mirrored artifact availability are incorrect.")
        next_steps.extend(
            [
                "Verify the registry URL, repository names, and image or artifact paths supplied to the role.",
                "Check network reachability from the automation controller to the internal registry and any upstream fallback endpoints.",
                "Confirm the registry credentials used by the task are valid for the referenced repositories.",
            ]
        )

    redfish_message_id = str(redfish_response.get("message_id", "") or "").lower()
    if (
        "unabletomodifyduringsystempost" in redfish_message_id
        or "unabletomodifyduringsystempost" in focused_text
        or "systembusy" in redfish_message_id
    ):
        matched_real_failure = True
        health = "blocked"
        kind_label = metadata.get("kind", "resource")
        name_label = metadata.get("name", "unknown")
        summary_override = (
            f"{kind_label} {name_label} is blocked because the target system is currently in POST; "
            f"the BMC rejected the configuration PATCH."
        )
        signals.append(
            "The BMC returned a 'system busy / in POST' response to the configuration PATCH; this is not a controller bug."
        )
        causes.append(
            "The target host is mid-POST (firmware self-test). HPE iLO rejects BootSourceOverride and similar PATCHes "
            "until POST completes."
        )
        next_steps.extend(
            [
                "Open the BMC Integrated Remote Console for the affected host and confirm whether POST is hung or simply slow.",
                "If POST is hung, perform a virtual power cycle from the BMC or press the physical power button; do not retry the operator reconciliation.",
                "This failure mode requires human intervention at the BMC. Do not re-arm this_ansible_run_id on the owning object until the host is past POST.",
            ]
        )
        if redfish_response.get("message_id"):
            extra_evidence["redfish_message_id"] = redfish_response["message_id"]
        if redfish_response.get("endpoint"):
            extra_operator_context["redfish_endpoint"] = redfish_response["endpoint"]
    elif playbook_family == "hardware-control" or any(
        token in task_text for token in ("redfish", "/redfish/v1/", "ilo", "idrac", "bmc")
    ):
        matched_real_failure = True
        signals.append("The failure intersects with out-of-band management or BMC communication.")
        causes.append("The target BMC may be unreachable, using the wrong credentials, or presenting an unexpected endpoint or certificate.")
        next_steps.extend(
            [
                "Test the out-of-band management endpoint directly from the controller network path.",
                "Confirm BMC credentials, port, and vendor assumptions used by the role.",
            ]
        )

    dhcp_config = rendered_artifacts.get("dhcp_config", {}) or {}
    dhcp_rendered = dhcp_config.get("rendered_reservations")
    dhcp_expected = dhcp_config.get("expected_systems")
    dhcp_missing = dhcp_config.get("missing_hosts") or []
    if (
        isinstance(dhcp_rendered, int)
        and isinstance(dhcp_expected, int)
        and dhcp_rendered < dhcp_expected
    ) or dhcp_missing:
        matched_real_failure = True
        signals.append(
            f"The rendered DHCP config has fewer reservations ({dhcp_rendered}) than the expected system count "
            f"({dhcp_expected}); late-discovered hosts are not in the file."
        )
        causes.append(
            "The provisioning role renders DHCP from the systems list at the moment the CR succeeds. "
            "If discovery was partial at render time, the role does not automatically re-render when later hosts appear."
        )
        next_steps.extend(
            [
                "Inspect the rendered DHCP config on the boot services host and compare its host list against current discovery.",
                "Force a re-render by patching the owning Crossplane Object's spec.forProvider.manifest.spec.this_ansible_run_id; the provisioning role will re-execute and the DHCP container will restart with the new config.",
                "For each missing host, confirm it was discovered after the original render and is expected to PXE.",
            ]
        )
        if isinstance(dhcp_expected, int):
            extra_evidence["dhcp_expected_systems"] = dhcp_expected
        if isinstance(dhcp_rendered, int):
            extra_evidence["dhcp_rendered_reservations"] = dhcp_rendered
        if dhcp_missing:
            extra_evidence["dhcp_missing_hosts"] = ", ".join(dhcp_missing)

    peers_succeeded = peer_evidence.get("peers_succeeded_recently") or []
    if (
        "error: time out opening" in focused_text
        and "debian-installer" in focused_text
        and peers_succeeded
    ):
        matched_real_failure = True
        health = "degraded"
        kind_label = metadata.get("kind", "resource")
        name_label = metadata.get("name", "unknown")
        summary_override = (
            f"{kind_label} {name_label} appears to be stuck on a wedged TFTP session while peers on the same fabric "
            f"completed PXE recently."
        )
        signals.append(
            "GRUB timed out opening the kernel image over TFTP for this host; recent peers booted successfully — pattern matches a wedged tftpd-hpa per-client session."
        )
        causes.append(
            "tftpd-hpa can wedge for an individual client while continuing to serve others; the operator cannot recover this without restarting the tftp container."
        )
        next_steps.extend(
            [
                "On the boot services host, run `docker restart tftp` (or the equivalent for your container runtime).",
                "From the affected host's GRUB prompt or BMC, reboot to retry PXE.",
                "If multiple hosts are affected simultaneously, this is not the wedged-session pattern — investigate the tftp container logs directly.",
            ]
        )
        extra_evidence["peer_success_count"] = len(peers_succeeded)
    elif any(token in focused_text for token in ("wait_for_connection", "wait for server to come back online", "timed out waiting")):
        matched_real_failure = True
        signals.append("Automation was waiting for a host to become reachable again and that did not happen in time.")
        causes.append("The node may have failed to reboot cleanly, lost network access, or booted with an invalid interface configuration.")
        next_steps.extend(
            [
                "Validate the target node's network configuration and current power state.",
                "Check whether the preceding reboot or interface template change produced an unreachable host.",
            ]
        )

    if _looks_like_airgap_artifact_issue(playbook_family, environment, logs, focused_text):
        matched_real_failure = True
        signals.append("This may be a downstream symptom of missing boot or OS artifacts in an air-gapped content path.")
        causes.append("Required boot images, initrd content, or mirrored packages may be absent from the local artifact repositories.")
        next_steps.extend(
            [
                "Verify the required boot images, OS netboot artifacts, and any custom air-gap initrd files exist in the local artifact store.",
                "Check whether the host ever began the expected OS install, rather than only watching for it to return on the network.",
                "Correlate this failure with the prerequisite boot-preparation stage to see whether artifact setup failed earlier.",
            ]
        )

    if (
        "kubernetes.core.k8s" in focused_text
        or "kubernetes.core.k8s_info" in focused_text
        or ("forbidden" in focused_text and "kubernetes.core" in focused_text)
        or ("not found" in task_text and "kubernetes.core" in task_text)
    ):
        matched_real_failure = True
        signals.append("The reconciliation touched Kubernetes API objects and encountered an API lookup or patch problem.")
        causes.append("The referenced resource may be missing, in the wrong namespace, or blocked by RBAC.")
        next_steps.extend(
            [
                "Confirm the resource exists with the expected apiVersion, kind, name, and namespace.",
                "Check the automation controller service account permissions for read or patch access.",
            ]
        )

    if gate_matched and not matched_real_failure:
        health = "waiting"
        kind_label = metadata.get("kind", "resource")
        name_label = metadata.get("name", "unknown")
        summary_override = (
            f"{kind_label} {name_label} is waiting on upstream dependencies; the controller's "
            f"check_dependencies step bailed by design."
        )
        wait_targets = dependency_wait.get("targets") or []
        target_strs = [
            f"{item.get('kind', 'Resource')}/{item.get('name', 'unknown')}"
            for item in wait_targets
            if isinstance(item, dict)
        ]
        target_summary = ", ".join(target_strs) if target_strs else "the upstream resource"
        signals.append(
            "This is the controller's idle/gating behavior, not a fault: the playbook intentionally short-circuits "
            "until prerequisite custom resources reach a Successful state."
        )
        causes.append(
            "A prerequisite custom resource has not yet reported the expected successful condition set. "
            "The reconciler will keep retrying on its backoff schedule until the upstream resolves."
        )
        next_steps.extend(
            [
                f"No action needed on this resource until {target_summary} reports Successful.",
                "Re-check the upstream resource's status conditions; do not patch this CR or its owning Object in response to this 'failure'.",
            ]
        )
        if target_strs:
            extra_evidence["dependency_wait_targets"] = ", ".join(target_strs)
    elif gate_matched and matched_real_failure:
        signals.append(
            "A dependency check also signalled missing upstream readiness, but a concrete failure dominates "
            "and is described above."
        )
        causes.append("A prerequisite custom resource did not reach the expected successful condition set.")
        next_steps.append(
            "After resolving the concrete failure above, re-check upstream CR conditions before re-arming this resource."
        )

    if not causes:
        causes.append("The log snippet is not yet specific enough to classify the failure beyond the task boundary.")
        next_steps.extend(
            [
                "Capture the full fatal Ansible task output including module args, stderr, and any preceding context.",
                "Add one or two log lines before the fatal task so stage detection can be more precise.",
            ]
        )

    task_intent = _task_intent(playbook_family, task_name, task_file)
    playbook_intent = PLAYBOOK_INTENTS.get(playbook_family or "", "")

    if playbook_intent:
        causes.append(f"The surrounding playbook is responsible for: {playbook_intent}")

    if summary_override:
        summary = summary_override
    else:
        summary = (
            f"{metadata.get('kind', 'resource')} {metadata.get('name', 'unknown')} failed reconciliation "
            f"during '{task_name}'."
        )

    operator_context_out = {
        "controller": operator_context.get("controller"),
        "playbook": operator_context.get("playbook"),
        "imported_playbook": operator_context.get("imported_playbook"),
        "playbook_family": playbook_family,
        "host": host,
        "task_file": task_file or None,
        "module": module or None,
        "environment_mode": environment.get("mode"),
    }
    operator_context_out.update(extra_operator_context)

    evidence_out = {
        "log_line_count": len(logs),
        "task_name": task_name,
        "message": message or None,
        "stderr": stderr or None,
    }
    evidence_out.update(extra_evidence)

    report = {
        "metadata": metadata,
        "health": health,
        "summary": summary,
        "signals": _dedupe(signals),
        "likely_causes": _dedupe(causes),
        "next_steps": _dedupe(next_steps),
        "task_intent": task_intent,
        "operator_context": operator_context_out,
        "evidence": evidence_out,
    }
    if related_resources:
        report["operator_context"]["related_resources"] = ", ".join(
            f"{item.get('kind', 'Resource')}/{item.get('name', 'unknown')}" for item in related_resources
        )
    return report


def _infer_playbook_family(operator_context: dict) -> str:
    explicit = operator_context.get("playbook_family", "")
    if explicit:
        return explicit
    aliases = {
        "pxe": "provisioning",
        "redfish": "hardware-control",
        "proxmox": "node-config",
        "s3": "object-storage",
        "rke2": "cluster-bootstrap",
        "bigbang": "platform-apps",
        "opnsense": "network-appliance",
    }
    for key in ("playbook", "imported_playbook"):
        value = operator_context.get(key, "")
        if value:
            parts = Path(value).parts
            for part in parts:
                if part in aliases:
                    return aliases[part]
    return ""


def _task_intent(playbook_family: str, task_name: str, task_file: str) -> str:
    text = f"{task_name} {task_file}".lower()

    if "check_dependencies" in text:
        return "Confirm upstream dependent custom resources completed successfully before continuing this reconciliation."
    if "check for dns image in nexus" in text or "check for dns image in registry" in text:
        return "Verify whether the DNS service container image exists in the internal registry so the role can choose a mirrored image source."
    if "check for dhcpd image in nexus" in text or "check for dhcpd image in registry" in text:
        return "Verify whether the DHCP container image exists in the internal registry before starting the boot service."
    if "check for tftp image in nexus" in text or "check for tftp image in registry" in text:
        return "Verify whether the TFTP container image exists in the internal registry before preparing boot assets."
    if "login to nexus" in text or "login to registry" in text:
        return "Authenticate to the internal registry so later image pulls and artifact downloads can succeed."
    if "wait for server to come back online" in text:
        return "Pause reconciliation until the target host is reachable again after reboot or power-cycle activity."
    if "configure repos" in text or "configure_repos" in text:
        return "Prepare package repositories and trust keys so the host can install required node packages."
    if "configure interfaces" in text or "proxmox_configure_if" in text:
        return "Apply the target network interface and bonding configuration required for the host."
    if "redfish_force_boot" in text:
        return "Patch the hardware-control objects so future reconciliations do not force another install boot."
    if playbook_family in PLAYBOOK_INTENTS:
        return PLAYBOOK_INTENTS[playbook_family]
    return "Clarify what this task was trying to accomplish by mapping it to the surrounding role or playbook stage."


def _looks_like_airgap_artifact_issue(
    playbook_family: str,
    environment: dict,
    logs: list[str],
    focused_text: str,
) -> bool:
    joined_logs = "\n".join(logs).lower()
    environment_mode = str(environment.get("mode", "")).lower()
    airgap_hint = environment_mode in {"airgap", "disconnected"} or any(
        token in joined_logs for token in ("airgap", "disconnected", "local nexus", "custom airgap initrd")
    )
    artifact_hint = any(
        token in joined_logs or token in focused_text
        for token in (
            "netboot.tar.gz",
            "sha256sums",
            "initrd-airgap",
            "check for dns image in nexus",
            "check for dhcpd image in nexus",
            "check for tftp image in nexus",
            "download boot image",
            "download hash file",
            "404",
            "manifest unknown",
            "failed to fetch",
        )
    )
    wait_symptom = any(
        token in focused_text or token in joined_logs
        for token in (
            "wait_for_connection",
            "wait for server to come back online",
            "timed out waiting",
            "connection timed out",
        )
    )

    return airgap_hint and (artifact_hint or playbook_family in {"provisioning", "hardware-control", "node-config"}) and wait_symptom
