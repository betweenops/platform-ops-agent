import unittest

from platform_ops_agent.analyzer import analyze_scenario, load_scenario


class AnalyzerTests(unittest.TestCase):
    def test_crashloop_reports_failed_health(self) -> None:
        report = analyze_scenario(load_scenario("crashloop-api"))

        self.assertEqual(report["health"], "failed")
        self.assertTrue(any("crashing" in signal.lower() for signal in report["signals"]))

    def test_image_pull_reports_registry_issue(self) -> None:
        report = analyze_scenario(load_scenario("image-pull-backoff"))

        self.assertEqual(report["health"], "failed")
        self.assertTrue(any("registry" in cause.lower() for cause in report["likely_causes"]))

    def test_failed_scheduling_reports_capacity_issue(self) -> None:
        report = analyze_scenario(load_scenario("failed-scheduling"))

        self.assertTrue(any("capacity" in signal.lower() for signal in report["signals"]))

    def test_ansible_provisioning_failure_reports_registry_context(self) -> None:
        report = analyze_scenario(load_scenario("ansible-provisioning-artifact-failure"))

        self.assertEqual(report["health"], "failed")
        self.assertEqual(report["operator_context"]["playbook_family"], "provisioning")
        self.assertIn("internal registry", report["task_intent"])
        self.assertTrue(any("registry" in signal.lower() for signal in report["signals"]))

    def test_ansible_airgap_wait_loop_mentions_hidden_artifact_cause(self) -> None:
        report = analyze_scenario(load_scenario("ansible-airgap-wait-loop"))

        self.assertEqual(report["health"], "failed")
        self.assertEqual(report["operator_context"]["environment_mode"], "airgap")
        self.assertTrue(any("downstream symptom" in signal.lower() for signal in report["signals"]))
        self.assertTrue(any("boot images" in cause.lower() or "initrd" in cause.lower() for cause in report["likely_causes"]))

    def test_custom_resource_failure_uses_conditions_and_dependencies(self) -> None:
        report = analyze_scenario(
            {
                "scenario_type": "custom_resource",
                "metadata": {
                    "name": "site-a",
                    "namespace": "ops-system",
                    "kind": "PlatformInstallation",
                },
                "conditions": [
                    {
                        "type": "Ready",
                        "status": "False",
                        "reason": "DependencyNotReady",
                        "message": "BootProvisioning/boot-prep-a is not yet ready.",
                    }
                ],
                "related_resources": [
                    {"kind": "BootProvisioning", "name": "boot-prep-a"},
                ],
                "events": [
                    {
                        "type": "Warning",
                        "reason": "DependencyNotReady",
                        "message": "Waiting for BootProvisioning/boot-prep-a",
                    }
                ],
                "logs": [],
            }
        )

        self.assertEqual(report["health"], "failed")
        self.assertEqual(report["operator_context"]["primary_condition_type"], "Ready")
        self.assertIn("BootProvisioning/boot-prep-a", report["operator_context"]["related_resources"])


if __name__ == "__main__":
    unittest.main()
