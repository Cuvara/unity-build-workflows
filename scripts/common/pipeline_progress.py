#!/usr/bin/env python3
"""
pipeline_progress.py
Draw where a pipeline run has got to.

GitHub renders no progress indicator on a workflow node. The graph shows shape
and colour but not distance: with eleven nodes, three of them matrix legs and
four legitimately skipped, "how far did this get?" is a question the graph
cannot answer and a human has to reconstruct. Every release pipeline was
reconstructing it by eye.

So the pipeline draws its own. Two modes, one renderer:

  --current   a live marker. Each job calls this as it finishes, so the run's
              summary grows into a ladder while the run is still going.
  --results   the final report: every stage with the result it actually had.

The ladder is deliberately the same shape in both, so the thing you watched
during the run is the thing you read afterwards.

    Release / Android                    ████████████░░░░░░░░░░░░  3/7 stages
      [x] 04 Verify Release Identity     ████████ done
      [x] 04 Validate AAB                ████████ done
      [>] 05 Publish - Internal Testing  ████░░░░ running
          06 Release - External Testing  ░░░░░░░░ pending
          06 Release - Production        ░░░░░░░░ pending

Stdlib only. Writes to stdout and, when set, appends to $GITHUB_STEP_SUMMARY.
"""

import argparse
import os
import sys

BAR_WIDTH = 24
STAGE_BAR_WIDTH = 8

# A result maps to a glyph and to whether it counts as "distance covered".
# `skipped` counts: a phase skipped on purpose (a dry run, a start-phase that
# begins later, a platform this project does not ship) is not a stall, and
# drawing it as one would make every normal run look stuck.
STATES = {
    "success":   ("[x]", "done",      True),
    "skipped":   ("[-]", "skipped",   True),
    "running":   ("[>]", "running",   False),
    "failure":   ("[!]", "FAILED",    False),
    "cancelled": ("[/]", "cancelled", False),
    "pending":   ("   ", "pending",   False),
    # Skipped because something earlier failed — never attempted, so it is not
    # distance covered.
    "blocked":   ("   ", "not reached", False),
}
UNKNOWN = ("[?]", "no result", False)


def bar(done, total, width=BAR_WIDTH):
    if total <= 0:
        return "░" * width
    filled = max(0, min(width, round(width * done / total)))
    return "█" * filled + "░" * (width - filled)


def stage_bar(state):
    """A per-stage bar: full when done, part-full while running, empty before."""
    if state == "success":
        return "█" * STAGE_BAR_WIDTH
    if state == "skipped":
        # Deliberately a different texture from both done and pending: a
        # skipped phase is neither.
        return "▒" * STAGE_BAR_WIDTH
    if state == "running":
        return "█" * (STAGE_BAR_WIDTH // 2) + "░" * (STAGE_BAR_WIDTH - STAGE_BAR_WIDTH // 2)
    if state in ("failure", "cancelled"):
        return "█" * 2 + "░" * (STAGE_BAR_WIDTH - 2)
    return "░" * STAGE_BAR_WIDTH


def parse_stages(raw):
    """`label=result` lines, in pipeline order. A bare label means pending."""
    stages = []
    for line in (raw or "").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        label, _, result = line.partition("=")
        result = result.strip().lower() or "pending"
        # An empty `needs.x.result` renders as the empty string, which means
        # the job has not reported — pending, not broken.
        stages.append((label.strip(), result or "pending"))
    return stages


def apply_current(stages, current):
    """Mark `current` as running and everything before it as done.

    A live marker knows its own position and nothing else: the job calling it
    cannot see whether a sibling matrix leg has finished. Treating everything
    earlier as done is what the sequence already guarantees — a later phase
    cannot start until the earlier ones let it.
    """
    marked = []
    seen_current = False
    for label, result in stages:
        if label == current:
            seen_current = True
            marked.append((label, "running" if result == "pending" else result))
        elif not seen_current:
            marked.append((label, "success" if result == "pending" else result))
        else:
            marked.append((label, result))
    return marked


def resolve_blocked(stages):
    """Everything skipped AFTER a failure was blocked, not skipped on purpose.

    Without this a failed run counts its own wreckage as distance covered:
    "4/5 stages" beside a red cross reads as nearly finished, when in fact
    nothing after the failure was even attempted.
    """
    resolved = []
    stopped = False
    for label, result in stages:
        if stopped and result in ("skipped", "pending"):
            resolved.append((label, "blocked"))
            continue
        resolved.append((label, result))
        if result in ("failure", "cancelled"):
            stopped = True
    return resolved


def render(title, stages, note=""):
    if not stages:
        return f"{title}\n  (no stages)"

    stages = resolve_blocked(stages)
    width = max(len(label) for label, _ in stages)
    done = sum(1 for _, result in stages
               if STATES.get(result, UNKNOWN)[2])
    # Line the overall bar up with the per-stage bars: 2 indent + 3 glyph +
    # 1 space + label + 2 spaces.
    lines = [
        f"{title}",
        f"{'':<{width + 8}}{bar(done, len(stages))}  {done}/{len(stages)} stages",
        "",
    ]
    for label, result in stages:
        glyph, word, _ = STATES.get(result, UNKNOWN)
        lines.append(f"  {glyph} {label:<{width}}  {stage_bar(result)} {word}")
    if note:
        lines += ["", f"  {note}"]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render pipeline progress")
    parser.add_argument("--title", required=True,
                        help="e.g. 'Release / Android'")
    parser.add_argument("--stages", required=True,
                        help="Newline-separated `label` or `label=result`, in order")
    parser.add_argument("--current", default="",
                        help="Live mode: the stage that just ran. Earlier stages "
                             "are shown as done, later ones as pending.")
    parser.add_argument("--note", default="",
                        help="One line under the ladder, e.g. why it stopped")
    parser.add_argument("--heading", default="",
                        help="Markdown heading above the block in the summary")
    args = parser.parse_args(argv)

    stages = parse_stages(args.stages)
    if args.current:
        if args.current not in [label for label, _ in stages]:
            print(f"::error::'{args.current}' is not one of the declared stages: "
                  + ", ".join(label for label, _ in stages), file=sys.stderr)
            return 2
        stages = apply_current(stages, args.current)

    block = render(args.title, stages, args.note)
    print(block)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            if args.heading:
                fh.write(f"{args.heading}\n\n")
            fh.write("```text\n" + block + "\n```\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
