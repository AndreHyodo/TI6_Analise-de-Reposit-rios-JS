#!/usr/bin/env python3
"""
Função wrapper para merge + plots. Recebe lista de commits json files.
"""
import os
import json
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import numpy as np

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def merge_and_plot_main(deps_json, commits_json_list, sonar_json, out_dataset, out_plots):
    # carregar deps (pode falhar/estar vazio)
    deps = load_json(deps_json) if deps_json and os.path.exists(deps_json) else []
    # carregar commits: list of files -> concat
    commits_all = []
    for cj in commits_json_list or []:
        if os.path.exists(cj):
            commits_all.extend(load_json(cj))
    # build DF
    rows = []
    for c in commits_all:
        before = c.get("metrics_before", {})
        after = c.get("metrics_after", {})
        rows.append({
            "repo": c.get("repo"),
            "removed_dep": c.get("removed_dep"),
            "lines_before": before.get("lines_of_code", 0),
            "lines_after": after.get("lines_of_code", 0),
            "complex_before": before.get("avg_complexity", 0),
            "complex_after": after.get("avg_complexity", 0),
            "delta_lines": after.get("lines_of_code", 0) - before.get("lines_of_code", 0),
            "delta_complex": after.get("avg_complexity", 0) - before.get("avg_complexity", 0),
        })
    df_commits = pd.DataFrame(rows)
    # merge repo-level deps into commit df (defensivo: verifique coluna 'repo')
    if deps:
        df_deps = pd.DataFrame(deps)
        if 'repo' in df_deps.columns:
            df = df_commits.merge(df_deps[['repo','dependencies','vulnerable_deps']], on='repo', how='left')
        else:
            print("Aviso: arquivo de dependências não tem coluna 'repo' — pulando merge de dependências.")
            df = df_commits
    else:
        df = df_commits
    os.makedirs(os.path.dirname(out_dataset), exist_ok=True)
    df.to_json(out_dataset, orient='records', indent=2)
    print(f"Saved merged dataset: {out_dataset}")
    # plots (mesma lógica anterior)
    os.makedirs(out_plots, exist_ok=True)
    if not df.empty:
        df['log_complex_before'] = np.log1p(df['complex_before'].fillna(0).astype(float))
        df['log_complex_after']  = np.log1p(df['complex_after'].fillna(0).astype(float))
        df['log_lines_before']   = np.log1p(df['lines_before'].fillna(0).astype(float))
        df['log_lines_after']    = np.log1p(df['lines_after'].fillna(0).astype(float))

        melt_complex = df[['log_complex_before','log_complex_after']].melt(var_name='when', value_name='log1p_complexity')
        plt.figure(figsize=(8,6))
        sns.boxplot(data=melt_complex, x='when', y='log1p_complexity')
        plt.title("log1p(Complexidade média) antes vs depois")
        plt.ylabel("log1p(avg_complexity)  (isto é, log(1 + x))")
        plt.savefig(os.path.join(out_plots, "boxplot_complexity_before_after_log1p.png"))
        plt.close()

        melt_loc = df[['log_lines_before','log_lines_after']].melt(var_name='when', value_name='log1p_loc')
        plt.figure(figsize=(8,6))
        sns.boxplot(data=melt_loc, x='when', y='log1p_loc')
        plt.title("log1p(LOC) antes vs depois")
        plt.ylabel("log1p(lines_of_code)  (isto é, log(1 + x))")
        plt.savefig(os.path.join(out_plots, "boxplot_loc_before_after_log1p.png"))
        plt.close()

        x = df.get('dependencies') if 'dependencies' in df.columns else pd.Series([0]*len(df))
        x = x.fillna(0)
        y = df['delta_complex'].fillna(0)

        plt.figure(figsize=(8,6))
        plt.scatter(x + 1e-6, y + 1e-6, alpha=0.6)
        plt.xscale('log')
        plt.yscale('symlog')
        plt.xlabel("dependencies (repo) [log scale]")
        plt.ylabel("delta_complex (after - before) [symlog]")
        plt.title("delta_complex vs dependencies")
        plt.grid(True, which="both", ls="--", lw=0.5)
        plt.savefig(os.path.join(out_plots, "scatter_delta_complex_vs_dependencies_log.png"))
        plt.close()
    print(f"Plots saved to {out_plots}")