import argparse
import json
import re
import subprocess
import os
import sys

def run(cmd, cwd=None):
    try:
        res = subprocess.check_output(cmd, shell=True, cwd=cwd, stderr=subprocess.DEVNULL, text=True)
        return res
    except subprocess.CalledProcessError:
        return ""

def list_js_files_at_commit(repo_dir, commit):
    out = run(f'git -C "{repo_dir}" ls-tree -r --name-only {commit}')
    if not out:
        return []
    files = [l.strip() for l in out.splitlines() if l.strip() and l.strip().lower().endswith(('.js', '.jsx', '.ts', '.tsx'))]
    return files

def get_file_content_at_commit(repo_dir, commit, path):
    return run(f'git -C "{repo_dir}" show {commit}:{path}')

KEYWORDS_RE = re.compile(r'\bif\b|\bfor\b|\bwhile\b|\bcase\b|\bcatch\b|&&|\|\|', flags=re.IGNORECASE)
FUNCTION_RE = re.compile(r'\bfunction\b|=>', flags=re.IGNORECASE)

def analyze_contents(contents):
    total_loc = 0
    total_functions = 0
    total_complexity_contrib = 0

    for file in contents:
        src = file.get("source", "")
        if not src:
            continue
        lines = src.splitlines()
        loc = len(lines)
        total_loc += loc

        functions_count = len(FUNCTION_RE.findall(src))
        keywords_count = len(KEYWORDS_RE.findall(src))

        complexity_contrib = functions_count + keywords_count

        if functions_count == 0 and keywords_count > 0:
            total_functions += 1
            total_complexity_contrib += complexity_contrib
        else:
            total_functions += functions_count
            total_complexity_contrib += complexity_contrib

    if total_functions > 0:
        avg_complexity = total_complexity_contrib / total_functions
    else:
        avg_complexity = 0.0

    return {"lines_of_code": total_loc, "avg_complexity": round(float(avg_complexity), 4)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="caminho para checkout do repositório (diretório com .git)")
    parser.add_argument("--commit", required=True, help="hash do commit a analisar")
    parser.add_argument("--out", required=True, help="arquivo de saída JSON")
    args = parser.parse_args()

    repo_dir = args.repo
    commit = args.commit
    out_path = args.out

    files = list_js_files_at_commit(repo_dir, commit)
    contents = []
    for f in files:
        src = get_file_content_at_commit(repo_dir, commit, f)
        if src is None:
            src = ""
        contents.append({"path": f, "source": src})

    metrics = analyze_contents(contents)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2, ensure_ascii=False)

    print(json.dumps(metrics))

if __name__ == "__main__":
    main()