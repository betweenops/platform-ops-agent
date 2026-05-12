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
    if scenario_type == "edgeops_ansible_failure":
        return _analyze_edgeops_ansible_failure(scenario)

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

    return "\n".join(lines)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


EDGEOPS_PLAYBOOK_INTENTS = {
    "pxe": "Prepare PXE, DHCP, DNS, and boot artifacts so bare metal systems can install successfully.",
    "redfish": "Discover BMC endpoints and apply Redfish-based boot and reboot actions for target systems.",
    "proxmox": "Bring Proxmox nodes online and configure repositories, networking, and monitoring.",
    "s3": "Install and configure RADOS Gateway components and collect resulting object storage details.",
    "rke2": "Build and deploy the RKE2 cluster components required for the edge Kubernetes environment.",
    "bigbang": "Deploy the Big Bang application platform on top of the provisioned cluster.",
    "opnsense": "Build and start OPNsense firewall virtual machine resources for the edge environment.",
}


def _analyze_edgeops_ansible_failure(scenario: dict) -> dict:
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

    if "check_dependencies" in task_file or "all conditions do not" in focused_text:
        signals.append("A declared dependency was not ready before this reconciliation stage began.")
        causes.append("A prerequisite custom resource did not reach the expected successful condition set.")
        next_steps.extend(
            [
                "Inspect the dependency objects referenced by this resource and confirm their status conditions.",
                "Verify the dependent resource names, kinds, and namespaces match what the composition emitted.",
            ]
        )

    if any(token in focused_text for token in ("nexus", "docker_login", "manifests", "failed to fetch")):
        signals.append("The failure happened while resolving artifacts from Nexus or an upstream package/image source.")
        causes.append("Nexus connectivity, credentials, repository paths, or mirrored artifact availability are incorrect.")
        next_steps.extend(
            [
                "Verify `nexus_url`, repository names, and image or artifact paths supplied to the role.",
                "Check network reachability from the edge-controller execution host to Nexus and any upstream fallback endpoints.",
                "Confirm the Nexus credentials used by the task are valid for the referenced repos.",
            ]
        )

    if playbook_family == "redfish" or any(
        token in task_text for token in ("redfish", "/redfish/v1/", "ilo", "idrac")
    ):
        signals.append("The failure intersects with Redfish or BMC communication.")
        causes.append("The target BMC may be unreachable, using the wrong credentials, or presenting an unexpected endpoint or certificate.")
        next_steps.extend(
            [
                "Test the Redfish endpoint directly from the controller network path.",
                "Confirm BMC credentials, port, and vendor assumptions used by the role.",
            ]
        )

    if any(token in focused_text for token in ("wait_for_connection", "wait for server to come back online", "timed out waiting")):
        signals.append("Automation was waiting for a host to become reachable again and that did not happen in time.")
        causes.append("The node may have failed to reboot cleanly, lost network access, or booted with an invalid interface configuration.")
        next_steps.extend(
            [
                "Validate the target node's network configuration and current power state.",
                "Check whether the preceding reboot or interface template change produced an unreachable host.",
            ]
        )

    if _looks_like_airgap_artifact_issue(playbook_family, environment, logs, focused_text):
        signals.append("This may be a downstream symptom of missing PXE or OS artifacts in an air-gapped content path.")
        causes.append("Required boot images, initrd content, or mirrored packages may be absent from the local Nexus repositories.")
        next_steps.extend(
            [
                "Verify the required PXE images, Debian netboot artifacts, and any custom airgap initrd files exist in local Nexus.",
                "Check whether the host ever began the expected OS install, rather than only watching for it to return on the network.",
                "Correlate this failure with the prerequisite Pxe reconciliation to see whether artifact preparation failed earlier.",
            ]
        )

    if (
        "kubernetes.core.k8s" in focused_text
        or "kubernetes.core.k8s_info" in focused_text
        or ("forbidden" in focused_text and "kubernetes.core" in focused_text)
        or ("not found" in task_text and "kubernetes.core" in task_text)
    ):
        signals.append("The reconciliation touched Kubernetes API objects and encountered an API lookup or patch problem.")
        causes.append("The referenced resource may be missing, in the wrong namespace, or blocked by RBAC.")
        next_steps.extend(
            [
                "Confirm the resource exists with the expected apiVersion, kind, name, and namespace.",
                "Check the edge-controller service account permissions for read or patch access.",
            ]
        )

    if not causes:
        causes.append("The log snippet is not yet specific enough to classify the failure beyond the task boundary.")
        next_steps.extend(
            [
                "Capture the full fatal Ansible task output including module args, stderr, and any preceding context.",
                "Add one or two log lines before the fatal task so stage detection can be more precise.",
            ]
        )

    task_intent = _edgeops_task_intent(playbook_family, task_name, task_file)
    playbook_intent = EDGEOPS_PLAYBOOK_INTENTS.get(playbook_family or "", "")

    if playbook_intent:
        causes.append(f"The surrounding playbook is responsible for: {playbook_intent}")

    summary = (
        f"{metadata.get('kind', 'resource')} {metadata.get('name', 'unknown')} failed reconciliation "
        f"during '{task_name}'."
    )

    report = {
        "metadata": metadata,
        "health": "failed",
        "summary": summary,
        "signals": _dedupe(signals),
        "likely_causes": _dedupe(causes),
        "next_steps": _dedupe(next_steps),
        "task_intent": task_intent,
        "operator_context": {
            "controller": operator_context.get("controller"),
            "playbook": operator_context.get("playbook"),
            "imported_playbook": operator_context.get("imported_playbook"),
            "playbook_family": playbook_family,
            "host": host,
            "task_file": task_file or None,
            "module": module or None,
            "environment_mode": environment.get("mode"),
        },
        "evidence": {
            "log_line_count": len(logs),
            "task_name": task_name,
            "message": message or None,
            "stderr": stderr or None,
        },
    }
    if related_resources:
        report["operator_context"]["related_resources"] = ", ".join(
            f"{item.get('kind', 'Resource')}/{item.get('name', 'unknown')}" for item in related_resources
        )
    return report


