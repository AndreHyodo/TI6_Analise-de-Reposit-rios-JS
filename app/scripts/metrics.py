import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.scripts.github_api import fetch_package_json_at_ref, find_package_json_paths, make_session, graphql_fetch_package_json_batch
from dotenv import load_dotenv
import requests

load_dotenv()

OSV_URL = "https://api.osv.dev/v1/query"
OSV_CACHE = os.path.join("app", "results", "osv_cache.json")

def load_osv_cache():
    try:
        with open(OSV_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_osv_cache(cache):
    try:
        os.makedirs(os.path.dirname(OSV_CACHE), exist_ok=True)
        with open(OSV_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def get_cve_for_package(package_name, session=None, cache=None):
    if cache is None:
        cache = {}
    if package_name in cache:
        return cache[package_name]
    payload = {"package": {"name": package_name, "ecosystem": "npm"}}
    s = session or requests
    try:
        r = s.post(OSV_URL, json=payload, timeout=10)
        if r.status_code == 200:
            vulns = r.json().get("vulns", [])
            result = (len(vulns), [v.get("id") for v in vulns])
            cache[package_name] = result
            return result
    except Exception:
        pass
    cache[package_name] = (0, [])
    return (0, [])

def compute_metrics_for_repo(pkg_jsons, repo_name, token=None, session=None, osv_cache=None):
    metrics = {
        "repo": repo_name,
        "dependencies": 0,
        "dev_dependencies": 0,
        "vulnerable_deps": 0,
        "cves": [],
        "path_used": "",
    }
    if not pkg_jsons:
        return metrics

    deps_agg = {}
    dev_deps_agg = {}
    paths = [p for p,_ in pkg_jsons]
    for path, pkg in pkg_jsons:
        deps = pkg.get("dependencies", {}) or {}
        dev = pkg.get("devDependencies", {}) or {}
        for k,v in deps.items():
            if k not in deps_agg:
                deps_agg[k] = v
        for k,v in dev.items():
            if k not in dev_deps_agg:
                dev_deps_agg[k] = v

    metrics["dependencies"] = len(deps_agg)
    metrics["dev_dependencies"] = len(dev_deps_agg)
    metrics["path_used"] = ",".join(paths) if paths else ""

    cache = osv_cache if osv_cache is not None else load_osv_cache()
    total_vulns = 0
    cve_list = []
    session = session or requests
    for dep in deps_agg.keys():
        count, ids = get_cve_for_package(dep, session=session, cache=cache)
        total_vulns += count
        if ids:
            cve_list.extend(ids)

    metrics["vulnerable_deps"] = total_vulns
    metrics["cves"] = sorted(list(set(cve_list)))
    return metrics

def get_metrics_batch(repos, token=None, workers=4, session=None, graphql_batch=40):
    """
    Repos: list of dict with 'repo' full_name
    This implementation uses GraphQL batch fetch for HEAD:package.json to reduce REST calls.
    """
    results = []
    session = session or make_session(token)
    shared_cache = load_osv_cache()

    repo_names = [r.get("repo") or r.get("name") for r in repos]
    # GraphQL batch to fetch package.json at HEAD for many repos
    pkg_map = graphql_fetch_package_json_batch(repo_names, token=token, batch_size=graphql_batch, session=session)

    def process(repo):
        repo_name = repo.get("repo") or repo.get("name")
        # GraphQL gave us the root package.json only; we still look for monorepo package.jsons via trees if needed
        pkg_jsons = []
        root_pkg = pkg_map.get(repo_name)
        if root_pkg:
            pkg_jsons.append(("package.json", root_pkg))
        else:
            # fallback: find package.json paths and fetch via REST (cheaper since not many)
            try:
                pkg_paths = find_package_json_paths(repo_name, ref="HEAD", session=session)
                for p in pkg_paths:
                    pj = fetch_package_json_at_ref(repo_name, ref="HEAD", path=p, session=session)
                    if pj:
                        pkg_jsons.append((p,pj))
            except Exception:
                pass
        metrics = compute_metrics_for_repo(pkg_jsons, repo_name, token=token, session=session, osv_cache=shared_cache)
        metrics["repo"] = repo_name
        metrics["stars"] = repo.get("stars") or repo.get("stargazers_count", 0)
        metrics["forks"] = repo.get("forks") or repo.get("forks_count", 0)
        return metrics

    max_workers = min(workers or 4, max(1, len(repos)))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(process, r): r for r in repos}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                print("Erro get_metrics_batch:", e)

    save_osv_cache(shared_cache)
    return results