"""Alpha Asset Registry (Alpha Factory upgrade Phase 3) — versioned, lineage-aware entity store.

Dumb, deterministic storage for `AlphaAsset` snapshots:
  * immutable versions — re-deriving / re-fitting an alpha is a NEW version, never an overwrite;
  * lineage queries    — find an alpha's children (assets whose lineage names it as a parent);
  * provenance filters — by generator / status (over each alpha's latest version);
  * append-only audit   — every add is logged with the caller-supplied timestamp.

Containment: the registry drives NO lifecycle transitions and exposes NO privileged status mutator. An
asset's `status` reflects what the lifecycle manager already decided; storing a snapshot cannot promote
an alpha, so this is not an agent path to APPROVED. Pure-Python stdlib, deterministic (no RNG, no
wall-clock — the caller supplies `ts`).
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.alpha_asset import AlphaAsset
from quantos.contracts.research import AlphaState


class AlphaAssetRegistry:
    def __init__(self) -> None:
        self._versions: dict[str, dict[int, AlphaAsset]] = {}
        self._audit: list[dict] = []

    # --- write ---
    def add(self, asset: AlphaAsset, *, ts: int = 0) -> AlphaAsset:
        """Store an asset snapshot. A new alpha_id registers at its version; an existing alpha_id may
        only ADD a strictly higher version (immutable, monotonic — no overwrite, no downgrade).

        CONTAINMENT: APPROVED is the sacred live state. It may only enter the registry as the snapshot
        of a COMPLETED lifecycle transition — proven by a non-empty `lifecycle_event_ids`, which only
        AlphaLifecycleManager.advance() populates. A directly-constructed APPROVED asset (no transition
        proof) is rejected, so the registry can never be a backdoor around the human-only promotion."""
        self._validate_and_store(asset)
        self._audit.append({"event": "add", "alpha_id": asset.alpha_id, "version": asset.version,
                            "status": asset.status.value, "generator": asset.generator,
                            "owner": asset.owner, "lifecycle_event_ids": list(asset.lifecycle_event_ids),
                            "ts": int(ts)})
        return asset

    def _validate_and_store(self, asset: AlphaAsset) -> None:
        """三条守卫(APPROVED 须 lifecycle_event_ids、version 不可重、version 单调)+ 写 _versions。
        从 add() 抽出供 restore() 复用——**含 containment 不变量,守卫顺序/语义逐字保留**:非人不能经
        registry(内存 add 或 sqlite restore)后门到 APPROVED。"""
        if asset.status == AlphaState.APPROVED and not asset.lifecycle_event_ids:
            raise ValueError(f"alpha {asset.alpha_id}: APPROVED requires lifecycle_event_ids (promotion "
                             "goes through AlphaLifecycleManager, not direct registry.add)")
        vmap = self._versions.get(asset.alpha_id)
        if vmap is None:
            self._versions[asset.alpha_id] = {asset.version: asset}
        else:
            if asset.version in vmap:
                raise ValueError(f"alpha {asset.alpha_id} version {asset.version} already registered (immutable)")
            if asset.version <= max(vmap):
                raise ValueError(f"alpha {asset.alpha_id} version must exceed {max(vmap)}, got {asset.version}")
            vmap[asset.version] = asset

    def restore(self, assets, audit) -> None:
        """从持久层(asset_store.load_registry)重建内存态。fail-closed:逐 asset 过 _validate_and_store
        的同一套守卫(升序;损坏/单调违反/伪造 APPROVED 即 raise,不静默部分加载),再置 _audit。"""
        for asset in assets:
            self._validate_and_store(asset)
        self._audit = list(audit)

    # --- read ---
    def get(self, alpha_id: str, version: Optional[int] = None) -> Optional[AlphaAsset]:
        vmap = self._versions.get(alpha_id)
        if not vmap:
            return None
        return vmap.get(version if version is not None else max(vmap))

    def latest(self, alpha_id: str) -> Optional[AlphaAsset]:
        return self.get(alpha_id)

    def versions(self, alpha_id: str) -> tuple[int, ...]:
        return tuple(sorted(self._versions.get(alpha_id, {})))

    def history(self, alpha_id: str) -> tuple[AlphaAsset, ...]:
        vmap = self._versions.get(alpha_id, {})
        return tuple(vmap[v] for v in sorted(vmap))

    def all_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._versions))

    def all_latest(self) -> tuple[AlphaAsset, ...]:
        return tuple(self.latest(aid) for aid in self.all_ids())

    # --- filters (over each alpha's latest version) ---
    def by_generator(self, generator: str) -> tuple[AlphaAsset, ...]:
        return tuple(a for a in self.all_latest() if a.generator == generator)

    def by_status(self, status: AlphaState) -> tuple[AlphaAsset, ...]:
        return tuple(a for a in self.all_latest() if a.status == status)

    def children_of(self, parent_id: str) -> tuple[AlphaAsset, ...]:
        """Descendants of `parent_id`: any alpha that named it as a parent in ANY version (lineage is
        historical fact, so a later version dropping the link does not erase the genealogy). Returns the
        latest version of each such child, sorted by id."""
        ids = {aid for aid, vmap in self._versions.items()
               if any(parent_id in a.lineage.parent_ids for a in vmap.values())}
        return tuple(self.latest(aid) for aid in sorted(ids))

    # --- audit ---
    def audit_log(self) -> tuple[dict, ...]:
        return tuple(self._audit)
