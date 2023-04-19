import argparse
import csv
import functools
import json
import multiprocessing
import os
import pathlib
import subprocess
import sys
import time
from urllib.parse import urlparse


ROLE_METAVARIABLES = ["$AUTHZ", "$...AUTHZ"]
JS_TS_LANGUAGE = "JavaScript/TypeScript"
SHORT_HASH_LEN = 7

HARNESS = {
    "Python": {
        "django": [
            "https://github.com/DefectDojo/django-DefectDojo",
            "https://github.com/saleor/saleor",
            "https://github.com/wagtail/wagtail",
        ],
        "django-rest-framework": [
            "https://github.com/DefectDojo/django-DefectDojo",
        ],
        "flask": [
            "https://github.com/apache/airflow",
            "https://github.com/flaskbb/flaskbb",
            "https://github.com/getredash/redash",
        ],
        "sanic": [
            "https://github.com/howie6879/owllook",
            "https://github.com/jacebrowning/memegen",
        ],
    },
    "PHP": {
        "laravel": [
            "https://github.com/monicahq/monica",
            "https://github.com/koel/koel",
            "https://github.com/BookStackApp/BookStack",
        ],
        "symfony": [
            "https://github.com/Sylius/Sylius",
            "https://github.com/sulu/sulu",
            "https://github.com/bolt/core",
        ],
        "cakephp": [
            "https://github.com/passbolt/passbolt_api",
            "https://github.com/croogo/croogo",
        ],
    },
    "Ruby": {
        "rails": [
            "https://github.com/discourse/discourse",
            "https://github.com/gitlabhq/gitlabhq",
            "https://github.com/diaspora/diaspora",
        ],
        "grape": [
            "https://github.com/locomotivecms/engine",
            "https://github.com/gitlabhq/gitlabhq",
            "https://github.com/Mapotempo/optimizer-api",
        ],
    },
    "Java": {
        "spring": [
            "https://github.com/thingsboard/thingsboard",
            "https://github.com/macrozheng/mall",
            "https://github.com/sqshq/piggymetrics",
        ],
        "jax-rs": [
            "https://github.com/DependencyTrack/dependency-track",
            "https://github.com/eclipse/kura",
            "https://github.com/eclipse/kapua",
        ],
    },
    "Go": {
        "gorilla": [
            "https://github.com/portainer/portainer",
            "https://github.com/google/exposure-notifications-server",
        ],
        "gin": [
            "https://github.com/photoprism/photoprism",
            "https://github.com/go-admin-team/go-admin",
            "https://github.com/gotify/server",
        ],
        "chi": [
            "https://github.com/dhax/go-base",
            "https://github.com/cloudfoundry/korifi",
        ],
    },
    JS_TS_LANGUAGE: {
        "express": [
            "https://github.com/payloadcms/payload",
            "https://github.com/directus/directus",
        ],
        "react": [
            "https://github.com/elastic/kibana",
            "https://github.com/mattermost/mattermost-webapp",
            "https://github.com/apache/superset",
        ],
        "angular": [
            "https://github.com/Chocobozzz/PeerTube",
            "https://github.com/bitwarden/clients",
            "https://github.com/ever-co/ever-demand",
        ],
    },
}


stderr = functools.partial(print, file=sys.stderr)


def run_cmd(*args, cwd=None):
    try:
        proc = subprocess.run(args, capture_output=True, encoding="utf-8", cwd=cwd)
    except FileNotFoundError:
        stderr(f"Failed to run {args[0]}, please install {args[0]} and try again")
        sys.exit(1)

    if proc.returncode != os.EX_OK:
        stderr(
            f"Running {args} returned code {proc.returncode} and stderr {proc.stderr}"
        )
        sys.exit(1)

    return proc.stdout


def get_org_repo(url):
    parsed = urlparse(url)
    _, org, repo = parsed.path.split("/")
    return org, repo


def process_output(filepath):
    stderr(f"Processing {filepath}")

    data = json.load(filepath.open())

    languages = (
        ["JavaScript", "TypeScript"]
        if data["language"] == JS_TS_LANGUAGE
        else [data["language"]]
    )
    language_loc = sum(
        data["tokei"][language][key]
        for language in languages
        for key in ["blanks", "code", "comments"]
    )
    route_count = sum(
        int("-route" in result["check_id"]) for result in data["semgrep"]["results"]
    )
    authenticated_count = sum(
        int("-authenticated" in result["check_id"])
        for result in data["semgrep"]["results"]
    )
    unauthenticated_count = sum(
        int("-unauthenticated" in result["check_id"])
        for result in data["semgrep"]["results"]
    )
    authorized_count = sum(
        int("-authorized" in result["check_id"])
        for result in data["semgrep"]["results"]
    )
    unauthorized_count = sum(
        int("-unauthorized" in result["check_id"])
        for result in data["semgrep"]["results"]
    )
    role_count = len(
        {
            result["extra"]["metavars"][metavariable]["abstract_content"]
            for result in data["semgrep"]["results"]
            for metavariable in ROLE_METAVARIABLES
            if metavariable in result["extra"]["metavars"]
        }
    )

    name = "/".join(get_org_repo(data["repository"]))
    commit_hash = data["hash"][:SHORT_HASH_LEN]

    return [
        name,
        commit_hash,
        data["framework"],
        data["language"],
        str(language_loc),
        str(data["runtime"]),
        str(route_count),
        str(authenticated_count),
        str(unauthenticated_count),
        str(authorized_count),
        str(unauthorized_count),
        str(role_count),
    ]


