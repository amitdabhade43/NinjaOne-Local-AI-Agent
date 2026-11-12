"""Run a local-only read-only NinjaOne agent latency benchmark."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


QUESTIONS = [
    "How many NinjaOne organizations do we manage? Show names and IDs.",
    "Which machines have not checked in for 7 days?",
    "Show critical alerts raised in the last 24 hours.",
    "Find devices with less than 10 percent free disk space.",
    "Which devices have failed or missing patches?",
    "Summarize unresolved alerts by organization.",
    "Find laptops that are offline and show their last contact time.",
    "Which devices have policy assignment or compliance issues?",
]


def post_chat(base_url: str, message: str, model: str | None, timeout: int) -> dict:
    payload = {"message": message}
    if model:
        payload["model"] = model
    request = Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default=None, help="Exact Foundry model ID; omit to use agent default")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", default="reports/benchmark-results.json")
    parser.add_argument("--question", action="append", help="Run only supplied question(s); repeat this option")
    args = parser.parse_args()

    questions = args.question or QUESTIONS
    results = []
    for index, question in enumerate(questions, start=1):
        started = time.perf_counter()
        try:
            response = post_chat(args.base_url, question, args.model, args.timeout)
            elapsed = time.perf_counter() - started
            steps = response.get("steps") or []
            statuses = [str(step.get("status", "unknown")) for step in steps]
            tools = [str(step.get("tool", "unknown")) for step in steps]
            pending = bool(response.get("pending_approvals"))
            result = {
                "question_number": index,
                "question": question,
                "model": response.get("model"),
                "elapsed_seconds": round(elapsed, 3),
                "tool_count": len(steps),
                "tools": tools,
                "statuses": statuses,
                "pending_approval": pending,
                "answer_length": len(response.get("answer") or ""),
                "http_success": True,
            }
            print(f"\n[{index}/{len(questions)}] {question}")
            print(f"Model: {result['model']} | Time: {elapsed:.2f}s | Tools: {len(steps)} | Pending approval: {pending}")
            print(response.get("answer") or "")
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            elapsed = time.perf_counter() - started
            result = {
                "question_number": index,
                "question": question,
                "elapsed_seconds": round(elapsed, 3),
                "http_success": False,
                "error": str(exc),
            }
            print(f"\n[{index}/{len(questions)}] FAILED after {elapsed:.2f}s: {exc}")
        results.append(result)

    successful_times = [item["elapsed_seconds"] for item in results if item.get("http_success")]
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "requested_model": args.model,
        "question_count": len(questions),
        "successful_requests": len(successful_times),
        "average_seconds": round(statistics.mean(successful_times), 3) if successful_times else None,
        "median_seconds": round(statistics.median(successful_times), 3) if successful_times else None,
        "results": results,
        "privacy_note": "Answer bodies and NinjaOne records are not written to this report.",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nBenchmark report saved to: {output.resolve()}")
    if successful_times:
        print(f"Average: {report['average_seconds']}s | Median: {report['median_seconds']}s")
    return 0 if len(successful_times) == len(questions) else 1


if __name__ == "__main__":
    raise SystemExit(main())
