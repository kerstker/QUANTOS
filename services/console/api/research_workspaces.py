"""Operator-configured read-only views of separately frozen research state.

Only the current runtime root accepts research mutations. A workspace ID is not
a filesystem path, a running-worker assertion, or permission to resume a study.
"""
import os
from pathlib import Path
import re

from fastapi import HTTPException

from services.governance.json_integrity import load_json_without_duplicate_keys

_CURRENT = {'id': 'current', 'label': '当前研究', 'writable': True}


def resolve_workspace(current_root: Path, workspace: str):
    entries = {}
    configured = os.environ.get('QUANTOS_RESEARCH_WORKSPACES_PATH', '').strip()
    if configured:
        try:
            path = Path(configured)
            if not path.is_absolute() or not path.is_file():
                raise ValueError('absolute registry path required')
            with path.open('rb') as stream:
                raw = stream.read(16385)
            if len(raw) > 16384:
                raise ValueError('registry exceeds bound')
            data = load_json_without_duplicate_keys(raw)
            if (type(data) is not dict or set(data) != {'version', 'workspaces'}
                    or type(data['version']) is not int or data['version'] != 1
                    or type(data['workspaces']) is not list or len(data['workspaces']) > 16):
                raise ValueError('invalid registry')
            for row in data['workspaces']:
                if (type(row) is not dict or set(row) != {'id', 'label', 'root'}
                        or any(type(row[k]) is not str for k in row)
                        or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', row['id'])
                        or row['id'] == 'current' or row['id'] in entries
                        or not 1 <= len(row['label'].strip()) <= 80
                        or any(ord(c) < 32 for c in row['label'])
                        or not 1 <= len(row['root']) <= 4096 or '\x00' in row['root']
                        or not Path(row['root']).is_absolute()):
                    raise ValueError('invalid workspace')
                entries[row['id']] = row
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise HTTPException(503, 'RESEARCH_WORKSPACES_CONFIGURATION_INVALID') from exc
    choices = [dict(_CURRENT)] + [
        {'id': row['id'], 'label': row['label'], 'writable': False} for row in entries.values()
    ]
    if workspace == 'current':
        return current_root, dict(_CURRENT), choices
    if workspace not in entries:
        raise HTTPException(404, 'RESEARCH_WORKSPACE_NOT_FOUND')
    row = entries[workspace]
    try:
        root = Path(row['root']).resolve(strict=True)
        if not root.is_dir() or not (root / 'data/cache/ai_factory/factory.db').is_file():
            raise ValueError('research store unavailable')
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(503, 'RESEARCH_WORKSPACE_UNAVAILABLE') from exc
    return root, {'id': workspace, 'label': row['label'], 'writable': False}, choices
