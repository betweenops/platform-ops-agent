from __future__ import annotations

from dataclasses import dataclass
from typing import Any


WORKLOAD_KINDS = {"pod", "deployment", "replicaset", "statefulset", "daemonset"}


@dataclass
class CollectedWorkload:
    object_data: dict[str, Any]
    related_pods: list[dict[str, Any]]
    events: list[dict[str, Any]]
    logs: list[str]


def collect_live_workload(
    api_version: str,
    kind: str,
    namespace: str,
    name: str,
    *,
    container: str | None = None,
    context: str | None = None,
    kubeconfig: str | None = None,
    tail_lines: int = 100,
) -> CollectedWorkload:
    try:
        from kubernetes import client, config
        from kubernetes.dynamic import DynamicClient
    except ImportError as exc:
        raise RuntimeError(
            "The kubernetes package is not installed. Run `pip install -e .` first."
        ) from exc

    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config(config_file=kubeconfig, context=context)

    api_client = client.ApiClient()
    dynamic_client = DynamicClient(api_client)

    resource = dynamic_client.resources.get(api_version=api_version, kind=kind)
    object_data = resource.get(name=name, namespace=namespace).to_dict()

    core_api = client.CoreV1Api(api_client)
    related_pods = _collect_related_pods(core_api, kind=kind, namespace=namespace, object_data=object_data)
    events = _collect_events(core_api, namespace=namespace, object_data=object_data, related_pods=related_pods)
    logs = _collect_logs(
        core_api,
        namespace=namespace,
        related_pods=related_pods,
        target_kind=kind,
        target_name=name,
        container=container,
        tail_lines=tail_lines,
    )

    return CollectedWorkload(
        object_data=object_data,
        related_pods=related_pods,
        events=events,
        logs=logs,
    )


def build_workload_scenario(collected: CollectedWorkload) -> dict[str, Any]:
    object_data = collected.object_data
    metadata = object_data.get("metadata", {})
    status = object_data.get("status", {})
    kind = object_data.get("kind", "Unknown")
    normalized_kind = kind.lower()

    if normalized_kind not in WORKLOAD_KINDS:
        scenario: dict[str, Any] = {
            "scenario_type": "custom_resource",
            "metadata": {
                "name": metadata.get("name", "unknown"),
                "namespace": metadata.get("namespace", "default"),
                "kind": kind,
                "api_version": object_data.get("apiVersion"),
            },
            "conditions": _normalize_conditions(status.get("conditions", [])),
            "related_resources": _extract_related_resources(object_data),
            "events": [_normalize_event(event) for event in collected.events],
            "logs": collected.logs,
        }
        crossplane_owner = _extract_crossplane_owner(metadata)
        if crossplane_owner:
            scenario["crossplane_owner"] = crossplane_owner
        return scenario

    pod = _select_primary_pod(collected.related_pods)
    pod_status = _build_pod_status(pod)
    deployment_status = _build_workload_status(kind=kind, status=status)
    event_items = [_normalize_event(event) for event in collected.events]

    return {
        "scenario_type": "kubernetes_workload",
        "metadata": {
            "name": metadata.get("name", "unknown"),
            "namespace": metadata.get("namespace", "default"),
            "kind": kind,
            "api_version": object_data.get("apiVersion"),
        },
        "deployment_status": deployment_status,
        "pod_status": pod_status,
        "events": event_items,
        "logs": collected.logs,
    }


def _collect_related_pods(core_api: Any, *, kind: str, namespace: str, object_data: dict[str, Any]) -> list[dict[str, Any]]:
    normalized_kind = kind.lower()
    if normalized_kind == "pod":
        return [object_data]

    selector = _label_selector_from_object(object_data)
    if not selector:
        return []

    pods = core_api.list_namespaced_pod(namespace=namespace, label_selector=selector)
    return [item.to_dict() for item in pods.items]


def _collect_events(
    core_api: Any,
    *,
    namespace: str,
    object_data: dict[str, Any],
    related_pods: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    involved_uids = {object_data.get("metadata", {}).get("uid")}
    involved_uids.update(pod.get("metadata", {}).get("uid") for pod in related_pods)
    involved_uids.discard(None)

    for uid in involved_uids:
        response = core_api.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.uid={uid}",
        )
        events.extend(item.to_dict() for item in response.items)

    events.sort(key=lambda event: event.get("last_timestamp") or event.get("event_time") or "")
    return events


def _collect_logs(
    core_api: Any,
    *,
    namespace: str,
    related_pods: list[dict[str, Any]],
    target_kind: str,
    target_name: str,
    container: str | None,
    tail_lines: int,
) -> list[str]:
    pod = _select_primary_pod(related_pods)
    if not pod:
        return []

    pod_name = pod.get("metadata", {}).get("name")
    if not pod_name:
        return []

    selected_container = container or _default_container_name(pod)
    try:
        log_text = core_api.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=selected_container,
            tail_lines=tail_lines,
        )
    except Exception:
        return [
            f"Unable to fetch logs for {target_kind}/{target_name} from pod {pod_name}.",
        ]

    return [line for line in log_text.splitlines() if line.strip()]


