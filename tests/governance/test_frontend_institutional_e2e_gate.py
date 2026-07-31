from __future__ import annotations

import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "apps" / "research-console"


def _png_size(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    assert payload[12:16] == b"IHDR"
    return struct.unpack(">II", payload[16:24])


def test_frontend_e2e_dependencies_and_font_are_exactly_pinned() -> None:
    package = json.loads((CONSOLE / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["e2e"] == "playwright test"
    assert package["devDependencies"]["@playwright/test"] == "1.62.1"
    assert package["devDependencies"]["@axe-core/playwright"] == "4.12.1"
    assert package["dependencies"]["@fontsource-variable/noto-sans-sc"] == "5.3.0"

    styles = (CONSOLE / "src" / "index.css").read_text(encoding="utf-8")
    assert "@import '@fontsource-variable/noto-sans-sc';" in styles
    assert '"Noto Sans SC Variable"' in styles


def test_frontend_e2e_contract_is_deterministic_accessible_and_no_overflow() -> None:
    config = (CONSOLE / "playwright.config.ts").read_text(encoding="utf-8")
    spec = (CONSOLE / "e2e" / "paper-cockpit.spec.ts").read_text(encoding="utf-8")

    for marker in (
        "workers: 1",
        "locale: 'en-US'",
        "timezoneId: 'UTC'",
        "viewport: { width: 1440, height: 1000 }",
        "maxDiffPixelRatio: 0.01",
        "{arg}-${process.platform}{ext}",
    ):
        assert marker in config

    for marker in (
        "installFixedApi(page)",
        "document.documentElement.scrollWidth",
        "document.body.scrollWidth",
        "new AxeBuilder({ page })",
        "'wcag2a'",
        "'wcag2aa'",
        "'wcag21a'",
        "'wcag21aa'",
        "toHaveScreenshot('paper-cockpit.png')",
    ):
        assert marker in spec


def test_frontend_visual_contract_has_reviewable_windows_and_linux_baselines() -> None:
    snapshots = CONSOLE / "e2e" / "__snapshots__"
    windows = snapshots / "paper-cockpit-win32.png"
    linux = snapshots / "paper-cockpit-linux.png"

    assert _png_size(windows) == (1440, 1000)
    assert _png_size(linux) == (1440, 1000)
    assert windows.stat().st_size > 100_000
    assert linux.stat().st_size > 100_000


def test_frontend_e2e_is_load_bearing_in_ci_and_top_tier_assessment() -> None:
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )
    for marker in (
        "npx playwright install chromium",
        "npm run e2e",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        "playwright-visual-diff-${{ github.run_id }}",
    ):
        assert marker in workflow

    requirements = json.loads(
        (ROOT / "config" / "top_tier_requirements.json").read_text(encoding="utf-8")
    )
    requirement = next(
        item
        for item in requirements["requirements"]
        if item["id"] == "FRONTEND.ROUTES.SINGLE_SOURCE"
    )
    required_paths = set(requirement["checker_args"]["paths"])
    assert {
        ".github/workflows/validate.yml",
        "apps/research-console/playwright.config.ts",
        "apps/research-console/e2e/paper-cockpit.spec.ts",
        "apps/research-console/e2e/__snapshots__/paper-cockpit-win32.png",
        "apps/research-console/e2e/__snapshots__/paper-cockpit-linux.png",
        "tests/governance/test_frontend_institutional_e2e_gate.py",
    } <= required_paths
    assert "Add E2E accessibility and visual regression" not in requirement["next_action"]
