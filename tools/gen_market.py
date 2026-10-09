#!/usr/bin/env python3
"""Generate market.json, the catalog of the module market (backend/invasor/market.py):

    python3 tools/gen_market.py --conf <repos.conf> --out <market.json>

For every module repository in repos.conf it records the newest stable release and the newest one
pre-releases included, read exactly as the app reads them (Market.entry). The workspace repository's
GitHub Action runs it every hour and publishes the file on its market-data branch; the app reads it
instead of asking GitHub repository by repository. The file carries no URLs: the app rebuilds them.

GITHUB_TOKEN (optional) authenticates the calls to api.github.com: 1000 an hour instead of 60.
Nothing is written when the catalog would be wrong: no module at all, or a repository whose release
couldn't be read because GitHub failed (the next run tries again). A repository that simply has no
release yet, or a release that is malformed, is left out and noted under "skipped".
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.dont_write_bytecode = True

from invasor import market, updater  # noqa: E402

CARD_KEYS = ("id", "version", "tag", "name", "description", "author", "min_core")


def transient(reason):
    """True when a repository's reason for having no card is GitHub (or the network) failing, not the
    repository's own content."""
    return reason.startswith(("GitHub answered", market.RATE_LIMITED, "error:")) or reason.startswith("module.json answered 5")


def build_catalog(repos, entry, now):
    """({schema, generated, modules, skipped}, [transient problems]). `entry(repo, beta)` is Market.entry:
    a card (dict) or the reason there is none (str)."""
    modules, skipped, problems = [], [], []
    for repo in repos:
        cards, reasons = {}, []
        for channel, beta in (("stable", False), ("beta", True)):
            res = entry(repo, beta)
            if isinstance(res, dict):
                cards[channel] = {k: res[k] for k in CARD_KEYS}
            else:
                cards[channel] = None
                if res != market.NO_RELEASE:
                    reasons.append(res)
                if transient(res):
                    problems.append(f"{repo} ({channel}): {res}")
        if cards["stable"] is None and cards["beta"] is None:
            skipped.append(f"{repo}: {reasons[0] if reasons else market.NO_RELEASE}")
        else:
            modules.append({"repo": repo, **cards})
    catalog = {
        "schema": market.FILE_SCHEMA,
        "generated": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "modules": modules,
        "skipped": skipped,
    }
    return catalog, problems


def authenticated_fetch(token):
    def fetch(url):
        req = updater._request(url)
        if token and url.startswith("https://api.github.com/"):
            req.add_header("Authorization", f"Bearer {token}")
        with updater._opener().open(req, timeout=updater.TIMEOUT) as r:
            data = r.read(updater.MAX_JSON + 1)
        if len(data) > updater.MAX_JSON:
            raise ValueError("answer too large")
        return json.loads(data)

    return fetch


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate market.json from repos.conf")
    ap.add_argument("--conf", required=True, type=Path, help="the workspace's repos.conf")
    ap.add_argument("--out", required=True, type=Path, help="where to write market.json")
    args = ap.parse_args(argv)

    repos = market.parse_repos_conf(args.conf.read_text())
    m = market.Market(fetch=authenticated_fetch(os.environ.get("GITHUB_TOKEN")))

    def entry(repo, beta):
        try:
            return m.entry(repo, beta)
        except Exception as e:  # network down, odd answer…: transient, never a reason to publish less
            return f"error: {e}"

    catalog, problems = build_catalog(repos, entry, datetime.now(timezone.utc))
    if problems:
        sys.exit("not writing market.json, GitHub failed for:\n  " + "\n  ".join(problems))
    if not catalog["modules"]:
        sys.exit("not writing market.json: no repository has a module release")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_name(args.out.name + ".tmp")
    tmp.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, args.out)
    print(f"{args.out}: {len(catalog['modules'])} modules, {len(catalog['skipped'])} skipped")
    for note in catalog["skipped"]:
        print(f"  skipped {note}")


if __name__ == "__main__":
    main()
