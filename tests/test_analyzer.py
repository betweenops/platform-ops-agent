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

    def test_edgeops_pxe_failure_reports_nexus_context(self) -> None:
        report = analyze_scenario(load_scenario("edgeops-pxe-nexus-failure"))

        self.assertEqual(report["health"], "failed")
        self.assertEqual(report["operator_context"]["playbook_family"], "pxe")
        self.assertIn("Nexus", report["task_intent"])
        self.assertTrue(any("nexus" in signal.lower() for signal in report["signals"]))

    def test_edgeops_airgap_wait_loop_mentions_hidden_artifact_cause(self) -> None:
        report = analyze_scenario(load_scenario("edgeops-airgap-blade-wait-loop"))

        self.assertEqual(report["health"], "failed")
        self.assertEqual(report["operator_context"]["environment_mode"], "airgap")
        self.assertTrue(any("downstream symptom" in signal.lower() for signal in report["signals"]))
        self.assertTrue(any("boot images" in cause.lower() or "initrd" in cause.lower() for cause in report["likely_causes"]))


if __name__ == "__main__":
    unittest.main()
