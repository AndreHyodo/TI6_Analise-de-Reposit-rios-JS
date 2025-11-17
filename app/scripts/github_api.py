import requests
import os
import json
import base64
import time
import random
from urllib.parse import quote

from dotenv import load_dotenv
load_dotenv()

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"

def make_session(token=None):
    s = requests.Session()
    token = token or os.getenv("GITHUB_TOKEN")
    if token:
        s.headers.update({
            "Authorization": f"token {token}",
            "User-Agent": "ti6-miner/1.0"
        })
    else:
        s.headers.update({"User-Agent": "ti6-miner/1.0"})
    return s

def _sleep_backoff(attempt, resp=None):
    if resp is not None:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                sec = int(ra)
                time.sleep(sec + 1)
                return
            except Exception:
                pass
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")
        if remaining == "0" and reset:
            try:
                reset_ts = int(reset)
                sleep_for = max(0, reset_ts - int(time.time()) + 2)
                time.sleep(sleep_for)
                return
            except Exception:
                pass
    base = min(60, (2 ** attempt))
    jitter = random.uniform(0.5, 2.0)
    time.sleep(base + jitter)

def request_with_backoff(method, url, session=None, headers=None, params=None, json_body=None, timeout=30, max_retries=6):
    s = session or make_session()
    req_headers = {}
    if headers:
        req_headers.update(headers)
    attempt = 0
    last_exc = None
    while attempt < max_retries:
        attempt += 1
        try:
            resp = s.request(method, url, headers=req_headers or None, params=params, json=json_body, timeout=timeout)
            if resp.status_code < 400:
                return resp
            if resp.status_code in (429, 403, 502, 503, 504):
                _sleep_backoff(attempt, resp)
                last_exc = Exception(f"{resp.status_code} Client Error: {resp.text} for url: {url}")
                continue
            resp.raise_for_status()
        except requests.RequestException as e:
            last_exc = e
            _sleep_backoff(attempt, None)
            continue
    if last_exc:
        raise last_exc
    raise RuntimeError("request_with_backoff: exhausted retries.")

def get_top_js_repos(limit=100, session=None):
    session = session or make_session()
    results = []
    per_page = 100
    page = 1
    remaining = limit
    while remaining > 0:
        take = min(per_page, remaining)
        url = f"{GITHUB_API}/search/repositories"
        params = {"q": "language:javascript", "sort": "stars", "order": "desc", "per_page": take, "page": page}
        resp = request_with_backoff("GET", url, session=session, params=params, timeout=30)
        data = resp.json().get("items", []) if resp and resp.status_code == 200 else []
        if not data:
            break
        for repo in data:
            results.append({
                "repo": repo["full_name"],
                "name": repo["full_name"],
                "url": repo["html_url"],
                "stars": repo["stargazers_count"],
                "forks": repo["forks_count"],
                "size_kb": repo["size"],
                "updated_at": repo["updated_at"],
            })
        remaining -= len(data)
        page += 1
        if len(data) < take:
            break
    return results

def fetch_package_json_at_ref(repo_full_name, ref="HEAD", path="package.json", session=None):
    session = session or make_session()
    encoded_path = quote(path, safe="")
    url = f"{GITHUB_API}/repos/{repo_full_name}/contents/{encoded_path}"
    params = {"ref": ref}
    try:
        r = request_with_backoff("GET", url, session=session, params=params, timeout=20)
    except Exception:
        return None
    if r.status_code == 404:
        return None
    data = r.json()
    if isinstance(data, list):
        return None
    if data.get("encoding") == "base64":
        try:
            decoded = base64.b64decode(data.get("content", "")).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            return None
    return None

def list_commits_touching_path(repo_full_name, path="package.json", session=None, per_page=100, params_extra=None):
    session = session or make_session()
    url = f"{GITHUB_API}/repos/{repo_full_name}/commits"
    params = {"path": path, "per_page": per_page}
    if params_extra:
        params.update(params_extra)
    try:
        r = request_with_backoff("GET", url, session=session, params=params, timeout=20)
    except Exception:
        return []
    if r.status_code == 404:
        return []
    try:
        items = r.json()
    except Exception:
        return []
    return items

def get_commit_detail(repo_full_name, sha, session=None):
    session = session or make_session()
    url = f"{GITHUB_API}/repos/{repo_full_name}/commits/{sha}"
    try:
        r = request_with_backoff("GET", url, session=session, timeout=20)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None

def get_file_at_commit_raw(repo_full_name, filepath, commit_sha, session=None):
    return fetch_package_json_at_ref(repo_full_name, ref=commit_sha, path=filepath, session=session)

