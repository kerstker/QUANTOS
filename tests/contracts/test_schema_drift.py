"""Guard: committed JSON Schemas under schemas/ must match the pydantic
contracts they are generated from (run `python scripts/gen_schemas.py` to
refresh if this fails)."""
import importlib.util
import os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GEN_PATH = os.path.join(REPO, "scripts", "gen_schemas.py")


def _load_gen():
    spec = importlib.util.spec_from_file_location("quantos_gen_schemas", GEN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_schemas_match_contracts():
    gen = _load_gen()
    rendered = gen.generate(write=False)
    assert rendered, "no schema targets configured"
    for rel, text in rendered.items():
        path = os.path.join(gen.SCHEMAS_DIR, rel)
        assert os.path.exists(path), f"missing committed schema: {rel} (run scripts/gen_schemas.py)"
        with open(path, encoding="utf-8") as fh:
            committed = fh.read()
        assert committed == text, f"stale schema: {rel} (run scripts/gen_schemas.py)"
