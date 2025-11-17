import subprocess
import json
import os


def run_command(command, cwd=None):
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        print(f"⚠️ Erro ao executar '{command}' em {cwd}:\n{result.stderr}")
    return result.stdout.strip()


def save_json(filepath, data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
