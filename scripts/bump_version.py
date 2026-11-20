# -*- coding: utf-8 -*-
"""Incrementa a versão menor (x.y.z) para a próxima release.
   Uso: python scripts/bump_version.py [major|minor|patch]
   - major: incrementa o primeiro número e zera os outros
   - minor: incrementa o segundo número e zera o terceiro
   - patch: incrementa o terceiro número (padrão)
"""
import json, sys, re, os

def load_version(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def save_version(data, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def bump(v, kind):
    parts = [int(p) for p in v.split('.')]
    if kind == 'major':
        parts = [parts[0] + 1, 0, 0]
    elif kind == 'minor':
        parts = [parts[0], parts[1] + 1, 0]
    else:  # patch
        parts[2] += 1
    return '.'.join(str(p) for p in parts)

if __name__ == '__main__':
    kind = sys.argv[1] if len(sys.argv) > 1 else 'patch'
    if kind not in ('major', 'minor', 'patch'):
        print('Invalid kind, use major|minor|patch')
        sys.exit(1)
    version_path = os.path.join('G:/Downloads/AutoBIM.extension', 'version.json')
    data = load_version(version_path)
    old = data.get('version')
    new = bump(old, kind)
    data['version'] = new
    save_version(data, version_path)
    # atualizar bundle.yaml
    bundle_path = os.path.join('G:/Downloads/AutoBIM.extension', 'AutoBIM.tab', 'bundle.yaml')
    if os.path.exists(bundle_path):
        with open(bundle_path, encoding='utf-8') as f:
            yaml = f.read()
        yaml = re.sub(r'version:\s*"[^"]+"', f'version: "{new}"', yaml)
        with open(bundle_path, 'w', encoding='utf-8') as f:
            f.write(yaml)
    print(f'Versão atualizada: {old} → {new}')