def _infer_playbook_family(operator_context: dict) -> str:
    for key in ("playbook_family", "playbook", "imported_playbook"):
        value = operator_context.get(key, "")
        if value:
            parts = Path(value).parts
            for part in parts:
                if part in EDGEOPS_PLAYBOOK_INTENTS:
                    return part
    return ""


def _edgeops_task_intent(playbook_family: str, task_name: str, task_file: str) -> str:
    text = f"{task_name} {task_file}".lower()

    if "check_dependencies" in text:
        return "Confirm upstream dependent custom resources completed successfully before continuing this reconciliation."
    if "check for dns image in nexus" in text:
        return "Verify whether the dnsmasq container image exists in Nexus so the role can choose a mirrored image source."
    if "check for dhcpd image in nexus" in text:
        return "Verify whether the DHCP container image exists in Nexus before starting the PXE DHCP service."
    if "check for tftp image in nexus" in text:
        return "Verify whether the TFTP container image exists in Nexus before preparing boot assets."
    if "login to nexus" in text:
        return "Authenticate to Nexus so later image pulls and artifact downloads can use the internal registry."
    if "wait for server to come back online" in text:
        return "Pause reconciliation until the target host is reachable again after reboot or power-cycle activity."
    if "configure repos" in text or "configure_repos" in text:
        return "Prepare package repositories and trust keys so the host can install required Proxmox packages."
    if "configure interfaces" in text or "proxmox_configure_if" in text:
        return "Apply the target network interface and bonding configuration required for the host."
    if "redfish_force_boot" in text:
        return "Patch the Redfish-related objects so future reconciliations do not force another install boot."
    if playbook_family == "pxe":
        return EDGEOPS_PLAYBOOK_INTENTS["pxe"]
    if playbook_family == "redfish":
        return EDGEOPS_PLAYBOOK_INTENTS["redfish"]
    if playbook_family == "proxmox":
        return EDGEOPS_PLAYBOOK_INTENTS["proxmox"]
    if playbook_family == "rke2":
        return EDGEOPS_PLAYBOOK_INTENTS["rke2"]
    if playbook_family == "s3":
        return EDGEOPS_PLAYBOOK_INTENTS["s3"]
    if playbook_family == "bigbang":
        return EDGEOPS_PLAYBOOK_INTENTS["bigbang"]
    if playbook_family == "opnsense":
        return EDGEOPS_PLAYBOOK_INTENTS["opnsense"]
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

    return airgap_hint and (artifact_hint or playbook_family in {"pxe", "redfish", "proxmox"}) and wait_symptom
