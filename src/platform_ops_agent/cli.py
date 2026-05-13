from __future__ import annotations

import argparse
import json
import sys

from platform_ops_agent.analyzer import (
    analyze_scenario,
    available_scenarios,
    load_scenario,
    render_text_report,
)
from platform_ops_agent.live_cluster import (
    build_workload_scenario,
    collect_live_workload,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-ops-agent",
        description="Analyze replayable Kubernetes troubleshooting scenarios.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-scenarios", help="List bundled example scenarios.")

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze a bundled scenario name or a path to a scenario JSON file.",
    )
    analyze_parser.add_argument("scenario", help="Fixture name or path to JSON scenario.")
    analyze_parser.add_argument(
        "--json",
        action="store_true",
        help="Render the full analysis report as JSON.",
    )

    live_parser = subparsers.add_parser(
        "analyze-live",
        help="Collect live Kubernetes data for a workload and analyze it.",
    )
    live_parser.add_argument("--api-version", required=True, help="Target API version, for example apps/v1.")
    live_parser.add_argument("--kind", required=True, help="Target kind, for example Deployment or Pod.")
    live_parser.add_argument("--namespace", required=True, help="Target namespace.")
    live_parser.add_argument("--name", required=True, help="Target object name.")
    live_parser.add_argument("--container", help="Optional container name when collecting pod logs.")
    live_parser.add_argument("--context", help="Optional kubeconfig context name.")
    live_parser.add_argument("--kubeconfig", help="Optional kubeconfig path.")
    live_parser.add_argument(
        "--tail-lines",
        type=int,
        default=100,
        help="Number of pod log lines to fetch. Defaults to 100.",
    )
    live_parser.add_argument(
        "--json",
        action="store_true",
        help="Render the full analysis report as JSON.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "list-scenarios":
        for scenario in available_scenarios():
            print(scenario)
        return 0

    if args.command == "analyze":
        try:
            scenario = load_scenario(args.scenario)
        except FileNotFoundError:
            parser.error(f"scenario not found: {args.scenario}")
        report = analyze_scenario(scenario)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(render_text_report(report))
        return 0

    if args.command == "analyze-live":
        try:
            collected = collect_live_workload(
                api_version=args.api_version,
                kind=args.kind,
                namespace=args.namespace,
                name=args.name,
                container=args.container,
                context=args.context,
                kubeconfig=args.kubeconfig,
                tail_lines=args.tail_lines,
            )
        except Exception as exc:
            parser.error(str(exc))

        report = analyze_scenario(build_workload_scenario(collected))
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(render_text_report(report))
        return 0

    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
