# -*- coding: utf-8 -*-
"""Incrementa a versao (major.minor.patch) em version.json e installer/AutoBIM_Setup.iss.

Uso:
    python scripts/bump_version.py [patch|minor|major] [--notes "Texto"] [--dry-run] [--print-only]
"""
import argparse
import datetime
import json
import os
import re
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_version(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_version(data, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")


def bump(v, kind):
    parts = [int(p) for p in v.strip().lstrip("vV").split(".")]
    while len(parts) < 3:
        parts.append(0)
    if kind == "major":
        parts = [parts[0] + 1, 0, 0]
    elif kind == "minor":
        parts = [parts[0], parts[1] + 1, 0]
    else:
        parts[2] += 1
    return ".".join(str(p) for p in parts[:3])


def update_iss(iss_path, new_ver):
    if not os.path.exists(iss_path):
        return False
    with open(iss_path, "r", encoding="utf-8") as f:
        content = f.read()
    updated = re.sub(
        r'#define MyAppVersion ".*?"',
        '#define MyAppVersion "{}"'.format(new_ver),
        content,
    )
    if updated != content:
        with open(iss_path, "w", encoding="utf-8", newline="") as f:
            f.write(updated)
    return True


def main():
    parser = argparse.ArgumentParser(description="Incrementa a versao do AutoBIM.")
    parser.add_argument(
        "kind",
        nargs="?",
        default="patch",
        choices=("major", "minor", "patch"),
        help="Parte da versao a incrementar (padrao: patch).",
    )
    parser.add_argument("--notes", default=None, help="Notas da release para version.json.")
    parser.add_argument("--dry-run", action="store_true", help="Nao grava arquivos.")
    parser.add_argument("--print-only", action="store_true", help="Imprime apenas a nova versao.")
    args = parser.parse_args()

    version_path = os.path.join(ROOT, "version.json")
    iss_path = os.path.join(ROOT, "installer", "AutoBIM_Setup.iss")

    data = load_version(version_path)
    old = data.get("version", "1.0.0")
    new = bump(old, args.kind)

    if not args.dry_run:
        data["version"] = new
        data["release_date"] = datetime.date.today().isoformat()
        if args.notes:
            data["notes"] = args.notes
        save_version(data, version_path)
        update_iss(iss_path, new)

    if args.print_only:
        print(new)
    else:
        print("Versao atualizada: {} -> {}".format(old, new))


if __name__ == "__main__":
    main()
