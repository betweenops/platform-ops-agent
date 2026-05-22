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

    def test_dependency_gate_only_reports_waiting_not_failed(self) -> None:
        report = analyze_scenario(load_scenario("ansible-dependency-gate-waiting"))

        self.assertEqual(report["health"], "waiting")
        self.assertTrue(any("gating" in signal.lower() or "idle" in signal.lower() for signal in report["signals"]))
        self.assertIn("dependency_wait_targets", report["evidence"])
        self.assertIn("NodeConfiguration/node-config-a", report["evidence"]["dependency_wait_targets"])

    def test_dependency_gate_plus_real_failure_still_fails(self) -> None:
        # Synthesize: check_dependencies gate AND a Nexus registry failure.
        # The real failure should dominate and health should remain "failed".
        report = analyze_scenario(
            {
                "scenario_type": "ansible_operator_failure",
                "metadata": {
                    "name": "boot-prep-x",
                    "namespace": "ops-system",
                    "kind": "BootProvisioning",
                    "group": "automation.example.io",
                },
                "operator_context": {
                    "controller": "automation-controller",
                    "playbook": "playbooks/provisioning/provisioning.yml",
                    "playbook_family": "provisioning",
                },
                "ansible_failure": {
                    "playbook_family": "provisioning",
                    "host": "controller-node",
                    "task_name": "check for dns image in registry",
                    "task_file": "roles/check_dependencies/tasks/main.yml",
                    "module": "ansible.builtin.uri",
                    "message": "Failed to fetch from nexus",
                    "stderr": "fatal: connection refused",
                },
                "logs": [
                    "TASK [check_dependencies : check if all conditions match] ****",
                    "fatal: [controller-node]: FAILED! => {\"msg\": [\"All conditions do not match True False Running \"]}",
                    "TASK [provisioning : check for dns image in registry] ********",
                    "fatal: [controller-node]: FAILED! => {\"msg\": \"Failed to fetch from nexus\"}",
                ],
            }
        )

        self.assertEqual(report["health"], "failed")
        self.assertTrue(any("registry" in cause.lower() for cause in report["likely_causes"]))

    def test_redfish_post_busy_reports_blocked_not_failed(self) -> None:
        report = analyze_scenario(load_scenario("ansible-redfish-post-busy"))

        self.assertEqual(report["health"], "blocked")
        self.assertEqual(
            report["evidence"]["redfish_message_id"],
            "iLO.2.25.UnableToModifyDuringSystemPOST",
        )
        self.assertTrue(
            any("human" in step.lower() or "console" in step.lower() for step in report["next_steps"])
        )
        self.assertIn("redfish_endpoint", report["operator_context"])

    def test_pxe_stale_dhcp_reports_missing_reservations(self) -> None:
        report = analyze_scenario(load_scenario("ansible-pxe-stale-dhcp-reservations"))

        self.assertEqual(report["health"], "failed")
        self.assertEqual(report["evidence"]["dhcp_expected_systems"], 4)
        self.assertEqual(report["evidence"]["dhcp_rendered_reservations"], 3)
        self.assertIn("node-04.internal.example.invalid", report["evidence"]["dhcp_missing_hosts"])
        self.assertTrue(
            any("this_ansible_run_id" in step for step in report["next_steps"])
        )

    def test_tftp_wedged_session_reports_degraded_and_restart_step(self) -> None:
        report = analyze_scenario(load_scenario("ansible-tftp-wedged-session"))

        self.assertEqual(report["health"], "degraded")
        self.assertEqual(report["evidence"]["peer_success_count"], 2)
        self.assertTrue(
            any("docker restart" in step.lower() for step in report["next_steps"])
        )

    def test_wait_for_connection_without_tftp_signature_still_uses_airgap_rule(self) -> None:
        # Regression: existing airgap fixture must not be hijacked by the new TFTP rule.
        report = analyze_scenario(load_scenario("ansible-airgap-wait-loop"))

        self.assertEqual(report["health"], "failed")
        self.assertTrue(any("downstream symptom" in signal.lower() for signal in report["signals"]))
        # Ensure the wedged-session rule did not trigger.
        self.assertNotIn("peer_success_count", report["evidence"])

    def test_custom_resource_analysis_surfaces_crossplane_owner(self) -> None:
        report = analyze_scenario(load_scenario("custom-resource-with-crossplane-owner"))

        self.assertEqual(
            report["operator_context"]["crossplane_owner_object"],
            "ops-system/boot-prep-a-object",
        )
        self.assertEqual(
            report["operator_context"]["crossplane_owner_api_version"],
            "kubernetes.crossplane.io/v1alpha2",
        )
        self.assertTrue(
            any("this_ansible_run_id" in step for step in report["next_steps"])
        )
        self.assertTrue(
            any("reverted" in signal.lower() or "object" in signal.lower() for signal in report["signals"])
        )


if __name__ == "__main__":
    unittest.main()