def analyze_repository(harness_dir, output_dir, language, framework, repository):
    stderr(f"Analyzing {language}, {framework}, {repository}")

    org, repo = get_org_repo(repository)
    target_dir = harness_dir / repo
    target_abs = target_dir.resolve(strict=True)

    if not target_dir.exists():
        stderr(f"Cloning repository {repository}")
        harness_abs = harness_dir.resolve(strict=True)
        run_cmd("git", "clone", repository, cwd=harness_abs)

    repository_hash = run_cmd("git", "rev-parse", "HEAD", cwd=target_abs).strip()
    stderr(f"Repository hash {repository_hash}")

    tokei_output = run_cmd("tokei", "--output", "json", cwd=target_abs)
    tokei_json = json.loads(tokei_output)
    semgrep_config = run_cmd("routes", "which", framework)

    # Create an empty ignore file so we don't skip files (e.g. tests)
    semgrepignore_path = target_abs / ".semgrepignore"
    run_cmd("touch", semgrepignore_path.resolve())

    stderr(f"Running Semgrep against {target_abs} with framework {framework}")
    start_time = time.monotonic()
    semgrep_output = run_cmd(
        "semgrep", "--json", "--config", semgrep_config, cwd=target_abs
    )
    end_time = time.monotonic()
    runtime = round(end_time - start_time, 2)
    semgrep_json = json.loads(semgrep_output)
    stderr(f"Finished Semgrep in {runtime}s, received {len(semgrep_output)} bytes")

    output_file = f"{repo}.{framework}.json"
    output_path = output_dir / output_file
    output = {
        "language": language,
        "framework": framework,
        "repository": repository,
        "hash": repository_hash,
        "tokei": tokei_json,
        "semgrep": semgrep_json,
        "runtime": runtime,
    }
    json.dump(output, output_path.open(mode="w"))


def parse_args():
    p = argparse.ArgumentParser(
        description="Evaluate route-detect against dependent codebases",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--harness-dir",
        action="store",
        default="harness",
        help="Clone test harness code to this directory",
    )
    p.add_argument(
        "--output-dir",
        action="store",
        default="output",
        help="Output Semgrep results to this directory",
    )
    p.add_argument(
        "-r",
        "--repos",
        action="store",
        nargs="+",
        help="Only include matching repositories",
    )

    action_group = p.add_mutually_exclusive_group(required=True)
    action_group.add_argument(
        "--analyze",
        action="store_true",
        help="Clone dependent repositories and run analysis against each",
    )
    action_group.add_argument(
        "--process",
        action="store_true",
        help="Process analysis results and output evaluation metrics",
    )

    return p.parse_args()


def main():
    args = parse_args()

    harness_dir = pathlib.Path(args.harness_dir)
    harness_dir.mkdir(exist_ok=True)

    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    if args.analyze:
        analyses = [
            (language, framework, repository)
            for language, frameworks in HARNESS.items()
            for framework, repositories in frameworks.items()
            for repository in repositories
            if not args.repos or any(repo in repository for repo in args.repos)
        ]
        # No need for multiprocessing here, Semgrep will already saturate the CPU
        for language, framework, repository in analyses:
            analyze_repository(harness_dir, output_dir, language, framework, repository)
    elif args.process:
        outputs = output_dir.glob("*.json")

        if args.repos:
            outputs = [
                output
                for output in outputs
                if any(repo in output for repo in args.repos)
            ]

        with multiprocessing.Pool(multiprocessing.cpu_count()) as pool:
            results = pool.map(process_output, outputs)

        # Sort on language then framework
        results.sort(key=lambda r: r[3] + r[2])

        headers = [
            "Repository",
            "Commit hash",
            "Framework",
            "Language",
            "Lines of code",
            "Semgrep runtime",
            "Route count",
            "Authn route count",
            "Unauthn route count",
            "Authz route count",
            "Unauthz route count",
            "Role count",
        ]
        mismatch = any(len(headers) != len(result) for result in results)
        if mismatch:
            stderr("CSV header/row mismatch")
            return 1

        csv_out = csv.writer(sys.stdout)
        csv_out.writerows([headers] + results)
    else:
        raise ValueError("Missing required action argument")

    return os.EX_OK


if __name__ == "__main__":
    sys.exit(main())