def _default_container_name(pod: dict[str, Any]) -> str | None:
    containers = pod.get("spec", {}).get("containers", [])
    if not containers:
        return None
    return containers[0].get("name")


def _label_selector_from_object(object_data: dict[str, Any]) -> str:
    match_labels = (
        object_data.get("spec", {})
        .get("selector", {})
        .get("matchLabels", {})
    )
    if not match_labels:
        return ""
    return ",".join(f"{key}={value}" for key, value in sorted(match_labels.items()))


def _select_primary_pod(pods: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pods:
        return None

    def score(pod: dict[str, Any]) -> tuple[int, int]:
        status = pod.get("status", {})
        container_statuses = status.get("containerStatuses", [])
        restart_sum = sum(item.get("restartCount", 0) for item in container_statuses)
        has_waiting = any(item.get("state", {}).get("waiting") for item in container_statuses)
        return (1 if has_waiting else 0, restart_sum)

    return max(pods, key=score)


def _build_pod_status(pod: dict[str, Any] | None) -> dict[str, Any]:
    if not pod:
        return {
            "phase": "Unknown",
            "restart_count": 0,
            "container_state": {},
        }

    status = pod.get("status", {})
    container_status = (status.get("containerStatuses") or [{}])[0]
    state = container_status.get("state", {})
    last_state = container_status.get("lastState", {})

    container_state: dict[str, Any] = {}
    if state.get("waiting"):
        container_state["waiting"] = {
            "reason": state["waiting"].get("reason", ""),
        }
    if last_state.get("terminated"):
        container_state["last_terminated"] = {
            "reason": last_state["terminated"].get("reason", ""),
            "exit_code": last_state["terminated"].get("exitCode"),
        }

    return {
        "phase": status.get("phase", "Unknown"),
        "restart_count": container_status.get("restartCount", 0),
        "container_state": container_state,
    }


def _build_workload_status(*, kind: str, status: dict[str, Any]) -> dict[str, Any]:
    normalized_kind = kind.lower()
    if normalized_kind == "deployment":
        conditions = status.get("conditions", [])
        progressing_condition = next(
            (item for item in conditions if item.get("type") == "Progressing"),
            {},
        )
        return {
            "desired_replicas": status.get("replicas", 0),
            "available_replicas": status.get("availableReplicas", 0),
            "progressing": progressing_condition.get("status") == "True",
            "condition_reason": progressing_condition.get("reason", ""),
        }

    return {
        "desired_replicas": status.get("replicas", 0),
        "available_replicas": status.get("availableReplicas", 0),
    }


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": event.get("type", ""),
        "reason": event.get("reason", ""),
        "message": event.get("message", ""),
    }


def _normalize_conditions(conditions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in conditions:
        normalized.append(
            {
                "type": item.get("type", ""),
                "status": item.get("status", ""),
                "reason": item.get("reason", ""),
                "message": item.get("message", ""),
            }
        )
    return normalized


def _extract_crossplane_owner(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Return Crossplane Object owner info if the resource is wrapped by one.

    Patching the inner CR directly does not stick when ownership is held by a
    Crossplane provider-kubernetes Object; the Object is the source of truth
    and will revert changes. This helper surfaces the owning Object's identity
    so the analyzer can suggest patching the right resource.
    """
    owner_references = metadata.get("ownerReferences") or metadata.get("owner_references") or []
    namespace = metadata.get("namespace", "default")
    for ref in owner_references:
        if not isinstance(ref, dict):
            continue
        api_version = ref.get("apiVersion") or ref.get("api_version") or ""
        kind = ref.get("kind", "")
        name = ref.get("name", "")
        if (
            isinstance(api_version, str)
            and api_version.startswith("kubernetes.crossplane.io/")
            and kind == "Object"
            and name
        ):
            return {
                "name": name,
                "namespace": namespace,
                "api_version": api_version,
                "kind": "Object",
            }
    return None


def _extract_related_resources(object_data: dict[str, Any]) -> list[dict[str, Any]]:
    spec = object_data.get("spec", {})
    status = object_data.get("status", {})
    related: list[dict[str, Any]] = []

    def append_ref(ref: dict[str, Any]) -> None:
        if not isinstance(ref, dict):
            return
        name = ref.get("name")
        kind = ref.get("kind")
        api_version = ref.get("apiVersion") or ref.get("api_version")
        namespace = ref.get("namespace")
        if name or kind:
            related.append(
                {
                    "name": name,
                    "kind": kind,
                    "api_version": api_version,
                    "namespace": namespace,
                }
            )

    for key in ("resourceRefs", "dependencies"):
        value = spec.get(key) or status.get(key)
        if isinstance(value, list):
            for item in value:
                append_ref(item)

    claim_ref = spec.get("claimRef") or status.get("claimRef")
    if isinstance(claim_ref, dict):
        append_ref(claim_ref)

    provider_config_ref = spec.get("providerConfigRef")
    if isinstance(provider_config_ref, dict):
        append_ref(
            {
                "name": provider_config_ref.get("name"),
                "kind": "ProviderConfig",
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for item in related:
        key = (item.get("kind"), item.get("name"), item.get("namespace"))
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped
