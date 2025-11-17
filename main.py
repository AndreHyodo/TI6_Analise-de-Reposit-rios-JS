import argparse
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import pandas as pd
from app.scripts.github_api import get_top_js_repos, make_session
from app.scripts.metrics import get_metrics_batch
from app.scripts.find_dependency_replacements import analyze_repo
from app.scripts.merge_and_plot import merge_and_plot_main

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

RESULTS_DIR = os.path.join("app", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

def stage_deps(limit, workers, out_json, session=None):
    session = session or make_session(GITHUB_TOKEN)
    repos = get_top_js_repos(limit=limit, session=session)
    summaries = get_metrics_batch(repos, token=GITHUB_TOKEN, workers=workers, session=session)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summaries, f, indent=2, ensure_ascii=False)
    print(f"Saved deps JSON: {out_json}", flush=True)
    return summaries

def chunked_iterable(it, chunk_size):
    for i in range(0, len(it), chunk_size):
        yield it[i:i+chunk_size]

def stage_mining_aggregate(deps_json, sample=None, workers=2, include_pkg_snapshots=False, out_json=None, out_csv=None, session=None, max_candidates=1, days_back=None, chunk_size=50, file_limit=200):
    with open(deps_json, "r", encoding="utf-8") as f:
        repos = json.load(f)
    repo_names = [r["repo"] for r in repos]
    if sample:
        repo_names = repo_names[:sample]
    total = len(repo_names)
    print(f"Mining {total} repos (sample={sample}) in chunks of {chunk_size} with workers={workers} ...", flush=True)
    all_candidates = []
    session = session or make_session(GITHUB_TOKEN)
    processed = 0
    for chunk_idx, chunk in enumerate(chunked_iterable(repo_names, chunk_size), start=1):
        start_time = time.time()
        # print(f"Processing chunk {chunk_idx} ({len(chunk)} repos). Processed so far: {processed}/{total}", flush=True)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(analyze_repo, repo, token=GITHUB_TOKEN, limit_commits=50, include_pkg_snapshots=include_pkg_snapshots, write_per_repo_file=None, max_candidates_per_repo=max_candidates, days_back=days_back, session=session, file_limit=file_limit): repo for repo in chunk}
            for fut in as_completed(futures):
                repo = futures[fut]
                try:
                    res = fut.result()
                    if res:
                        all_candidates.extend(res)
                    print(f"Done mining {repo} -> {len(res or [])} candidates", flush=True)
                except Exception as e:
                    print(f"Mining failed for {repo}: {e}", flush=True)
        processed += len(chunk)
        elapsed = time.time() - start_time
        print(f"Finished chunk {chunk_idx} in {elapsed:.1f}s. Total candidates so far: {len(all_candidates)}", flush=True)
        time.sleep(1)
    if out_json:
        os.makedirs(os.path.dirname(out_json), exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(all_candidates, f, indent=2, ensure_ascii=False)
        print(f"Saved aggregated mining JSON: {out_json}", flush=True)
    if out_csv:
        rows = []
        for c in all_candidates:
            rd = c.get("removed_dep_details", {}) or {}
            rows.append({
                "repo": c.get("repo"),
                "commit": c.get("commit"),
                "parent": c.get("parent"),
                "commit_date": c.get("commit_date"),
                "commit_message": c.get("commit_message"),
                "removed_dep": c.get("removed_dep"),
                "version_before": rd.get("versions_before"),
                "version_after": rd.get("versions_after"),
                "cve_count": rd.get("cve_count", 0),
                "cve_ids": ";".join(rd.get("cve_ids") or []),
                "lines_before": c.get("metrics_before", {}).get("lines_of_code"),
                "lines_after": c.get("metrics_after", {}).get("lines_of_code"),
                "complex_before": c.get("metrics_before", {}).get("avg_complexity"),
                "complex_after": c.get("metrics_after", {}).get("avg_complexity"),
            })
        df = pd.DataFrame(rows)
        os.makedirs(os.path.dirname(out_csv), exist_ok=True)
        df.to_csv(out_csv, index=False)
        print(f"Saved aggregated mining CSV: {out_csv}", flush=True)
    return all_candidates

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["deps","mining","merge","all"], default="all")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--mining_workers", type=int, default=2)
    parser.add_argument("--mining_sample", type=int, default=50)
    parser.add_argument("--deps_out", default=os.path.join(RESULTS_DIR,"dependencies_cve_summary.json"))
    parser.add_argument("--mining_json_out", default=os.path.join(RESULTS_DIR,"commit_changes_all.json"))
    parser.add_argument("--mining_csv_out", default=os.path.join(RESULTS_DIR,"commit_changes_all.csv"))
    parser.add_argument("--plots", default=os.path.join(RESULTS_DIR,"plots"))
    parser.add_argument("--final_out", default=os.path.join(RESULTS_DIR,"final_dataset.json"))
    parser.add_argument("--max_candidates", type=int, default=1)
    parser.add_argument("--days_back", type=int, default=365)
    parser.add_argument("--chunk_size", type=int, default=50)
    parser.add_argument("--file_limit", type=int, default=200, help="máx. arquivos JS/TS processados por commit")
    args = parser.parse_args()

    session = make_session(GITHUB_TOKEN)

    if args.stage in ("deps","all"):
        print("Running stage: deps", flush=True)
        stage_deps(limit=args.limit, workers=args.workers, out_json=args.deps_out, session=session)

    if args.stage in ("mining","all"):
        print("Running stage: mining (aggregated)", flush=True)
        sample = None if (args.mining_sample==0) else args.mining_sample
        stage_mining_aggregate(args.deps_out, sample=sample, workers=args.mining_workers, include_pkg_snapshots=False, out_json=args.mining_json_out, out_csv=args.mining_csv_out, session=session, max_candidates=args.max_candidates, days_back=args.days_back, chunk_size=args.chunk_size, file_limit=args.file_limit)

    if args.stage in ("merge","all"):
        commits_input = [args.mining_json_out] if os.path.exists(args.mining_json_out) else []
        merge_and_plot_main(args.deps_out, commits_input, None, args.final_out, args.plots)
        print(f"Merge done -> {args.final_out} and plots at {args.plots}", flush=True)

if __name__ == "__main__":
    main()