def _get_tree_sha_for_ref(repo_full_name, ref, session=None):
    session = session or make_session()
    url = f"{GITHUB_API}/repos/{repo_full_name}/commits/{ref}"
    try:
        r = request_with_backoff("GET", url, session=session, timeout=20)
    except Exception:
        return ref
    if r.status_code == 200:
        data = r.json()
        tree = data.get("commit", {}).get("tree", {}) or {}
        sha = tree.get("sha")
        if sha:
            return sha
    return ref

def list_files_at_ref(repo_full_name, ref="HEAD", session=None):
    session = session or make_session()
    tree_sha = _get_tree_sha_for_ref(repo_full_name, ref, session=session)
    url = f"{GITHUB_API}/repos/{repo_full_name}/git/trees/{tree_sha}"
    params = {"recursive": "1"}
    try:
        r = request_with_backoff("GET", url, session=session, params=params, timeout=30)
        if r.status_code == 200:
            data = r.json()
            tree = data.get("tree", []) or []
            paths = [item.get("path") for item in tree if item.get("type") == "blob"]
            return paths
        else:
            r2 = request_with_backoff("GET", f"{GITHUB_API}/repos/{repo_full_name}/contents", session=session, params={"ref": ref}, timeout=20)
            if r2.status_code == 200:
                data = r2.json()
                if isinstance(data, list):
                    return [item.get("path") for item in data if item.get("type") == "file"]
    except Exception:
        pass
    return []

def find_package_json_paths(repo_full_name, ref="HEAD", session=None):
    paths = list_files_at_ref(repo_full_name, ref=ref, session=session)
    pkg_paths = [p for p in paths if p.lower().endswith("package.json")]
    pkg_paths_sorted = sorted(pkg_paths, key=lambda p: (0 if p == "package.json" else 1, p))
    return pkg_paths_sorted

# --- Tree & blob helpers (used by blob-based metrics) ---
def get_tree_for_ref(repo_full_name, ref="HEAD", session=None):
    session = session or make_session()
    tree_sha = _get_tree_sha_for_ref(repo_full_name, ref, session=session)
    url = f"{GITHUB_API}/repos/{repo_full_name}/git/trees/{tree_sha}"
    params = {"recursive": "1"}
    try:
        r = request_with_backoff("GET", url, session=session, params=params, timeout=60)
        if r.status_code == 200:
            data = r.json()
            tree = data.get("tree", []) or []
            return tree
    except Exception:
        pass
    return []

def get_blob_content(repo_full_name, blob_sha, session=None):
    session = session or make_session()
    url = f"{GITHUB_API}/repos/{repo_full_name}/git/blobs/{blob_sha}"
    try:
        r = request_with_backoff("GET", url, session=session, timeout=60)
        if r.status_code != 200:
            return None
        data = r.json()
        content = data.get("content")
        encoding = data.get("encoding")
        if content and encoding == "base64":
            try:
                raw = base64.b64decode(content)
                try:
                    return raw.decode("utf-8", errors="replace")
                except Exception:
                    return raw.decode("latin-1", errors="replace")
            except Exception:
                return None
        return None
    except Exception:
        return None

# --- GraphQL batch fetch for HEAD:package.json ---
def graphql_fetch_package_json_batch(repo_full_names, token=None, batch_size=40, session=None):
    """
    repo_full_names: list of "owner/repo"
    returns dict repo_full_name -> parsed package.json dict or None
    """
    token = token or os.getenv("GITHUB_TOKEN")
    session = session or make_session(token)
    if token:
        # GraphQL commonly expects 'bearer', but requests with token also works; set both headers if needed
        session.headers.update({"Authorization": f"bearer {token}"})
    out = {}
    for i in range(0, len(repo_full_names), batch_size):
        block = repo_full_names[i:i+batch_size]
        parts = []
        alias_map = {}
        for idx, full in enumerate(block):
            try:
                owner, name = full.split("/", 1)
            except Exception:
                out[full] = None
                continue
            alias = f"r{idx}"
            expr = f"HEAD:package.json"
            qpart = f'{alias}: repository(owner: "{owner}", name: "{name}") {{ object(expression: "{expr}") {{ ... on Blob {{ text }} }} }}'
            parts.append(qpart)
            alias_map[alias] = full
        query = "query { " + " ".join(parts) + " }"
        try:
            resp = request_with_backoff("POST", GITHUB_GRAPHQL, session=session, json_body={"query": query}, timeout=30)
            data = resp.json().get("data") if resp and resp.status_code == 200 else {}
            for alias, full in alias_map.items():
                repo_obj = data.get(alias) if data else None
                if not repo_obj:
                    out[full] = None
                    continue
                obj = repo_obj.get("object")
                if obj and obj.get("text") is not None:
                    try:
                        out[full] = json.loads(obj.get("text"))
                    except Exception:
                        out[full] = None
                else:
                    out[full] = None
        except Exception:
            for full in block:
                out[full] = None
    return out