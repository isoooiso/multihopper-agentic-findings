"""
Run all four PoCs in sequence and write ONE combined evidence log
(evidence/run_all.log). Single-command reproduction of every finding.

    python run_all.py

Exit 0 == all four findings reproduced.
"""
import datetime
import subprocess
import sys

POCS = [
    ("Finding #1 — create is not restart-safe (duplicate transfers/funding)", "poc_create_idempotency.py"),
    ("Finding #2 — confirm-broadcast keeper double-funding race", "poc_02_funding_race.py"),
    ("Finding #3 — single-blockhash budget exhaustion", "poc_03_blockhash_budget.py"),
    ("Finding #4 — preparedTxs.resume.* is undocumented", "poc_04_resume_undocumented.py"),
]


def main():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    lines = [f"MultiHopper agentic-flow findings — combined PoC run @ {now}",
             "Each PoC is deterministic; exit code 0 means the finding reproduced.\n"]
    results = []
    for title, script in POCS:
        lines += ["", "#" * 84, f"# {title}", f"#   ({script})", "#" * 84]
        proc = subprocess.run([sys.executable, script], capture_output=True, text=True)
        lines.append(proc.stdout.rstrip())
        ok = proc.returncode == 0
        results.append((title, ok))
        lines.append(f"\n[exit code {proc.returncode} -> {'REPRODUCED' if ok else 'FAILED'}]")

    lines += ["", "=" * 84, "COMBINED SUMMARY"]
    for title, ok in results:
        lines.append(f"  [{'PASS' if ok else 'FAIL'}]  {title}")
    n_ok = sum(1 for _, ok in results if ok)
    lines.append(f"  {n_ok}/{len(results)} findings reproduced")
    lines.append("=" * 84)

    text = "\n".join(lines) + "\n"
    with open("evidence/run_all.log", "w") as f:
        f.write(text)
    print(text)
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
