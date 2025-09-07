#!/usr/bin/env python3
"""
Select one or more prompt IDs based on issue context and routing rules. The
resulting selection is written to a JSON file with keys:

  chosen    – list of prompt identifiers (primary plus any combos)
  scores    – dictionary of scores per rule
  signals   – placeholder for future PDF analysis signals

The script expects a routing YAML file that defines matching rules and
combination logic. The context JSON should contain at least 'body' and
'latest_comment' fields.
"""

import argparse
import json
import re
import yaml


def detect_pdf_signals():
    """Placeholder for future PDF meta analysis. Currently returns defaults."""
    return {
        "page_count": 0,
        "contains_tables": False,
        "likely_invoice": False,
        "likely_datasheet": False,
    }


def score_rules(routing, comment, signals):
    """Compute a weighted score for each prompt ID based on routing rules."""
    scores = {}
    for rule in routing.get("rules", []):
        weight = rule.get("weight", 1)
        ok = True
        conditions = rule.get("when", {})
        # Match comment keywords
        if "comment_any" in conditions:
            if not any(re.search(re.escape(k), comment or "", re.I) for k in conditions["comment_any"]):
                ok = False
        # Match PDF signals
        if "pdf_signals" in conditions:
            for k, v in conditions["pdf_signals"].items():
                if isinstance(v, bool):
                    if bool(signals.get(k)) != v:
                        ok = False
                if isinstance(v, int) and k.endswith("_min"):
                    base = k[:-4]
                    if int(signals.get(base, 0)) < v:
                        ok = False
        if ok:
            for pid in rule.get("choose", []):
                scores[pid] = scores.get(pid, 0) + weight
    return scores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)
    ap.add_argument("--routing", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    ctx = json.load(open(args.context))
    routing = yaml.safe_load(open(args.routing))
    signals = detect_pdf_signals()
    # Use latest comment for update events or fallback to body
    comment = (ctx.get("latest_comment") or ctx.get("body") or "")
    scores = score_rules(routing, comment, signals)
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    chosen = [pid for pid, _ in ordered[:1]] or routing.get("defaults", {}).get("fallback", ["npp_requirements"])
    # Add combination prompts
    combo_map = routing.get("defaults", {}).get("combo", {})
    for pid in list(chosen):
        for co in combo_map.get(pid, []):
            if co not in chosen:
                chosen.append(co)
    out = {
        "chosen": chosen,
        "scores": scores,
        "signals": signals,
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Selected prompts: {chosen}")


if __name__ == "__main__":
    main()