#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar


def lower_contains_any(text: str, needles: list[str]) -> bool:
    hay = text.lower()
    return any(n.lower() in hay for n in needles)


class Client:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=self.base_url + path,
            method=method,
            data=data,
            headers=headers,
        )
        with self.opener.open(req, timeout=120) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        return json.loads(body) if body else {}

    def login(self, password: str) -> None:
        self.request_json("POST", "/auth/login", {"password": password})

    def ask(self, query: str, include_files: bool = False) -> dict:
        return self.request_json(
            "POST",
            "/api/ask",
            {"query": query, "include_files": include_files},
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Perplexio eval harness")
    parser.add_argument("--base-url", default=os.getenv("EVAL_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--password", default=os.getenv("EVAL_PASSWORD", ""))
    parser.add_argument("--cases", default="eval/cases.json")
    parser.add_argument("--out", default="eval/report.json")
    args = parser.parse_args()

    with open(args.cases, "r", encoding="utf-8") as f:
        cases = json.load(f)
    if not isinstance(cases, list):
        print("cases file must be a JSON array", file=sys.stderr)
        return 2

    client = Client(args.base_url)
    if args.password:
        try:
            client.login(args.password)
        except Exception as exc:
            print(f"login failed: {exc}", file=sys.stderr)
            return 2

    results: list[dict] = []
    passed = 0
    for case in cases:
        cid = str(case.get("id", "case"))
        query = str(case.get("query", "")).strip()
        must = [str(x) for x in case.get("must_include_any", [])]
        min_citations = int(case.get("min_citations", 0))
        if not query:
            results.append({"id": cid, "ok": False, "error": "empty query"})
            continue
        try:
            resp = client.ask(query=query, include_files=False)
            answer = str(resp.get("answer", ""))
            citations = resp.get("citations", [])
            ok_content = True if not must else lower_contains_any(answer, must)
            ok_cites = len(citations) >= min_citations
            ok = ok_content and ok_cites
            if ok:
                passed += 1
            results.append(
                {
                    "id": cid,
                    "ok": ok,
                    "query": query,
                    "answer_preview": answer[:240],
                    "citations": len(citations),
                    "ok_content": ok_content,
                    "ok_citations": ok_cites,
                }
            )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            results.append({"id": cid, "ok": False, "error": f"http {exc.code}: {body[:240]}"})
        except Exception as exc:
            results.append({"id": cid, "ok": False, "error": str(exc)})

    summary = {
        "base_url": args.base_url,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": (passed / len(results)) if results else 0.0,
        "results": results,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: summary[k] for k in ["total", "passed", "failed", "pass_rate"]}, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
