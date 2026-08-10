from __future__ import annotations

from dataclasses import dataclass

FIFA14_RESOURCE_BASE = 0x60000000
FIFA14_VERSION_CONST = 0x02000000
FIFA14_VERSION_STEP = 0x01000000


@dataclass(frozen=True)
class Fifa14CardIdentity:
    asset_id: int
    resource_id: int
    definition_id: int
    version: int


def _checked_asset_id(asset_id: int) -> int:
    value = int(asset_id)
    if value <= 0 or value >= FIFA14_VERSION_STEP // 2:
        raise ValueError(f"invalid FIFA 14 assetId: {asset_id}")
    return value


def _checked_version(version: int) -> int:
    value = int(version)
    if value < 1:
        raise ValueError(f"invalid FIFA 14 card version: {version}")
    return value


def resource_id_for(asset_id: int, version: int = 1) -> int:
    """Return FIFA 14 FUT resourceId, matching fut-calculator v1.0.0."""
    asset = _checked_asset_id(asset_id)
    card_version = _checked_version(version)
    version_offset = 0 if card_version == 1 else (
        FIFA14_VERSION_CONST + FIFA14_VERSION_STEP * (card_version - 1)
    )
    return FIFA14_RESOURCE_BASE + asset + version_offset


def definition_id_for(asset_id: int, version: int = 1) -> int:
    """Return FIFA 14 FUT definitionId for an asset/version pair."""
    return resource_id_for(asset_id, version) - FIFA14_RESOURCE_BASE


def decode_resource_id(resource_id: int) -> Fifa14CardIdentity:
    """Decode a FIFA 14 FUT resourceId using the original calculator rules."""
    resource = int(resource_id)
    tmp = resource - FIFA14_RESOURCE_BASE
    if tmp <= 0:
        raise ValueError(f"invalid FIFA 14 resourceId: {resource_id}")
    # PHP round(x, 0) for positive x is floor(x + .5). FIFA asset IDs are far
    # below half a version step, so this exactly recovers the version bucket.
    times = int(tmp / FIFA14_VERSION_STEP + 0.5)
    asset = tmp - FIFA14_VERSION_STEP * times
    asset = _checked_asset_id(asset)
    version = 1 if times == 0 else times - 1
    if version < 1:
        raise ValueError(f"invalid FIFA 14 version bucket in resourceId: {resource_id}")
    definition = resource - FIFA14_RESOURCE_BASE
    return Fifa14CardIdentity(asset, resource, definition, version)


def validate_identity(asset_id: int, resource_id: int, definition_id: int, version: int = 1) -> None:
    expected_resource = resource_id_for(asset_id, version)
    expected_definition = definition_id_for(asset_id, version)
    if int(resource_id) != expected_resource:
        raise ValueError(
            f"resourceId mismatch for assetId {asset_id} v{version}: "
            f"{resource_id} != {expected_resource}"
        )
    if int(definition_id) != expected_definition:
        raise ValueError(
            f"definitionId mismatch for assetId {asset_id} v{version}: "
            f"{definition_id} != {expected_definition}"
        )
