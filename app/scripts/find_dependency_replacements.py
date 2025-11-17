#!/usr/bin/env python3
"""
Mineração de commits usando blobs (sem checkout) e cálculo inline de métricas.

Modificações:
- adicionada flag --file_limit para limitar quantos arquivos JS/TS serão processados por commit
- prints de progresso para debug (mostra quantos blobs foram encontrados e progresso)
"""
import argparse
import json
import os
import shutil
import tempfile
import re
import time
from datetime import datetime, timedelta
from statistics import mean
from app.scripts.github_api import list_commits_touching_path, get_commit_detail, get_tree_for_ref, get_blob_content, make_session, fetch_package_json_at_ref
from app.scripts.metrics import get_cve_for_package, load_osv_cache, save_osv_cache
from dotenv import load_dotenv
load_dotenv()

# optional lizard import
try:
    import lizard
    _HAVE_LIZARD = True
except Exception:
    _HAVE_LIZARD = False

SRC_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
SKIP_PATH_PARTS = ("node_modules/", "bower_components/", "dist/", "build/", "vendor/", ".git/")
MAX_BLOB_BYTES = 1024 * 1024  # 1 MB
_pkg_line_re = re.compile(r'^[\-\+]\s*"(?P<name>[^"]+)":\s*"(?P<ver>[^"]+)"', flags=re.MULTILINE)

def parse_removed_added_from_patch(patch_text):
    removed = {}
    added = {}
    if not patch_text:
        return removed, added
    for m in _pkg_line_re.finditer(patch_text):
        line = m.group(0)
        op = line[0]
        name = m.group('name')
        ver = m.group('ver')
        if op == '-':
            removed[name] = ver
        elif op == '+':
            added[name] = ver
    return removed, added

def analyze_source_complexity(source_code, filename_for_reporting="<blob>"):
    lines = [ln for ln in source_code.splitlines() if ln.strip()]
    loc = len(lines)
    complexities = []
    if not _HAVE_LIZARD:
        return loc, complexities
    try:
        if hasattr(lizard.analyze_file, "analyze_source_code"):
            analysis = lizard.analyze_file.analyze_source_code(filename_for_reporting, source_code)
            funcs = getattr(analysis, "function_list", []) or []
        else:
            tf = None
            try:
                tf = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename_for_reporting)[1] or ".js")
                tf.write(source_code.encode("utf-8", errors="replace"))
                tf.close()
                analysis = lizard.analyze_file.analyze_file(tf.name)
                funcs = getattr(analysis, "function_list", []) or []
            finally:
                if tf:
                    try:
                        os.unlink(tf.name)
                    except Exception:
                        pass
        for f in funcs:
            c = None
            for attr in ("cyclomatic_complexity", "complexity", "cyclomatic"):
                if hasattr(f, attr):
                    c = getattr(f, attr)
                    break
            c = c or getattr(f, "cyclomatic_complexity", None) or getattr(f, "complexity", None) or 0
            try:
                complexities.append(float(c))
            except Exception:
                pass
    except Exception as e:
        print(f"[lizard] warning: failed to analyze {filename_for_reporting} with lizard: {e}")
        return loc, []
    return loc, complexities

