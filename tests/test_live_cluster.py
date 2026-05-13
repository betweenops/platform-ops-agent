import unittest

from platform_ops_agent.live_cluster import CollectedWorkload, build_workload_scenario


class LiveClusterTests(unittest.TestCase):
    def test_build_workload_scenario_uses_waiting_pod_and_events(self) -> None:
        collected = CollectedWorkload(
            object_data={
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "demo-api", "namespace": "demo"},
                "spec": {"selector": {"matchLabels": {"app": "demo-api"}}},
                "status": {
                    "replicas": 2,
                    "availableReplicas": 0,
                    "conditions": [
                        {
                            "type": "Progressing",
                            "status": "False",
                            "reason": "ProgressDeadlineExceeded",
                        }
                    ],
                },
            },
            related_pods=[
                {
                    "metadata": {"name": "demo-api-1"},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {
                                "restartCount": 4,
                                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                "lastState": {"terminated": {"reason": "Error", "exitCode": 1}},
                            }
                        ],
                    },
                    "spec": {"containers": [{"name": "app"}]},
                }
            ],
            events=[
                {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container"},
            ],
            logs=["fatal startup error"],
        )

        scenario = build_workload_scenario(collected)

        self.assertEqual(scenario["metadata"]["name"], "demo-api")
        self.assertEqual(
            scenario["pod_status"]["container_state"]["waiting"]["reason"],
            "CrashLoopBackOff",
        )
        self.assertEqual(
            scenario["deployment_status"]["condition_reason"],
            "ProgressDeadlineExceeded",
        )
        self.assertEqual(scenario["events"][0]["reason"], "BackOff")

    def test_build_workload_scenario_maps_custom_resource_conditions(self) -> None:
        collected = CollectedWorkload(
            object_data={
                "apiVersion": "platform.example.io/v1alpha1",
                "kind": "PlatformInstallation",
                "metadata": {"name": "site-a", "namespace": "ops-system"},
                "spec": {
                    "resourceRefs": [
                        {
                            "apiVersion": "platform.example.io/v1alpha1",
                            "kind": "BootProvisioning",
                            "name": "boot-prep-a",
                        }
                    ]
                },
                "status": {
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "False",
                            "reason": "DependencyNotReady",
                            "message": "BootProvisioning/boot-prep-a is not yet ready.",
                        }
                    ]
                },
            },
            related_pods=[],
            events=[
                {
                    "type": "Warning",
                    "reason": "DependencyNotReady",
                    "message": "Waiting for BootProvisioning/boot-prep-a",
                }
            ],
            logs=[],
        )

        scenario = build_workload_scenario(collected)

        self.assertEqual(scenario["scenario_type"], "custom_resource")
        self.assertEqual(scenario["conditions"][0]["type"], "Ready")
        self.assertEqual(scenario["related_resources"][0]["name"], "boot-prep-a")
        self.assertEqual(scenario["events"][0]["reason"], "DependencyNotReady")


if __name__ == "__main__":
    unittest.main()
