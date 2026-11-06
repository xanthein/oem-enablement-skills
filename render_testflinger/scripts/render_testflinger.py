#!/usr/bin/env python3
"""Render testflinger job YAML files from Jinja2 templates."""

import argparse
import json
import sys
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader, TemplateNotFound
except ImportError:
    print("Error: jinja2 is not installed. Run: pip install jinja2", file=sys.stderr)
    sys.exit(1)


TEST_TEMPLATE = "test_job.yaml.j2"
PROVISION_TEMPLATE = "provision_job.yaml.j2"
PROVISION_AND_TEST_TEMPLATE = "provision_and_test_job.yaml.j2"


def get_env(template_path: Path) -> "Environment":
    return Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def load_manifest(path: str | None) -> dict:
    """Load manifest key-value pairs from a JSON file.

    Returns an empty dict if no path is given.
    """
    if not path:
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except OSError as e:
        print(f"Error reading manifest file: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing manifest JSON: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print("Error: manifest JSON must be a top-level object", file=sys.stderr)
        sys.exit(1)
    return data


def render(template_path: Path, variables: dict) -> str:
    env = get_env(template_path)
    try:
        template = env.get_template(template_path.name)
    except TemplateNotFound:
        print(f"Error: could not load template: {template_path}", file=sys.stderr)
        sys.exit(1)
    return template.render(**variables)


def write_output(content: str, output: str) -> None:
    if output == "-":
        print(content)
    else:
        out_path = Path(output)
        out_path.write_text(content)
        print(f"Written to {out_path}", file=sys.stderr)


def resolve_template(args_template, default_name: str) -> Path:
    if args_template:
        path = Path(args_template).resolve()
    else:
        path = Path(__file__).parent / default_name
    if not path.exists():
        print(f"Error: template file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def cmd_test(args) -> None:
    template_path = resolve_template(args.template, TEST_TEMPLATE)
    content = render(template_path, {
        "job_queue": args.job_queue,
        "unit": args.unit,
        "manifest": load_manifest(args.manifest),
    })
    write_output(content, args.output)


def cmd_provision_and_test(args) -> None:
    template_path = resolve_template(args.template, PROVISION_AND_TEST_TEMPLATE)
    content = render(template_path, {
        "job_queue": args.job_queue,
        "provision_url": args.url,
        "unit": args.unit,
        "manifest": load_manifest(args.manifest),
    })
    write_output(content, args.output)


def cmd_provision(args) -> None:
    template_path = resolve_template(args.template, PROVISION_TEMPLATE)
    content = render(template_path, {"job_queue": args.job_queue, "provision_url": args.url})
    write_output(content, args.output)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-q", "--job-queue",
        required=True,
        help="Job queue name",
    )
    parser.add_argument(
        "-o", "--output",
        default="-",
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "-t", "--template",
        default=None,
        help="Path to a custom Jinja2 template file",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Render a Testflinger job YAML from a Jinja2 template.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # test subcommand
    test_parser = subparsers.add_parser(
        "test",
        help="Render a test job (uses testflinger-cli.yaml.j2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -q 202602-38401 -u com.canonical.certification::stress-warmboot-coldboot-automated
  %(prog)s -q my-queue -u com.canonical.certification::smoke -o job.yaml
""",
    )
    add_common_args(test_parser)
    test_parser.add_argument(
        "-u", "--unit",
        required=True,
        help="Checkbox test plan unit",
    )
    test_parser.add_argument(
        "--manifest",
        default=None,
        metavar="FILE",
        help="Path to a JSON file with [manifest] key-value pairs",
    )
    test_parser.set_defaults(func=cmd_test)

    # provision subcommand
    provision_parser = subparsers.add_parser(
        "provision",
        help="Render a provision job (uses provision_job.yaml.j2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -q 202512-38249 --url https://example.com/image.iso
  %(prog)s -q my-queue --url https://example.com/image.iso -o provision.yaml
""",
    )
    add_common_args(provision_parser)
    provision_parser.add_argument(
        "--url",
        required=True,
        help="ISO image URL for provisioning",
    )
    provision_parser.set_defaults(func=cmd_provision)

    # provision-and-test subcommand
    pat_parser = subparsers.add_parser(
        "provision-and-test",
        help="Render a combined provision+test job (uses provision_and_test_job.yaml.j2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -q 202602-38401 --url https://example.com/image.iso -u com.canonical.certification::smoke
  %(prog)s -q my-queue --url https://example.com/image.iso -u com.canonical.certification::stress-warmboot-coldboot-automated -o job.yaml
""",
    )
    add_common_args(pat_parser)
    pat_parser.add_argument(
        "--url",
        required=True,
        help="ISO image URL for provisioning",
    )
    pat_parser.add_argument(
        "-u", "--unit",
        required=True,
        help="Checkbox test plan unit",
    )
    pat_parser.add_argument(
        "--manifest",
        default=None,
        metavar="FILE",
        help="Path to a JSON file with [manifest] key-value pairs",
    )
    pat_parser.set_defaults(func=cmd_provision_and_test)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
