"""Near-duplicate detection across the estate (MinHash / LSH).

30-40% of enterprise BI estates are near-duplicates; surfacing
"migrate-one, retire-many" clusters is one of the largest effort levers
available (25-50% reduction). Clustering runs over the normalized
feature shingles, so a Tableau workbook copied with a renamed title and
one extra filter still lands in the same cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from datasketch import MinHash, MinHashLSH

from bimigrate.models.inventory import ArtifactInventory

NUM_PERM = 128
DEFAULT_THRESHOLD = 0.8


@dataclass
class DuplicateCluster:
    representative: str  # artifact_path of the suggested "migrate-one"
    members: list[str] = field(default_factory=list)  # paths incl. representative
    similarity_threshold: float = DEFAULT_THRESHOLD

    @property
    def retire_candidates(self) -> list[str]:
        return [m for m in self.members if m != self.representative]


def _minhash_for(inv: ArtifactInventory) -> MinHash:
    mh = MinHash(num_perm=NUM_PERM)
    for shingle in inv.feature_shingles():
        mh.update(shingle.encode("utf-8", errors="replace"))
    return mh


def cluster_inventories(
    inventories: list[ArtifactInventory], threshold: float = DEFAULT_THRESHOLD
) -> list[DuplicateCluster]:
    """Group near-duplicates; only clusters with 2+ members are returned.

    The representative is the member with the highest complexity score —
    migrating the richest variant usually covers the others.
    """
    lsh = MinHashLSH(threshold=threshold, num_perm=NUM_PERM)
    hashes: dict[str, MinHash] = {}
    by_path: dict[str, ArtifactInventory] = {}

    for inv in inventories:
        if not inv.feature_shingles():
            continue
        key = inv.artifact_path
        mh = _minhash_for(inv)
        hashes[key] = mh
        by_path[key] = inv
        lsh.insert(key, mh)

    seen: set[str] = set()
    clusters: list[DuplicateCluster] = []
    for key, mh in hashes.items():
        if key in seen:
            continue
        members = sorted(set(lsh.query(mh)))
        if len(members) < 2:
            seen.add(key)
            continue
        seen.update(members)
        representative = max(members, key=lambda p: by_path[p].complexity_score)
        clusters.append(
            DuplicateCluster(representative=representative, members=members, similarity_threshold=threshold)
        )
    return clusters


def dedup_savings_estimate(
    clusters: list[DuplicateCluster], inventories: list[ArtifactInventory]
) -> dict[str, float]:
    """Effort saved by migrating one representative per cluster."""
    from bimigrate.engine.complexity import effort_hours

    by_path = {inv.artifact_path: inv for inv in inventories}
    total = sum(effort_hours(inv) for inv in inventories)
    saved = sum(effort_hours(by_path[p]) for c in clusters for p in c.retire_candidates if p in by_path)
    return {
        "total_effort_hours": round(total, 1),
        "saved_effort_hours": round(saved, 1),
        "savings_pct": round(100 * saved / total, 1) if total else 0.0,
    }