def compute_metrics_from_commit(repo_full_name, commit_sha, session=None, file_limit=200):
    session = session or make_session()
    tree_items = get_tree_for_ref(repo_full_name, ref=commit_sha, session=session)
    if not tree_items:
        return {"lines_of_code": 0, "avg_complexity": 0.0, "files_processed": 0}
    # filter candidate blobs
    candidates = []
    for item in tree_items:
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if not path.lower().endswith(SRC_EXTS):
            continue
        if any(p in path for p in SKIP_PATH_PARTS):
            continue
        size = item.get("size") or 0
        if size and size > MAX_BLOB_BYTES:
            continue
        candidates.append(item)
    total_candidates = len(candidates)
    if total_candidates == 0:
        return {"lines_of_code": 0, "avg_complexity": 0.0, "files_processed": 0}
    # limit
    if file_limit and total_candidates > file_limit:
        candidates = candidates[:file_limit]
        total_to_process = file_limit
    else:
        total_to_process = total_candidates
    # print(f"[metrics] {repo_full_name}@{commit_sha} -> {total_candidates} candidate blobs, processing {total_to_process}", flush=True)
    total_loc = 0
    complexity_vals = []
    processed_files = 0
    report_every = max(1, total_to_process // 10)
    for idx, item in enumerate(candidates, start=1):
        blob_sha = item.get("sha")
        path = item.get("path")
        content = get_blob_content(repo_full_name, blob_sha, session=session)
        if content is None:
            # could be binary or API issue; skip
            continue
        loc, comps = analyze_source_complexity(content, filename_for_reporting=path)
        total_loc += loc
        complexity_vals.extend(comps)
        processed_files += 1
        # if idx % report_every == 0 or idx == total_to_process:
        #     print(f"[metrics] processed {idx}/{total_to_process} files for {repo_full_name}@{commit_sha}", flush=True)
    avg_complexity = float(mean(complexity_vals)) if complexity_vals else 0.0
    return {"lines_of_code": total_loc, "avg_complexity": round(avg_complexity, 4), "files_processed": processed_files}

def analyze_repo(full_name, token=None, limit_commits=None, include_pkg_snapshots=False, write_per_repo_file=None, max_candidates_per_repo=1, days_back=None, session=None, file_limit=200):
    session = session or make_session(token)
    if token:
        session.headers.update({"Authorization": f"token {token}"})
    if days_back:
        cutoff_dt = datetime.utcnow() - timedelta(days=int(days_back))
    else:
        cutoff_dt = None
    commits = list_commits_touching_path(full_name, path="package.json", session=session, per_page=100)
    if not commits:
        return []
    if days_back:
        filtered = []
        for c in commits:
            date_s = (c.get("commit") or {}).get("author", {}).get("date")
            if not date_s:
                filtered.append(c)
            else:
                try:
                    cd = datetime.fromisoformat(date_s.replace("Z", "+00:00"))
                    if cd >= cutoff_dt:
                        filtered.append(c)
                except Exception:
                    filtered.append(c)
        commits = filtered
    if limit_commits:
        commits = commits[:limit_commits]
    results = []
    cache = load_osv_cache() or {}
    try:
        for c in commits:
            if max_candidates_per_repo and len(results) >= max_candidates_per_repo:
                break
            sha = c.get("sha")
            parents = c.get("parents", []) or []
            if not parents:
                continue
            parent_sha = parents[0].get("sha")
            detail = get_commit_detail(full_name, sha, session=session)
            if not detail:
                continue
            files = detail.get("files", []) or []
            removed_list = []
            for f in files:
                fname = f.get("filename", "")
                if not fname.lower().endswith("package.json"):
                    continue
                patch = f.get("patch") or ""
                removed_map, added_map = parse_removed_added_from_patch(patch)
                for dep_name, ver_before in removed_map.items():
                    versions_before = [ver_before] if ver_before else []
                    versions_after = []
                    if dep_name in added_map:
                        versions_after = [added_map[dep_name]]
                    removed_list.append({
                        "name": dep_name,
                        "versions_before": versions_before,
                        "versions_after": versions_after,
                        "file": fname,
                    })
            if not removed_list:
                continue
            try:
                metrics_before = {"lines_of_code": 0, "avg_complexity": 0.0}
                metrics_after = {"lines_of_code": 0, "avg_complexity": 0.0}
                try:
                    metrics_before = compute_metrics_from_commit(full_name, parent_sha, session=session, file_limit=file_limit)
                except Exception as e:
                    print(f"[metrics] warning: failed computing metrics_before for {full_name}@{parent_sha}: {e}", flush=True)
                try:
                    metrics_after = compute_metrics_from_commit(full_name, sha, session=session, file_limit=file_limit)
                except Exception as e:
                    print(f"[metrics] warning: failed computing metrics_after for {full_name}@{sha}: {e}", flush=True)
                before_entry = {"lines_of_code": metrics_before.get("lines_of_code", 0), "avg_complexity": metrics_before.get("avg_complexity", 0.0)}
                after_entry = {"lines_of_code": metrics_after.get("lines_of_code", 0), "avg_complexity": metrics_after.get("avg_complexity", 0.0)}
                for r in removed_list:
                    dep_name = r.get("name")
                    try:
                        cve_count, cve_ids = get_cve_for_package(dep_name, session=session, cache=cache)
                    except Exception:
                        cve_count, cve_ids = 0, []
                    candidate = {
                        "repo": full_name,
                        "commit": sha,
                        "parent": parent_sha,
                        "commit_message": (detail.get("commit") or {}).get("message", ""),
                        "commit_date": (detail.get("commit") or {}).get("author", {}).get("date", ""),
                        "removed_dep": dep_name,
                        "removed_dep_details": {
                            "versions_before": r.get("versions_before", []),
                            "versions_after": r.get("versions_after", []),
                            "file": r.get("file"),
                            "cve_count": cve_count,
                            "cve_ids": cve_ids,
                        },
                        "metrics_before": before_entry,
                        "metrics_after": after_entry,
                    }
                    if include_pkg_snapshots:
                        try:
                            pkg_before = fetch_package_json_at_ref(full_name, ref=parent_sha, path=r.get("file"), session=session) or {}
                        except Exception:
                            pkg_before = {}
                        try:
                            pkg_after = fetch_package_json_at_ref(full_name, ref=sha, path=r.get("file"), session=session) or {}
                        except Exception:
                            pkg_after = {}
                        candidate["pkg_before"] = {r.get("file"): pkg_before}
                        candidate["pkg_after"] = {r.get("file"): pkg_after}
                    results.append(candidate)
                    if max_candidates_per_repo and len(results) >= max_candidates_per_repo:
                        break
            except Exception as e:
                print(f"Erro processando commit {sha} em {full_name}: {e}", flush=True)
                continue
        save_osv_cache(cache)
        if write_per_repo_file:
            os.makedirs(os.path.dirname(write_per_repo_file), exist_ok=True)
            with open(write_per_repo_file, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
        return results
    finally:
        pass
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--include_pkg_snapshots", action="store_true")
    parser.add_argument("--max_candidates", type=int, default=1)
    parser.add_argument("--days_back", type=int, default=365)
    parser.add_argument("--file_limit", type=int, default=200, help="máx. arquivos JS/TS processados por commit")
    args = parser.parse_args()
    res = analyze_repo(
        args.repo,
        token=os.getenv("GITHUB_TOKEN"),
        limit_commits=args.limit,
        include_pkg_snapshots=args.include_pkg_snapshots,
        write_per_repo_file=args.out,
        max_candidates_per_repo=args.max_candidates,
        days_back=args.days_back,
        session=None,
        file_limit=args.file_limit
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))