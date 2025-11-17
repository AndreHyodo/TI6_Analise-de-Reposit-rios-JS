import os
import pandas as pd
from dotenv import load_dotenv
from scripts.github_api import get_top_js_repos
from scripts.metrics import get_metrics
from scripts.utils import save_json

load_dotenv()
TOKEN = os.getenv("GITHUB_TOKEN")

RESULTS_DIR = "./results"
os.makedirs(RESULTS_DIR, exist_ok=True)


def main():
    print("🔍 Buscando repositórios JavaScript mais populares...")
    repos = get_top_js_repos(limit=100)
    summary = []

    for repo in repos:
        print(f"📦 Analisando dependências e CVEs em: {repo['name']} ...")
        metrics = get_metrics(repo, TOKEN)
        summary.append(metrics)

    df = pd.DataFrame(summary)
    df.to_csv(f"{RESULTS_DIR}/dependencies_cve_summary.csv", index=False)
    save_json(f"{RESULTS_DIR}/dependencies_cve_summary.json", summary)

    print("✅ Análise concluída! Resultados salvos em ./results/dependencies_cve_summary.csv")

    try:
        import matplotlib.pyplot as plt
        df.plot(x="repo", y=["dependencies", "vulnerable_deps"], kind="bar", figsize=(8, 4))
        plt.title("Dependências e Vulnerabilidades (Top JS Repos)")
        plt.tight_layout()
        plt.savefig(f"{RESULTS_DIR}/chart_cve.png")
        print("📈 Gráfico salvo em ./results/chart_cve.png")
    except Exception as e:
        print(f"⚠️ Erro ao gerar gráfico: {e}")


if __name__ == "__main__":
    main()
