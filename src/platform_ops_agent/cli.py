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

    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
